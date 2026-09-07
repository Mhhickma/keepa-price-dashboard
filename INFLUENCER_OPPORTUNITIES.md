# Creator Connections video opportunities

This feature runs inside the existing static Dashboard. Python scans run in GitHub Actions; the page reads generated JSON. No Keepa credential is sent to the browser.

## First run

1. Publish this branch through the normal Dashboard deployment.
2. Update the existing Apps Script web app: add `uploadCreatorChunkResponse_` from `apps-script-creator-upload.js`, and add the `uploadCreatorChunk` case to its existing `doPost` router. **Preserve the deployed script's other handlers**, including sheet and publishing actions. Deploy a new version of the existing web app; merely updating this repository does not update Apps Script. Its existing `GITHUB_TOKEN` Script Property needs Contents write access to `Mhhickma/Dashboard`. No new Keepa secret is needed.
3. Open Video Opportunities and upload CSVs. Wait for all parts to be confirmed before scanning. The upload is append-only; repeated campaign IDs are deduplicated. Failed uploads report how many parts were saved. Complete/retry the upload before running the scanner.
4. Open the linked **Update Influencer Opportunities** workflow. Keep defaults: limit 100, batch size 10, token budget 1500, reset false. The workflow reads `secrets.KEEPA_API_KEY`.
5. Resume by running that workflow again with the same limit and reset false. Raising the limit grows the cohort; the default never silently advances to another 100 ASINs. Reset explicitly discards the cache/cohort and may spend tokens again.

No Creator Connections CSV was present in the repository during implementation. Tests use controlled fixtures, not live Keepa results.

## Campaign rules and imports

- UTF-8/BOM CSVs are parsed with a real CSV reader, including quoted commas/newlines. ASIN List is expanded using complete 10-character alphanumeric tokens. All files in the input directory are read; the old scanner's 40-file/45-day caps do not apply.
- Both start and end must parse, and today's UTC date must fall inclusively between them. Missing/invalid dates fail closed. Explicit paused, inactive, cancelled, ended, expired, closed or upcoming statuses fail. Commission must be at least 10%. Percent strings and fraction notation (0.10) are supported; a bare value of 1 means 100%.
- Campaign ID identifies duplicate snapshots. Without an ID, name/brand/start/end identify the campaign. New timestamped upload filenames win over legacy filenames; otherwise lexical filename order decides which snapshot supplies campaign values. Use timestamped filenames for external imports. A newer cancellation invalidates older snapshots. ASIN memberships are unioned across exports of the same campaign.
- Distinct qualifying campaigns preserve name, brand, start/end, commission, budget, remaining budget, available/total slots and recommended flag. Main row commission is the highest eligible rate; its budget/slots come from that same campaign, not a sum that double-counts shared budgets. Details show the other campaigns.
- Common header spellings are normalized. Primary headers: Campaign Id, Campaign Name, Brand Name, Campaign Start Date, Campaign End Date, Commission Rate, Campaign Budget, Budget Remaining, Available Slots, Total Slots, Recommended, ASIN List. Optional Status/Campaign Status is honored.
- Explicit apparel categories are excluded; the broad Clothing, Shoes & Jewelry root is not itself excluded. PPE/product-use evidence takes precedence for functional protection. Products without sufficient classification evidence are unavailable. Initial classification can require a Keepa request because Creator Connections exports generally do not contain ASIN-level taxonomy.

## Data interpretation

The six hard rules are mandatory. Score never changes qualification. Additional freshness/standard-product checks prevent unsupported or stale records passing.

`monthlySold` is Amazon's bracketed bought-in-past-month metric, not exact unit sales. `monthlySoldHistory` is decoded as timestamp/value pairs. The 90-day average is the time-weighted step value over the entire preceding 90 days, requiring a known starting anchor and no invalid intervals. History is requested without a day cutoff to retain that anchor. Missing coverage, zero denominator or a monthly-sold update older than 30 days makes growth unavailable. The hard comparison is `current * 10 >= average * 11`, including the exact 10% boundary. BSR is diagnostic only: positive values mean the current primary rank is lower than the 30/90-day average. A known category-reference change suppresses these diagnostics.

Product requests use `/product`, US domain 1, `history=1`, `stats=90`, `videos=1`, `offers=20`, `only-live-offers=1`. A successful offers refresh and an explicit video array are required for qualification. Seller, Merchant, Brand and Vendor identify merchant-side videos. Main alone does not. Influencer and community counts are separate. Video URLs deduplicate repeated entries. Missing fields never become zero. Keepa covers carousel videos and up to ten community videos; displayed counts are **Keepa-observed**, not a guaranteed exhaustive Amazon-wide total. This limitation also applies to products with fewer than five observed videos.

Price uses current Buy Box including shipping, then Amazon, then New price tracks; the chosen source is exported. Negative sentinel values become unavailable. Cache validity defaults to 24 hours. The browser hides a row when its cache or earliest campaign end expires, even before another scan publishes. Resume to recompute it.

Score (0–100) weights logarithmic monthly sold 25, growth capped at 100% 25, fewer videos 20, commission capped at 30% 15, price capped at $200 5, budget capped at $10,000 5, and available slots capped at 100 5. Missing optional fields add zero points.

## Scale, cache and recovery

CSV import uses a disk-backed SQLite index and checkpoints every 500 logical rows. A changed file is reimported using its SHA-256 fingerprint; unchanged files are skipped. Removed sources lose their memberships. Interrupted imports publish no result pages until the source set is complete. Resuming a partial file rereads/skips its already committed rows without re-querying Keepa.

Browser uploads stream CSV records into parts of at most 2 MiB. A single record above that limit is rejected with a local-import instruction. For extremely large datasets, use the CLI below with local CSV files instead of adding large CSVs to GitHub. The parser permits fields up to 16 MiB and stops on malformed or larger fields. SQLite still retains all imported campaign/ASIN relationships. Processing is explicitly capped at 10,000 selected ASINs; larger source datasets do not increase paid requests automatically. Increase cohorts deliberately after inspecting the 100-ASIN test.

The checkpoint contains the selection, normalized product cache, campaign index, per-file import offsets and failed ASINs. GitHub stores it as `influencer-checkpoint` artifacts for 90 days, restored only from the current branch. This is durable state, not an evictable Actions cache. If prior published status exists but its checkpoint is absent/expired, restore fails and requires an explicit reset. Download a checkpoint artifact for long-term storage before retention expires.

Runs stop cooperatively after 15 minutes, before the 25-minute workflow timeout, reserving time to upload state. Each successful batch commits immediately. Network/429/5xx retries use exponential backoff, jitter and refill/Retry-After information. A run reserves up to 12 tokens per ASIN per attempted request for offers=20; uncertain network outcomes still consume that reservation. Reported actual token consumption and balance are displayed separately. A small budget may pause before finishing a batch; resume or choose a smaller batch. Both dashboard scanners share one Keepa token balance.

Manual hard cancellation/runner loss can prevent the final artifact upload; the previous artifact then remains the recovery point and the last unuploaded batches may be queried again. Use the normal time-budget stop for reliable resume. Missing products and terminal errors are logged by ASIN using sanitized codes. URLs, API keys and raw HTTP errors are never logged.

Generated JSON under `data/influencer/` follows the dashboard's existing publication pattern. The table reads pages of 250 rows, renders 50 at a time, and offers global filtering/sorting across the bounded cohort. Campaign details load on demand. Excluded/unavailable rows are hidden by default; failures are separate. Source CSVs and result campaign information share the repository's existing visibility. Checkpoints stay in Actions artifacts rather than Git history.

## Local processing and tests

Use the existing `KEEPA_API_KEY` environment variable; never paste it into a command, file or browser. Local runs use the same SQLite state across invocations.

```sh
python -m pip install requests
python influencer_pipeline.py --input /path/to/csv-directory --offline
python influencer_pipeline.py --input /path/to/csv-directory --limit 100 --batch-size 10 --token-budget 1500
python -m unittest discover -s tests -v
node tests/browser-influencer.cjs
```

Browser tests require Playwright and an installed Chromium/Edge browser. They mock uploaded data and all upload responses, checking the UI and multi-part CSV integrity without external writes. The checkpoint path defaults to `.influencer-state/checkpoint.sqlite` (gitignored). Export location is configurable with `--output`. Offline mode imports and evaluates cached products only and makes no API request.

References: [Keepa product request](https://keepa.com/api-docs/product.html), [product fields](https://keepa.com/api-docs/product-object.html).
