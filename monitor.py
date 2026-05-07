#!/usr/bin/env python3
"""Fetch RSS (and optionally NewsAPI) articles, filter by keywords, dedupe, write Markdown, post Slack."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

import feedparser
import requests

REQUEST_TIMEOUT = 25
NEWSAPI_URL = "https://newsapi.org/v2/everything"
SLACK_TEXT_MAX = 3500
SLACK_BULLET_CAP = 12


def _truthy(val: str | None) -> bool:
    if not val:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def _split_csv(val: str | None) -> list[str]:
    if not val or not val.strip():
        return []
    return [p.strip() for p in val.split(",") if p.strip()]


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        netloc = parts.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parts.path or "/"
        path = re.sub(r"/+$", "", path) if path != "/" else path
        normalized = urllib.parse.urlunsplit(
            (parts.scheme.lower(), netloc, path, "", "")
        )
        return normalized
    except Exception:
        return url.strip()


def first_match_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    """Return list of keyword phrases that appear in text (case-insensitive)."""
    if not text:
        return []
    lower = text.lower()
    matched: list[str] = []
    for kw in keywords:
        if kw.lower() in lower:
            matched.append(kw)
    return matched


def rss_published(entry: feedparser.FeedParserDict) -> str | None:
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            return str(raw)
    if entry.get("published_parsed"):
        try:
            t = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return t.isoformat()
        except (TypeError, ValueError):
            pass
    return None


@dataclass
class Article:
    title: str
    url: str
    summary: str
    source: str
    origin: str
    published: str | None = None
    matched_keywords: list[str] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        n = normalize_url(self.url)
        if n:
            return n
        base = f"{self.title.lower().strip()}|{self.source.lower()}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def fetch_feed(url: str) -> list[Article]:
    out: list[Article] = []
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "dc-news-scraper/1.0 (+monitor)"},
        )
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except requests.RequestException as exc:
        print(f"[rss] skip feed {url!r}: {exc}", file=sys.stderr)
        return out

    feed_title = ""
    if parsed.feed and parsed.feed.get("title"):
        feed_title = str(parsed.feed["title"])

    for entry in parsed.entries or []:
        link = entry.get("link") or entry.get("id")
        if not link:
            continue
        title = str(entry.get("title") or "").strip() or "(no title)"
        summary = (
            entry.get("summary")
            or entry.get("description")
            or entry.get("subtitle")
            or ""
        )
        summary = str(summary).strip()
        out.append(
            Article(
                title=title,
                url=str(link).strip(),
                summary=summary,
                source=feed_title or urllib.parse.urlsplit(url).netloc,
                origin="rss",
                published=rss_published(entry),
            )
        )
    return out


def fetch_newsapi(
    api_key: str,
    query: str,
    lookback_hours: int,
    page_size: int,
) -> list[Article]:
    out: list[Article] = []
    if not query.strip():
        return out

    from_dt = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    params = {
        "q": query,
        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": min(max(1, page_size), 100),
    }
    try:
        r = requests.get(
            NEWSAPI_URL,
            params=params,
            headers={
                "X-Api-Key": api_key,
                "User-Agent": "dc-news-scraper/1.0 (+monitor)",
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
    except requests.RequestException as exc:
        print(f"[newsapi] request failed: {exc}", file=sys.stderr)
        return out

    if payload.get("status") != "ok":
        print(f"[newsapi] bad response: {payload}", file=sys.stderr)
        return out

    for art in payload.get("articles") or []:
        u = art.get("url")
        if not u:
            continue
        title = str(art.get("title") or "").strip() or "(no title)"
        summary = str(art.get("description") or "").strip()
        src = (art.get("source") or {}).get("name") or "newsapi"
        pub = art.get("publishedAt")
        out.append(
            Article(
                title=title,
                url=str(u).strip(),
                summary=summary,
                source=str(src),
                origin="newsapi",
                published=str(pub) if pub else None,
            )
        )
    return out


def filter_articles(
    articles: list[Article],
    keywords: list[str],
) -> list[Article]:
    matched: list[Article] = []
    for a in articles:
        blob = f"{a.title}\n{a.summary}"
        hits = first_match_keywords(blob, keywords)
        if not hits:
            continue
        a.matched_keywords = hits
        matched.append(a)
    return matched


def dedupe(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    uniq: list[Article] = []
    for a in articles:
        k = a.dedupe_key
        if k in seen:
            continue
        seen.add(k)
        uniq.append(a)
    return uniq


def write_markdown(
    path: str,
    date_str: str,
    articles: list[Article],
    *,
    scanned_total: int | None = None,
) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = [
        f"# Data center / downtime digest — {date_str}",
        "",
        f"_Generated at {datetime.now(timezone.utc).isoformat()}_",
        "",
        f"**{len(articles)}** matching articles.",
        "",
    ]
    if scanned_total is not None:
        lines.append(f"_**Scanned** {scanned_total} article(s) from RSS/NewsAPI before keyword filter._")
        lines.append("")
    for a in articles:
        kw = ", ".join(a.matched_keywords)
        pub = f" — _{a.published}_" if a.published else ""
        lines.extend(
            [
                f"## {a.title}",
                "",
                f"- **Link:** {a.url}",
                f"- **Source:** {a.source} ({a.origin}){pub}",
                f"- **Matched:** {kw}",
                "",
            ]
        )
        if a.summary:
            lines.append(a.summary[:2000] + ("…" if len(a.summary) > 2000 else ""))
            lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_slack_text(
    date_str: str,
    articles: list[Article],
    *,
    scanned_total: int | None = None,
) -> str:
    header = f"*Daily data center / downtime digest* — {date_str}\n"
    if not articles:
        body = "_No keyword matches today._\n"
        if scanned_total is not None:
            if scanned_total == 0:
                body += (
                    "_No articles were fetched — check `FEED_URLS` (and NewsAPI key/query if used)._"
                )
            else:
                body += (
                    f"_Scanned {scanned_total} article(s); none matched your `KEYWORDS` "
                    "(title/summary substring match, case-insensitive)._\n"
                    "_Try broader phrases, e.g. `disruption`, `degradation`, "
                    "`unavailable`, `error`, `failure`, `region`, `datacenter`._"
                )
        else:
            body += "_Try broadening `KEYWORDS` in repo Variables._"
        return header + body

    lines = [header, f"_{len(articles)} article(s). Showing up to {SLACK_BULLET_CAP}._", ""]
    for a in articles[:SLACK_BULLET_CAP]:
        kw = ", ".join(a.matched_keywords[:3])
        if len(a.matched_keywords) > 3:
            kw += ", …"
        lines.append(f"• *{a.title[:120]}{'…' if len(a.title) > 120 else ''}*\n  {a.url}\n  _matched: {kw}_")

    if len(articles) > SLACK_BULLET_CAP:
        lines.append(
            f"\n_…and {len(articles) - SLACK_BULLET_CAP} more — see full `reports/daily_report_*.md` in the GitHub Actions artifact._"
        )

    text = "\n".join(lines)
    if len(text) > SLACK_TEXT_MAX:
        text = text[: SLACK_TEXT_MAX - 20] + "\n_…(truncated)_"
    return text


def post_slack(webhook: str, text: str) -> None:
    r = requests.post(
        webhook,
        data=json.dumps({"text": text}),
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()


def main() -> int:
    feed_urls = _split_csv(os.environ.get("FEED_URLS"))
    keywords = _split_csv(os.environ.get("KEYWORDS"))
    newsapi_key = (os.environ.get("NEWSAPI_KEY") or "").strip()
    newsapi_query = (os.environ.get("NEWSAPI_QUERY") or "").strip()
    slack_url = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()

    try:
        lookback = int(os.environ.get("LOOKBACK_HOURS") or "48")
    except ValueError:
        lookback = 48
    try:
        page_size = int(os.environ.get("NEWSAPI_PAGE_SIZE") or "40")
    except ValueError:
        page_size = 40

    require_slack = _truthy(os.environ.get("REQUIRE_SLACK_WEBHOOK"))

    if not keywords:
        print("KEYWORDS is empty; nothing can match.", file=sys.stderr)
        return 1

    if require_slack and not slack_url:
        print("REQUIRE_SLACK_WEBHOOK is set but SLACK_WEBHOOK_URL is missing.", file=sys.stderr)
        return 1

    all_articles: list[Article] = []
    rss_ok = 0
    for u in feed_urls:
        items = fetch_feed(u)
        if items:
            rss_ok += 1
        all_articles.extend(items)

    if feed_urls and rss_ok == 0:
        print("[rss] warning: no entries parsed from any feed URL", file=sys.stderr)

    if newsapi_key and newsapi_query:
        all_articles.extend(fetch_newsapi(newsapi_key, newsapi_query, lookback, page_size))
    elif newsapi_key and not newsapi_query:
        print("[newsapi] NEWSAPI_KEY set but NEWSAPI_QUERY empty; skipping NewsAPI.", file=sys.stderr)

    scanned_before_keywords = len(all_articles)
    filtered = filter_articles(all_articles, keywords)
    final = dedupe(filtered)

    today = datetime.now(timezone.utc).date().isoformat()
    report_path = os.path.join("reports", f"daily_report_{today}.md")
    write_markdown(
        report_path,
        today,
        final,
        scanned_total=scanned_before_keywords,
    )
    print(
        f"Wrote {report_path} ({len(final)} matches from {scanned_before_keywords} scanned articles)",
    )

    slack_body = build_slack_text(today, final, scanned_total=scanned_before_keywords)
    if slack_url:
        try:
            post_slack(slack_url, slack_body)
            print("Posted to Slack.")
        except requests.RequestException as exc:
            print(f"Slack post failed: {exc}", file=sys.stderr)
            return 1
    else:
        print("SLACK_WEBHOOK_URL not set; skipped Slack post.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
