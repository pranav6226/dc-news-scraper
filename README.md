# Data center downtime monitor (CSV-scoped feeds)

Daily job: read [`data/dc_companies.csv`](data/dc_companies.csv), rebuild [`data/company_feeds.json`](data/company_feeds.json) in CI (and locally when you run the generator), fetch every RSS/Atom endpoint, apply outage wording filters, dedupe, write Markdown under [`reports/`](reports/), then post Slack via an incoming webhook.

**NewsAPI is unsupported.** For operators without a stable status/IR Atom feed, the manifest uses **Google News RSS** scoped to each legal entity plus DC / outage vocabulary; those hits still require **operator-name matching** in the headline or summary (and `OUTAGE_SIGNALS` when set).

## Regenerate the manifest

Whenever you edit the company CSV:

```bash
python scripts/generate_company_feeds.py
```

This emits one **trusted** block per hyperscale/colo status or IR feed we ship in code, plus one **`official_only: false`** Google News block **per CSV row** (161 operators). GitHub Actions runs the same command before `monitor.py`.

## Pipeline behavior

| Input | Behavior |
| --- | --- |
| `data/company_feeds.json` | Feed manifest. **`official_only` omitted or `true`**: status / IR — **KEYWORDS waived**, still gated by `OUTAGE_SIGNALS` when set. **`official_only: false`**: Google News per company — **KEYWORDS waived** only after a **company / brand hint** matches in title+summary (see `hint_fragments` in [`monitor.py`](monitor.py)). |
| `KEYWORDS` | Optional. Required for any `FEED_URLS` you add via env. May be empty when the manifest alone supplies all feeds. |
| `OUTAGE_SIGNALS` | Strongly recommended. Whole-word / substring rules unchanged in [`monitor.py`](monitor.py). |
| `TOPIC_RELAX_HOSTS` | Optional legacy trade-press shortcut (skips `KEYWORDS` when outage wording matches). |

## Coverage

The runner prints `Manifest covers X/Y CSV labels`. After regeneration, **X should equal Y** (every CSV label appears in at least one manifest `companies` array). If a Google feed stops returning parseable RSS, fix the query in [`scripts/generate_company_feeds.py`](scripts/generate_company_feeds.py) or add a dedicated Atom URL to `OFFICIAL_BLOCKS` there.

## GitHub Actions

| Secret | Notes |
| --- | --- |
| `SLACK_WEBHOOK_URL` | Required with default `REQUIRE_SLACK_WEBHOOK=true`. |

Optional repository **variables**: `FEED_URLS`, `KEYWORDS`, `OUTAGE_SIGNALS`, `TOPIC_RELAX_HOSTS`, `COMPANIES_CSV`, `COMPANY_FEEDS_JSON` — see [.env.example](.env.example).

Workflow: install deps → **`python scripts/generate_company_feeds.py`** → **`python monitor.py`** → upload `reports/daily_report_*.md`.

## Local run

```bash
cd dc-news-scraper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a && source .env && set +a
python scripts/generate_company_feeds.py   # after CSV edits
python monitor.py
```

**Runtime:** ~150+ HTTP fetches per run (official + one Google feed per company). Default request timeout is 35s per feed.

## Outputs

- Markdown digest — `reports/daily_report_<UTC-date>.md` with linked operator labels from the manifest.
- Slack — plaintext summary capped for Slack limits.

## Custom spreadsheet

Replace [`data/dc_companies.csv`](data/dc_companies.csv) (single `Company` column), then run `python scripts/generate_company_feeds.py`. To add more **trusted** Atom/RSS URLs, edit `OFFICIAL_BLOCKS` in [`scripts/generate_company_feeds.py`](scripts/generate_company_feeds.py) and regenerate.
