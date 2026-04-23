# Keepa Price Dashboard

A personal dashboard for scanning a list of Amazon ASINs with Keepa and displaying clickable deal cards.

## What it does

- Reads ASINs from `asins.csv`
- Uses the Keepa API to check pricing history
- Detects products that dropped below their recent average price
- Writes results to `data/deals.json`
- Displays the results as cards in `index.html`

## Important setup

Do not put your Keepa API key directly in the code.

For GitHub Actions, add this secret:

- `KEEPA_API_KEY`

Optional variable/secret:

- `AMAZON_TAG` — your Amazon affiliate tag, such as `simplewoodsho-20`

## How to use

1. Add ASINs to `asins.csv`.
2. Add your Keepa API key as a GitHub secret named `KEEPA_API_KEY`.
3. Run the GitHub Action manually, or let it run on schedule.
4. Open the site/dashboard to view price-drop cards.

## Local testing

```bash
pip install requests
export KEEPA_API_KEY="your_key_here"
export AMAZON_TAG="simplewoodsho-20"
python fetch_keepa.py
```

Then open `index.html` in your browser.
