# Data center downtime news monitor

A small daily pipeline: pull articles from RSS feeds (and optionally [NewsAPI](https://newsapi.org/)), filter for outage-related keywords, deduplicate by URL, write a Markdown digest under `reports/`, and post a short summary to Slack via an [incoming webhook](https://api.slack.com/messaging/webhooks).

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
   | `KEYWORDS` | `aws outage,azure outage,google cloud outage,outage,downtime,incident` |
   | `NEWSAPI_QUERY` | `"data center" AND (outage OR downtime OR incident OR failure)` |

5. **Run the workflow manually once**: Actions → *Daily data center news monitor* → *Run workflow*. Then verify:
   - `FEED_URLS` and `KEYWORDS` variables are set (`NEWSAPI_QUERY` may be empty if you skip NewsAPI).
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

See [.env.example](.env.example) for `LOOKBACK_HOURS`, `NEWSAPI_PAGE_SIZE`, and `REQUIRE_SLACK_WEBHOOK`.
