#!/usr/bin/env python3
"""Emit data/company_feeds.json: trusted operator status/IR feeds + one Google News RSS per CSV company."""

from __future__ import annotations

import csv
import json
import os
import re
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "data", "dc_companies.csv")
OUT_PATH = os.path.join(ROOT, "data", "company_feeds.json")

# Trusted feeds (topic gate waived). URLs must stay machine-readable Atom/RSS.
OFFICIAL_BLOCKS: list[dict] = [
    {
        "companies": ["Amazon.com, Inc."],
        "urls": ["https://status.aws.amazon.com/rss/all.rss"],
    },
    {
        "companies": ["Microsoft Corporation"],
        "urls": ["https://azure.status.microsoft/status/feed/"],
    },
    {
        "companies": ["Meta Platforms, Inc."],
        "urls": ["https://metastatuspage.com/history.atom"],
    },
    {
        "companies": ["Digital Realty Trust, Inc.", "MC Digital Realty, Inc."],
        "urls": ["https://investor.digitalrealty.com/rss/news-releases.xml"],
    },
    {
        "companies": ["Equinix, Inc."],
        "urls": [
            "https://equinixproductstatus.statuspage.io/history.atom",
            "https://investor.equinix.com/rss/news-releases.xml",
            "https://status.equinixmetal.com/history.atom",
        ],
    },
]


def google_news_feed_url(company: str) -> str:
    """Scoped Google News RSS: company name + DC / outage vocabulary (narrower than web search)."""
    safe = company.replace('"', " ").strip()
    q = (
        f'"{safe}" '
        '("data center" OR datacenter OR colocation OR colo OR hyperscale OR outage OR downtime OR '
        "incident OR disruption OR degraded OR unavailable OR failure OR fire OR power OR cooling OR "
        "blackout OR flood OR evacuation OR status OR maintenance OR fiber OR network)"
    )
    return (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    )


def load_companies(path: str) -> list[str]:
    names: list[str] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        col = reader.fieldnames[0] if reader.fieldnames else "Company"
        for row in reader:
            raw = (row.get("Company") or row.get(col) or "").strip()
            if raw:
                names.append(raw)
    return names


def main() -> None:
    companies = load_companies(CSV_PATH)
    feeds: list[dict] = []
    feeds.extend(OFFICIAL_BLOCKS)
    for name in companies:
        feeds.append(
            {
                "companies": [name],
                "urls": [google_news_feed_url(name)],
                "official_only": False,
            }
        )
    doc = {
        "_comment": (
            "official_only omitted or true: status/IR — topic keyword gate waived. "
            "official_only false: Google News RSS scoped per CSV company — topic gate uses "
            "company name match in title/summary (see monitor.py)."
        ),
        "feeds": feeds,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Wrote {OUT_PATH} with {len(OFFICIAL_BLOCKS)} official block(s) and {len(companies)} company Google feed(s).")


if __name__ == "__main__":
    main()
