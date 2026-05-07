# Data center downtime news monitor

A small daily pipeline: pull articles from RSS feeds (and optionally [NewsAPI](https://newsapi.org/)), filter for **downtime-related** content, deduplicate by URL, write a Markdown digest under `reports/`, and post a short summary to Slack via an [incoming webhook](https://api.slack.com/messaging/webhooks).

**Filtering:** each article must match at least one **`KEYWORDS`** phrase *and* (if you set it) at least one **`OUTAGE_SIGNALS`** phrase — all in the title or summary. Use `KEYWORDS` for *topic* (providers, “data center”, colocation). Use `OUTAGE_SIGNALS` for *problem* language (outage, disruption, degradation, …). That stops “generic DC industry news” from flooding Slack when `KEYWORDS` is broad.

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
   | `FEED_URLS` | `https://www.datacenterdynamics.com/en/rss/,https://status.aws.amazon.com/rss/all.rss` |
   | `KEYWORDS` | `AWS,Azure,Google Cloud,Equinix,data center,datacenter,colocation` |
   | `OUTAGE_SIGNALS` | `outage,downtime,service disruption,degradation,incident,unavailable,failure,error rate` |
   | `NEWSAPI_QUERY` | `(outage OR downtime OR "service disruption" OR degradation) AND ("data center" OR datacenter OR AWS OR Equinix)` |

   Set **`OUTAGE_SIGNALS`** on GitHub to silence non-outage DC news. Leave it empty only if you want keyword-only matching (noisier).

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
# Edit .env with real values
export $(grep -v '^#' .env | xargs)   # or set vars manually
python monitor.py
```

With `REQUIRE_SLACK_WEBHOOK=false` (default), you can omit `SLACK_WEBHOOK_URL` locally and still get `reports/daily_report_YYYY-MM-DD.md`.

## Outputs

- **Markdown**: `reports/daily_report_YYYY-MM-DD.md` — full list of matches with links and matched keywords.
- **Slack**: Plain-text digest (capped length); for the full list, open the workflow run artifact from GitHub Actions.

## Optional environment variables

See [.env.example](.env.example) for `OUTAGE_SIGNALS`, `LOOKBACK_HOURS`, `NEWSAPI_PAGE_SIZE`, and `REQUIRE_SLACK_WEBHOOK`.
