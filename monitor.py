#!/usr/bin/env python3
"""Load operator RSS from ``data/company_feeds.json`` (trusted status/IR + optional per-company Google News),
filter by keywords / company-scope / outage phrases, dedupe, write Markdown, optionally post Slack.
NewsAPI is not supported."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

import feedparser
import requests

REQUEST_TIMEOUT = 35
SLACK_TEXT_MAX = 3500
SLACK_BULLET_CAP = 12

# Single-token topic terms that must substring-match (e.g. inside "colocation"), not \b…\b.
_SUBSTRING_WORD_TOKENS = frozenset({"colo"})

# Single-token phrases that prefix-match longer words (evacuat → evacuation).
_PREFIX_WORD_TOKENS = frozenset({"evacuat"})


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
        normalized = urllib.parse.urlunsplit((parts.scheme.lower(), netloc, path, "", ""))
        return normalized
    except Exception:
        return url.strip()


def phrase_matches(text: str, phrase: str) -> bool:
    """Match a keyword or signal phrase with fewer substring false positives.

    - Multi-word phrases: case-insensitive substring.
    - Very short ALL-CAPS ASCII tokens (e.g. AWS, GCP, NTT): whole-token match.
    - ``colo``: substring so it still hits "colocation".
    - ``evacuat``: word-start prefix so it hits "evacuation" / "evacuated".
    - Other single-token words: ``\\bphrase\\b`` (e.g. ``fire`` vs ``bonfire``, ``azure`` vs accidents).
    """
    if not text or not phrase or not phrase.strip():
        return False
    raw = phrase.strip()
    if " " in raw:
        return raw.lower() in text.lower()

    key = raw.lower()
    lower = text.lower()
    if key in _SUBSTRING_WORD_TOKENS:
        return key in lower
    if key in _PREFIX_WORD_TOKENS:
        return bool(re.search(rf"\b{re.escape(key)}", lower, re.I))

    if raw.isascii() and raw.isalpha() and raw.isupper() and len(raw) <= 6:
        return bool(re.search(rf"\b{re.escape(raw)}\b", text, re.I))

    if len(key) <= 32 and re.fullmatch(r"[a-z][a-z\-]*", key, re.I):
        return bool(re.search(rf"\b{re.escape(key)}\b", lower, re.I))

    return key in lower


def first_match_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    """Return list of keyword phrases that match in text."""
    if not text:
        return []
    matched: list[str] = []
    for kw in keywords:
        if phrase_matches(text, kw):
            matched.append(kw)
    return matched


def host_matches_topic_relax(url: str, relax_hosts: Iterable[str]) -> bool:
    """True if hostname equals or ends with one of relax_hosts (e.g. datacenterdynamics.com)."""
    if not url or not relax_hosts:
        return False
    try:
        hostname = (urllib.parse.urlsplit(url.strip()).hostname or "").lower()
    except Exception:
        return False
    if not hostname:
        return False
    for raw in relax_hosts:
        suf = raw.strip().lower().lstrip(".")
        if not suf:
            continue
        if hostname == suf or hostname.endswith("." + suf):
            return True
    return False


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


_LEGAL_SUFFIX = re.compile(
    r",?\s*(Inc\.?|LLC|Ltd\.?|Limited|Corporation|Corp\.?|AG|S\.A\.|B\.V\.|plc|PLC|"
    r"Pte\.?\s*Ltd\.?|LLP|LP|Trust|REIT|Co\.,?\s*Ltd\.?|Group|Holdings)\.?\s*$",
    re.IGNORECASE,
)


def hint_fragments(company_label: str) -> list[str]:
    """Short phrases derived from a CSV company label for loose headline matching."""
    raw = company_label.strip()
    out: list[str] = []
    if raw:
        out.append(raw)
    if " - " in raw:
        left = raw.split(" - ")[0].strip()
        if left:
            out.append(left)
    low = raw.lower()
    if "alphabet" in low:
        out.extend(["Google", "Google Cloud", "Alphabet"])
    if "meta platforms" in low:
        out.extend(["Meta", "Facebook", "Instagram", "WhatsApp"])
    if "amazon" in low and "com" in low:
        out.extend(["Amazon", "AWS"])
    if "tencent" in low:
        out.extend(["Tencent", "腾讯"])
    if "baidu" in low:
        out.append("Baidu")
    if "apple" in low and "inc" in low:
        out.append("Apple")
    base = _LEGAL_SUFFIX.sub("", raw).strip(" ,")
    if base and base not in out:
        out.append(base)
    base2 = _LEGAL_SUFFIX.sub("", base).strip(" ,")
    if base2 and base2 not in out:
        out.append(base2)
    first = re.split(r"[\s,]+", base2 or base or raw)[0]
    if len(first) >= 4 and first not in out:
        out.append(first)
    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        item = item.strip()
        if len(item) < 3 or item in seen:
            continue
        seen.add(item)
        uniq.append(item)
    return uniq


def blob_matches_company_scope(blob: str, companies: list[str]) -> bool:
    """True when title+summary plausibly references one of the feed's operator labels (Google News rows)."""
    if not blob or not companies:
        return False
    blob_lower = blob.lower()
    for label in companies:
        for frag in hint_fragments(label):
            fl = frag.lower()
            if len(frag) <= 6:
                if re.search(rf"\b{re.escape(fl)}\b", blob_lower):
                    return True
            elif fl in blob_lower:
                return True
    return False


@dataclass
class Article:
    title: str
    url: str
    summary: str
    source: str
    origin: str
    feed_url: str | None = None
    linked_companies: list[str] = field(default_factory=list)
    published: str | None = None
    matched_keywords: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        n = normalize_url(self.url)
        if n:
            return n
        base = f"{self.title.lower().strip()}|{self.source.lower()}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def fetch_feed(feed_url: str) -> list[Article]:
    out: list[Article] = []
    try:
        resp = requests.get(
            feed_url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "dc-news-scraper/1.0 (+monitor)"},
        )
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except requests.RequestException as exc:
        print(f"[rss] skip feed {feed_url!r}: {exc}", file=sys.stderr)
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
            entry.get("summary") or entry.get("description") or entry.get("subtitle") or ""
        )
        summary = str(summary).strip()
        out.append(
            Article(
                title=title,
                url=str(link).strip(),
                summary=summary,
                source=feed_title or urllib.parse.urlsplit(feed_url).netloc,
                origin="rss",
                feed_url=feed_url,
                published=rss_published(entry),
            )
        )
    return out


def filter_articles(
    articles: list[Article],
    keywords: list[str],
    outage_signals: list[str],
    topic_relax_hosts: list[str],
    *,
    trusted_feed_urls: frozenset[str],
    hint_feed_urls: frozenset[str],
) -> tuple[list[Article], int]:
    """Keep articles passing topic rules and outage_signals (when configured).

    - ``trusted_feed_urls``: status / IR manifest rows (``official_only`` not false) skip KEYWORDS.
    - ``hint_feed_urls``: per-company Google News RSS (``official_only: false``) — require
      a company-label match in title/summary via ``blob_matches_company_scope``.
    - ``FEED_URLS`` (env) feeds are neither trusted nor hint-gated; they always need KEYWORDS.
    - Optional ``TOPIC_RELAX_HOSTS``: legacy trade-press shortcut when ``OUTAGE_SIGNALS`` set.
    """
    matched: list[Article] = []
    dropped_signal_gate = 0

    for art in articles:
        blob = f"{art.title}\n{art.summary}"

        from_trusted = bool(art.feed_url and art.feed_url in trusted_feed_urls)
        from_hint = bool(art.feed_url and art.feed_url in hint_feed_urls)
        hint_ok = bool(from_hint and blob_matches_company_scope(blob, art.linked_companies))
        relaxed = bool(
            outage_signals and topic_relax_hosts and host_matches_topic_relax(art.url, topic_relax_hosts)
        )
        hits = first_match_keywords(blob, keywords)

        topic_ok = bool(hits) or relaxed or from_trusted or hint_ok
        if not topic_ok:
            continue

        if outage_signals:
            sigs = first_match_keywords(blob, outage_signals)
            if not sigs:
                dropped_signal_gate += 1
                continue
            art.matched_signals = sigs
        else:
            art.matched_signals = []

        if hits:
            art.matched_keywords = hits
        elif relaxed:
            art.matched_keywords = ["(trusted DC-news host, topic gate relaxed)"]
        elif from_trusted:
            art.matched_keywords = ["(official status / IR feed — topic gate waived)"]
        elif hint_ok:
            art.matched_keywords = ["(per-company Google News RSS — operator name matched in text)"]
        else:
            art.matched_keywords = []
        matched.append(art)

    return matched, dropped_signal_gate


def dedupe(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    uniq: list[Article] = []
    for art in articles:
        k = art.dedupe_key
        if k in seen:
            continue
        seen.add(k)
        uniq.append(art)
    return uniq


def load_companies_csv(path: str) -> list[str]:
    names: list[str] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        column = reader.fieldnames[0] if reader.fieldnames else "Company"
        for row in reader:
            raw = (row.get("Company") or row.get(column) or "").strip()
            if raw:
                names.append(raw)
    return names


def load_company_feed_manifest(
    json_path: str,
) -> tuple[list[str], dict[str, list[str]], frozenset[str], frozenset[str]]:
    """Return feed URL order, URL→labels, trusted URL set, and hint-scoped URL set."""
    with open(json_path, encoding="utf-8") as handle:
        root = json.load(handle)
    blocks = root.get("feeds") or []
    urls_out: list[str] = []
    url_to_cos: dict[str, list[str]] = {}
    trusted: set[str] = set()
    hint: set[str] = set()
    for block in blocks:
        companies_raw = block.get("companies") or []
        if isinstance(companies_raw, str):
            companies = [companies_raw]
        else:
            companies = [str(x).strip() for x in companies_raw if str(x).strip()]
        official_only = block.get("official_only", True)
        is_trusted = official_only is not False
        for raw_url in block.get("urls") or []:
            u = str(raw_url).strip()
            if not u:
                continue
            bucket = url_to_cos.setdefault(u, [])
            for label in companies:
                if label and label not in bucket:
                    bucket.append(label)
            if u not in urls_out:
                urls_out.append(u)
            if is_trusted:
                trusted.add(u)
                hint.discard(u)
            elif u not in trusted:
                hint.add(u)
    return urls_out, url_to_cos, frozenset(trusted), frozenset(hint)


def write_markdown(
    path: str,
    date_str: str,
    articles: list[Article],
    *,
    scanned_total: int | None = None,
    filter_note: str | None = None,
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
        lines.append(f"_**Scanned** {scanned_total} article(s) from curated & extra RSS feeds before filters._")
        lines.append("")
    if filter_note:
        lines.append(filter_note)
        lines.append("")
    for art in articles:
        kw = ", ".join(art.matched_keywords) if art.matched_keywords else "(none)"
        sig = ""
        if art.matched_signals:
            sig = f"\n- **Outage signal:** {', '.join(art.matched_signals)}"
        pub = f" — _{art.published}_" if art.published else ""
        cos = ""
        if art.linked_companies:
            lc = "; ".join(art.linked_companies[:8])
            if len(art.linked_companies) > 8:
                lc += ", …"
            cos = f"\n- **Linked operators (manifest):** {lc}"
        lines.extend(
            [
                f"## {art.title}",
                "",
                f"- **Link:** {art.url}",
                f"- **Source:** {art.source} ({art.origin}){pub}",
                f"- **Matched keywords:** {kw}{sig}{cos}",
                "",
            ]
        )
        if art.summary:
            snippet = art.summary[:2000] + ("…" if len(art.summary) > 2000 else "")
            lines.append(snippet)
            lines.append("")
    with open(path, "w", encoding="utf-8") as digest:
        digest.write("\n".join(lines))


def build_slack_text(
    date_str: str,
    articles: list[Article],
    *,
    scanned_total: int | None = None,
    dropped_by_signal: int = 0,
) -> str:
    header = f"*Daily data center / downtime digest* — {date_str}\n"
    if not articles:
        body = "_No downtime-focused matches today._\n"
        if scanned_total is not None:
            if scanned_total == 0:
                body += (
                    "_No articles were fetched — add working RSS URLs to ``data/company_feeds.json`` "
                    "(or extend ``FEED_URLS`` for auxiliary feeds)._"
                )
            elif dropped_by_signal > 0:
                body += (
                    f"_{dropped_by_signal} article(s) cleared the topic gate but failed "
                    "``OUTAGE_SIGNALS`` (title/summary must include outage-related wording)._"
                )
            else:
                body += (
                    f"_Scanned {scanned_total} article(s); none matched keywords / curated-feed rules "
                    "(and ``OUTAGE_SIGNALS``, if configured)._"
                )
        else:
            body += "_Configure ``KEYWORDS`` / ``company_feeds`` per README._"
        return header + body

    lines = [header, f"_{len(articles)} article(s). Showing up to {SLACK_BULLET_CAP}._", ""]
    for art in articles[:SLACK_BULLET_CAP]:
        kw = ", ".join(art.matched_keywords[:3])
        if len(art.matched_keywords) > 3:
            kw += ", …"
        sig_note = ""
        if art.matched_signals:
            s = ", ".join(art.matched_signals[:2])
            if len(art.matched_signals) > 2:
                s += ", …"
            sig_note = f"\n  _signal: {s}_"
        lines.append(
            f"• *{art.title[:120]}{'…' if len(art.title) > 120 else ''}*\n  {art.url}\n  "
            f"_keywords: {kw}_{sig_note}"
        )

    if len(articles) > SLACK_BULLET_CAP:
        lines.append(
            "\n_…and more — see reports/daily_report_*.md in the GitHub Actions artifact._"
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


def summarize_coverage(registry: list[str], manifest_companies: set[str]) -> tuple[list[str], int]:
    """Return (sorted CSV labels still lacking manifest coverage, count of labels matched)."""
    unique = sorted({name.strip() for name in registry if name and name.strip()})
    missing_labels = [name for name in unique if name not in manifest_companies]
    matched_cnt = sum(1 for name in unique if name in manifest_companies)
    return missing_labels, matched_cnt


def main() -> int:
    companies_csv = (os.environ.get("COMPANIES_CSV") or "").strip() or "data/dc_companies.csv"
    company_feed_json = (os.environ.get("COMPANY_FEEDS_JSON") or "").strip() or "data/company_feeds.json"
    keywords = _split_csv(os.environ.get("KEYWORDS"))
    outage_signals = _split_csv(os.environ.get("OUTAGE_SIGNALS"))
    topic_relax_hosts = _split_csv(os.environ.get("TOPIC_RELAX_HOSTS"))

    slack_url = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
    require_slack = _truthy(os.environ.get("REQUIRE_SLACK_WEBHOOK"))
    manifest_urls: list[str] = []
    url_company_map: dict[str, list[str]] = {}
    trusted_feed_urls = frozenset()
    hint_feed_urls = frozenset()

    registry: list[str] = []
    if os.path.isfile(companies_csv):
        registry = load_companies_csv(companies_csv)
    else:
        print(f"[data] Companies CSV missing at {companies_csv!r} — coverage warnings disabled.", file=sys.stderr)

    if os.path.isfile(company_feed_json):
        manifest_urls, url_company_map, trusted_feed_urls, hint_feed_urls = load_company_feed_manifest(
            company_feed_json
        )
    else:
        print(f"[feed-manifest] Missing {company_feed_json!r} — no manifest feeds.", file=sys.stderr)

    extra_feed_urls = _split_csv(os.environ.get("FEED_URLS"))
    combined_feed_urls = list(dict.fromkeys([*manifest_urls, *extra_feed_urls]))

    if not combined_feed_urls:
        print(
            "No RSS/Atom URLs configured — populate data/company_feeds.json "
            "(or set FEED_URLS to auxiliary feeds plus KEYWORDS).",
            file=sys.stderr,
        )
        return 1

    if (
        require_slack and not slack_url
    ):
        print("REQUIRE_SLACK_WEBHOOK is set but SLACK_WEBHOOK_URL is missing.", file=sys.stderr)
        return 1

    if not keywords and not manifest_urls:
        print(
            "KEYWORDS may not be empty unless company_feed manifest lists at least one feed URL.",
            file=sys.stderr,
        )
        return 1

    if extra_feed_urls and not keywords:
        print(
            "[feeds] FEED_URLS is set while KEYWORDS is empty — supplementary feeds demand KEYWORDS.",
            file=sys.stderr,
        )
        return 1

    manifest_company_labels: set[str] = set()
    for comps in url_company_map.values():
        manifest_company_labels.update(comps)

    if registry:
        missing, matched_cnt = summarize_coverage(registry, manifest_company_labels)
        print(
            f"[coverage] Manifest covers {matched_cnt}/{len(registry)} CSV labels "
            f"({len(missing)} lacking dedicated feeds)."
        )
        preview = missing[:35]
        if preview:
            print("[coverage] Add RSS/Atom for sample gaps such as:")
            for name in preview:
                print(f"  - {name}")
            if len(missing) > len(preview):
                print(f"  …and {len(missing) - len(preview)} more")
        else:
            print("[coverage] All CSV company labels appear in the manifest.")

    collected: list[Article] = []
    rss_feeds_ok = 0
    for feed_url in combined_feed_urls:
        items = fetch_feed(feed_url)
        if items:
            rss_feeds_ok += 1
        linked = url_company_map.get(feed_url, [])
        for article in items:
            article.linked_companies = linked
        collected.extend(items)

    if rss_feeds_ok == 0:
        print(
            "[rss] warning: no entries parsed — verify feeds emit machine-readable RSS/Atom (not SPA HTML shells).",
            file=sys.stderr,
        )

    if topic_relax_hosts and outage_signals:
        print(f"Topic relaxation active for hosts: {', '.join(topic_relax_hosts)}", file=sys.stderr)

    scanned_before_keywords = len(collected)

    filtered, dropped_by_signal = filter_articles(
        collected,
        keywords,
        outage_signals,
        topic_relax_hosts,
        trusted_feed_urls=trusted_feed_urls,
        hint_feed_urls=hint_feed_urls,
    )
    final = dedupe(filtered)

    if outage_signals:
        print(
            f"Outage filter: {len(final)} kept, {dropped_by_signal} dropped "
            "(topic OK, no outage signal)",
        )

    today = datetime.now(timezone.utc).date().isoformat()
    report_path = os.path.join("reports", f"daily_report_{today}.md")
    note_segments: list[str] = []

    roster = "; ".join(sorted(manifest_company_labels)[:40])
    if roster:
        if len(sorted(manifest_company_labels)) > 40:
            roster += ", …"
        note_segments.append(f"_Curated-operator manifest companies (partial list): **{roster}**._")

    if outage_signals:
        preview = ", ".join(outage_signals[:12])
        if len(outage_signals) > 12:
            preview += ", …"
        note_segments.append(
            f"_**Outage gate:** each article must also match at least one of: {preview}_"
        )
        if topic_relax_hosts:
            relaxed_hosts_fmt = ", ".join(topic_relax_hosts)
            note_segments.append(
                f"_**Topic relax ({relaxed_hosts_fmt}):** those hosts bypass ``KEYWORDS`` only when outage "
                f"signals match (legacy hook — prefer manifest feeds over broad news)._"
            )

    note_segments.append(
        "_**Google News rows:** ``official_only: false`` feeds require the operator name in the "
        "title/summary plus your ``OUTAGE_SIGNALS`` (when set)._"
    )

    note_segments.append("_NewsAPI disabled — RSS / Google News RSS only._")

    filter_note = "\n\n".join(note_segments) if note_segments else None

    write_markdown(
        report_path,
        today,
        final,
        scanned_total=scanned_before_keywords,
        filter_note=filter_note,
    )
    print(f"Wrote {report_path} ({len(final)} matches from {scanned_before_keywords} scanned articles)")

    slack_payload = build_slack_text(
        today,
        final,
        scanned_total=scanned_before_keywords,
        dropped_by_signal=dropped_by_signal,
    )
    if slack_url:
        try:
            post_slack(slack_url, slack_payload)
            print("Posted to Slack.")
        except requests.RequestException as exc:
            print(f"Slack post failed: {exc}", file=sys.stderr)
            return 1
    else:
        print("SLACK_WEBHOOK_URL not set; skipped Slack post.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
