# Data center downtime news monitor

A small daily pipeline: pull articles from RSS feeds (and optionally [NewsAPI](https://newsapi.org/)), filter for **downtime-related** content, deduplicate by URL, write a Markdown digest under `reports/`, and post a short summary to Slack via an [incoming webhook](https://api.slack.com/messaging/webhooks).

**Filtering:** each article must match at least one **`KEYWORDS`** phrase *and* (if you set it) at least one **`OUTAGE_SIGNALS`** phrase — title or summary substring match (case-insensitive). Use **`TOPIC_RELAX_HOSTS`** so sites like **[Data Centre Dynamics](https://www.datacenterdynamics.com/)** can contribute facility/colo outage headlines that never mention hyperscaler keywords: relaxation applies **only** when **`OUTAGE_SIGNALS` is non-empty** (those sites still must show outage/problem language).

Use **`KEYWORDS`** for *topic* (multi-cloud operators, “data center”, colocation brands). **`OUTAGE_SIGNALS`** catches *problem* language (fires, blackout, degraded service, …) so Slack is not flooded with generic DC announcements.

## Quick setup

1. **Create a Slack incoming webhook** for the channel where you want alerts. Copy the webhook URL.

2. **Create a GitHub repository** and push this project (or use an existing repo).

3. **GitHub repository secrets** (Settings → Secrets and variables → Actions):

   | Name | Required | Description |
   |------|----------|-------------|
   | `SLACK_WEBHOOK_URL` | Yes (for Slack posts) | Incoming webhook URL |
   | `NEWSAPI_KEY` | No | Enables NewsAPI discovery when set |

4. **GitHub repository variables** (same settings page, **Variables** tab):

   | Name | Example |
   |------|---------|
   | `FEED_URLS` | `https://www.datacenterdynamics.com/en/rss/,https://status.aws.amazon.com/rss/all.rss,https://azure.status.microsoft/status/feed/` (optional: add `https://www.cloudflarestatus.com/history.atom` for edge/PoP work — often many *scheduled* maintenance items) |
   | `KEYWORDS` | Many providers + neutral terms (`data center`, `colo`, `facility`, …) — see [.env.example](.env.example) |
   | `TOPIC_RELAX_HOSTS` | `datacenterdynamics.com` (optional — pairs with **`OUTAGE_SIGNALS`**) |
   | `OUTAGE_SIGNALS` | `outage`, `fire`, `blackout`, `degraded`, … — recommended |
   | `NEWSAPI_QUERY` | Wide cloud + DC operator OR-list — see [.env.example](.env.example) |

   Set **`OUTAGE_SIGNALS`** on GitHub to silence non-outage DC news (and to enable **`TOPIC_RELAX_HOSTS`** for DCD). Leave **`OUTAGE_SIGNALS`** empty only for keyword-only mode (much noisier).

5. **Run the workflow manually once**: Actions → *Daily data center news monitor* → *Run workflow*. Then verify:
   - `FEED_URLS`, `KEYWORDS`, and (recommended) `OUTAGE_SIGNALS` are set (`NEWSAPI_QUERY` may be empty if you skip NewsAPI).
   - Secret `SLACK_WEBHOOK_URL` is set (the workflow sets `REQUIRE_SLACK_WEBHOOK=true`).
   - The job succeeds, Slack shows the digest, and the **daily-digest-*** artifact contains `reports/daily_report_YYYY-MM-DD.md`.

6. **Adjust the schedule** if needed: edit `.github/workflows/daily-monitor.yml` — cron uses UTC (`0 15 * * *` = 15:00 UTC daily).

## Local run

```bash
cd dc-news-scraper
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with real values (quote values that contain spaces, e.g. KEYWORDS="…")
set -a && source .env && set +a         # zsh/bash
python monitor.py
```

With `REQUIRE_SLACK_WEBHOOK=false` (default), you can omit `SLACK_WEBHOOK_URL` locally and still get `reports/daily_report_YYYY-MM-DD.md`.

## Outputs

- **Markdown**: `reports/daily_report_YYYY-MM-DD.md` — full list of matches with links and matched keywords.
- **Slack**: Plain-text digest (capped length); for the full list, open the workflow run artifact from GitHub Actions.

## Optional environment variables

See [.env.example](.env.example) for **`TOPIC_RELAX_HOSTS`**, `OUTAGE_SIGNALS`, default feeds, expanded `KEYWORDS`, `LOOKBACK_HOURS`, `NEWSAPI_PAGE_SIZE`, and `REQUIRE_SLACK_WEBHOOK`.

**Bias toward AWS:** the AWS status **`all`** RSS emits many items; widening **`KEYWORDS`**, adding other status feeds (`Azure`, `Cloudflare`), and **`TOPIC_RELAX_HOSTS=datacenterdynamics.com`** rebalances toward colocation and facility outages.
