# Data center downtime news monitor

A small daily pipeline: pull articles from RSS feeds (and optionally [NewsAPI](https://newsapi.org/)), filter for **downtime-related** content, deduplicate by URL, write a Markdown digest under `reports/`, and post a short summary to Slack via an [incoming webhook](https://api.slack.com/messaging/webhooks).

**Filtering:** **`KEYWORDS`** and **`OUTAGE_SIGNALS`** use smarter matching than raw substring checks (short tokens such as **`AWS`** / **`GCP`** must appear as whole words so “straws” is not counted as **`AWS`**; **`fire`** must be a distinct word vs “bonfire”). Multi-word phrases (e.g. **`Google Cloud`**, **`service disruption`**) stay substring-based.

Use **`TOPIC_RELAX_HOSTS`** so sites like **[Data Centre Dynamics](https://www.datacenterdynamics.com/)** can contribute colo/DC outage headlines without naming a hyperscaler: relaxation applies **only** when **`OUTAGE_SIGNALS` is non-empty** (those pages still need outage/problem language).

When NewsAPI is enabled, set **`NEWSAPI_ANCHORS`** so each hit must also mention real DC/cloud context (otherwise NewsAPI returns random factory fires, food-plant evacuations, and “failure” in unrelated headlines). Consent/redirect URLs (e.g. Yahoo cookie walls) are dropped.

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
   | `FEED_URLS` | Long comma-separated RSS/Atom list — default in [.env.example](.env.example) includes **DCD**, **AWS/Azure**, major **colo/REIT** (Digital Realty, DataBank, Equinix Metal), **CDN** (Akamai), **hosted cloud** (OVHcloud, IBM, DO, Linode, Scaleway, Snowflake), **Meta** status. Optional SaaS-heavy feeds (**GitHub**, **Twilio**, **Salesforce**, **Cloudflare**) are commented there. |
   | `KEYWORDS` | DC/cloud operators and neutral terms (`data center`, `colo`, …) — see [.env.example](.env.example) (avoid bare **facility**; it matches any industrial building) |
   | `TOPIC_RELAX_HOSTS` | `datacenterdynamics.com` (optional — pairs with **`OUTAGE_SIGNALS`**) |
   | `OUTAGE_SIGNALS` | `outage`, `fire` (word-level), `blackout`, `degraded`, … — recommended |
   | `NEWSAPI_ANCHORS` | Required for sane NewsAPI results — copy from [.env.example](.env.example); leave empty only if you accept noisy matches |
   | `NEWSAPI_QUERY` | Infra-focused AND-clause — avoid bare `cloud` / `facility`; see [.env.example](.env.example) |

   Set **`OUTAGE_SIGNALS`** on GitHub to silence non-outage DC news (and to enable **`TOPIC_RELAX_HOSTS`** for DCD). Leave **`OUTAGE_SIGNALS`** empty only for keyword-only mode (much noisier).

5. **Run the workflow manually once**: Actions → *Daily data center news monitor* → *Run workflow*. Then verify:
   - `FEED_URLS`, `KEYWORDS`, **`NEWSAPI_ANCHORS`** (if you use NewsAPI), and (recommended) `OUTAGE_SIGNALS` are set (`NEWSAPI_QUERY` may be empty if you skip NewsAPI).
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

See [.env.example](.env.example) for **`NEWSAPI_ANCHORS`**, **`TOPIC_RELAX_HOSTS`**, `OUTAGE_SIGNALS`, default feeds, expanded `KEYWORDS`, `LOOKBACK_HOURS`, `NEWSAPI_PAGE_SIZE`, and `REQUIRE_SLACK_WEBHOOK`.

**Bias toward AWS:** the AWS status **`all`** RSS emits many items; widening **`KEYWORDS`**, adding other status feeds, **`TOPIC_RELAX_HOSTS`**, and **`NEWSAPI_ANCHORS`** (plus removing bare **`facility`** / substring **`AWS`** false positives) keeps the digest on real DC/cloud outage news.
