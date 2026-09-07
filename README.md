# Keepa Price Dashboard

## Creator Connections video opportunities

The existing **Video Opportunities** page now supports strict campaign, commission, apparel, video and sales-growth qualification, with a resumable 100-ASIN test workflow. See [setup, data limitations and operating instructions](INFLUENCER_OPPORTUNITIES.md). The Apps Script upload action must be added to the existing deployed web app before using the new upload controller.

A personal Amazon deal dashboard that scans ASINs with Keepa, keeps recent price drops active for 24 hours, and displays clickable deal cards in a static web page.

## What It Does

- Reads ASINs from a Google Sheet CSV when `ASIN_CSV_URL` is configured, otherwise from `asins.csv`
- Scans the ASIN list in rotating windows so large lists can be checked over multiple runs
- Uses Keepa pricing stats to find products below their recent average price
- Keeps active deals in `data/deals_memory.json` until their 24-hour TTL expires
- Writes dashboard data to `data/deals.json`
- Tracks the next scan position in `data/scan_state.json`
- Displays searchable, sortable deal cards in `index.html`

## Repository Layout

- `fetch_keepa.py` - Keepa scanner, deal detection, memory cleanup, and scan-state rotation
- `fetch_keepa_history_probe.py` - Scanner wrapper that adds best-price-age details from Keepa history
- `index.html` - Static dashboard page
- `app.js` - Dashboard filtering, sorting, posting helpers, hide/remove actions, and image fallbacks
- `apps-script-publer-publisher.js` - Google Apps Script helper for Publer Page publishing and Publer Group CSV queueing
- `styles.css` - Dashboard styling
- `asins.csv` - Local fallback ASIN list
- `data/deals.json` - Current dashboard data
- `data/deals_memory.json` - 24-hour active deal memory
- `data/scan_state.json` - Rotating scan position
- `.github/workflows/update-deals.yml` - Manual/external-trigger scanner workflow

## Required Setup

Do not put API keys directly in the code.

Add this GitHub Actions secret:

- `KEEPA_API_KEY` - your Keepa API key

Optional GitHub Actions secrets:

- `AMAZON_TAG` - your Amazon affiliate tag, such as `simplewoodsho-20`
- `ASIN_CSV_URL` - published CSV URL for the Google Sheet ASIN source

If `ASIN_CSV_URL` is not set, the scanner uses `asins.csv` in this repository.

## How The Scan Runs

The active workflow is `Update Keepa Deals` in `.github/workflows/update-deals.yml`.

It is intentionally configured with `workflow_dispatch` only. That means it can be started manually from GitHub Actions or triggered by an external scheduler such as cron-job.org. The workflow comments currently expect an external trigger every 15 minutes.

Each run:

1. Installs Python and `requests`.
2. Runs `python fetch_keepa_history_probe.py`.
3. Updates `data/deals.json`, `data/deals_memory.json`, and `data/scan_state.json`.
4. Commits those data changes back to `main` when anything changed.

## Daily Scan Coverage

With a 15-minute external trigger, the workflow can run 96 times per day.

The current `SCAN_LIMIT` is `auto`. Each run counts the current ASIN list and scans enough rows to cover the full sheet within 24 hours, with a 10% buffer for growth.

## Keepa Token Use

The current settings are designed for a Keepa refill rate of 25 tokens per minute.

Each scheduled run automatically sizes the ASIN window, split into batches of 25 with a 60-second delay between batches. That keeps requests paced near the token refill rate while covering the full list within the daily window.

## Current Scan Settings

The workflow currently sets:

- `SCAN_LIMIT`: `auto`
- `SCAN_RUNS_PER_DAY`: `96`
- `SCAN_LIMIT_BUFFER_PERCENT`: `10`
- `KEEPA_BATCH_SIZE`: `25`
- `KEEPA_REQUEST_DELAY_SECONDS`: `60`
- `KEEPA_RATE_LIMIT_WAIT_SECONDS`: `70`
- `KEEPA_MAX_RETRIES`: `5`
- `DEAL_TTL_HOURS`: `24`

The scanner defaults to a 5 percent minimum drop and Amazon US unless those values are changed with environment variables.

## Removing ASINs From The Dashboard

The `Remove ASIN` button calls the connected Google Apps Script web app to remove the ASIN from the source Google Sheet, then hides the card after the sheet confirms the removal.

If the Google Apps Script request fails, the ASIN is queued locally instead. Once at least one ASIN is queued, use the `Copy removals` button at the top of the dashboard, then remove those ASINs from the source Google Sheet manually.

## Uploading Creator Connections CSV

The dashboard upload box streams CSVs into bounded parts and sends `action=uploadCreatorChunk` to the existing Google Apps Script web app. Its handler preserves the CSV bytes in `Mhhickma/Dashboard/data/creator-connections/` and confirms each GitHub save. Add the new action to the deployed script while preserving its other actions; see [the setup guide](INFLUENCER_OPPORTUNITIES.md).

## Publishing Deals To Publer

Selected deal cards include buttons for:

- Woodworking Group CSV: `Now`, `60`, `90`, and `120`
- Dad Deals Group CSV: `Now`, `60`, `90`, and `120`
- Woodworking Page: `Now`, `60`, `90`, and `120`
- Black Lab Page: `Now`, `60`, `90`, and `120`

The dashboard calls the connected Google Apps Script web app with `action=publishDeal`. Add the helpers in `apps-script-publer-publisher.js` to that Apps Script project. If the live Apps Script already has a `doGet(e)` function, merge the `publishDeal` action branch into the existing dispatcher instead of adding a second `doGet(e)`.

Store these values as Apps Script Script Properties. Do not commit real keys to GitHub.

- `PUBLER_API_KEY` - Publer API key with `posts`, `media`, and `accounts` scopes
- `AMAZON_ASSOCIATE_TAG` - Amazon Associates tag, such as `simplewoodsho-20`
- `GITHUB_TOKEN` - GitHub token with Contents read/write on `Mhhickma/Dashboard`

Optional Script Properties:

- `PUBLISH_MODE` - defaults to `draft`; set to `live` only after testing
- `PUBLER_WOODWORKING_WORKSPACE_ID` - defaults to `69ff46121fa916e7b4abad77`
- `PUBLER_WOODWORKING_PAGE_ACCOUNT_ID` - connected Woodworking Facebook Page account ID in Publer
- `PUBLER_BLACK_LAB_WORKSPACE_ID` - defaults to `69fa2708b5031ee6cc0cb0a8`
- `PUBLER_BLACK_LAB_PAGE_ACCOUNT_ID` - connected Black Lab Facebook Page account ID in Publer
- `JOTURL_API_URL` - JotURL API endpoint for creating a deep link
- `JOTURL_API_KEY` - JotURL API key

Page buttons create a Publer draft by default. Set `PUBLISH_MODE=live` when you want `Now` to publish immediately and the delay buttons to schedule future posts.

Group CSV buttons append rows using Publer's CSV template columns. The current files are:

- `data/publer_group_queue_woodworking.csv`
- `data/publer_group_queue_dad_deals.csv`

Upload the matching CSV to Publer for Facebook Group scheduling.

## Local Testing

```bash
pip install requests
export KEEPA_API_KEY="your_key_here"
export AMAZON_TAG="simplewoodsho-20"
python fetch_keepa.py
```

Then open `index.html` in your browser.

On Windows PowerShell, set temporary environment variables like this:

```powershell
$env:KEEPA_API_KEY="your_key_here"
$env:AMAZON_TAG="simplewoodsho-20"
python fetch_keepa.py
```

## Notes

The `data/*.json` files are committed on purpose so the static dashboard can load the latest generated deal data without a separate backend.
