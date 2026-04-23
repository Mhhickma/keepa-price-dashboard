import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

KEEPA_API_KEY = os.getenv("KEEPA_API_KEY")
AMAZON_TAG = os.getenv("AMAZON_TAG", "simplewoodsho-20")
DOMAIN_ID = int(os.getenv("KEEPA_DOMAIN_ID", "1"))  # 1 = Amazon US
MIN_DROP_PERCENT = float(os.getenv("MIN_DROP_PERCENT", "10"))
ASIN_FILE = Path("asins.csv")
OUTPUT_FILE = Path("data/deals.json")


def keepa_to_dollars(value):
    """Keepa stores Amazon prices as cents. -1 or 0 usually means unavailable."""
    if value in (None, -1, 0):
        return None
    return round(value / 100, 2)


def read_asins():
    if not ASIN_FILE.exists():
        raise FileNotFoundError("Missing asins.csv")

    asins = []
    with ASIN_FILE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            asin = (row.get("asin") or "").strip().upper()
            if asin and asin not in asins:
                asins.append(asin)
    return asins


def fetch_keepa_products(asins):
    if not KEEPA_API_KEY:
        raise RuntimeError("Missing KEEPA_API_KEY environment variable")

    url = "https://api.keepa.com/product"
    all_products = []

    # Keep batches small to avoid request/API issues.
    batch_size = 20
    for i in range(0, len(asins), batch_size):
        batch = asins[i : i + batch_size]
        params = {
            "key": KEEPA_API_KEY,
            "domain": DOMAIN_ID,
            "asin": ",".join(batch),
            "stats": 90,
            "history": 0,
        }
        response = requests.get(url, params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        all_products.extend(payload.get("products", []))
        time.sleep(1)

    return all_products


def build_deal(product):
    asin = product.get("asin")
    title = product.get("title") or asin
    stats = product.get("stats") or {}

    current_raw = None
    avg_90_raw = None
    min_90_raw = None

    current = stats.get("current") or []
    avg90 = stats.get("avg90") or []
    min90 = stats.get("min90") or []

    # Keepa price type index 0 is Amazon price.
    if len(current) > 0:
        current_raw = current[0]
    if len(avg90) > 0:
        avg_90_raw = avg90[0]
    if len(min90) > 0:
        min_90_raw = min90[0]

    current_price = keepa_to_dollars(current_raw)
    avg_90_price = keepa_to_dollars(avg_90_raw)
    min_90_price = keepa_to_dollars(min_90_raw)

    if not current_price or not avg_90_price or current_price >= avg_90_price:
        return None

    drop_percent = round(((avg_90_price - current_price) / avg_90_price) * 100, 1)
    if drop_percent < MIN_DROP_PERCENT:
        return None

    images_csv = product.get("imagesCSV") or ""
    image = None
    if images_csv:
        first_image = images_csv.split(",")[0]
        image = f"https://images-na.ssl-images-amazon.com/images/I/{first_image}"

    amazon_url = f"https://www.amazon.com/dp/{asin}?tag={AMAZON_TAG}"

    return {
        "asin": asin,
        "title": title,
        "current_price": current_price,
        "avg_90_price": avg_90_price,
        "min_90_price": min_90_price,
        "drop_percent": drop_percent,
        "image": image,
        "amazon_url": amazon_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    print("Starting Keepa price scan...")
    asins = read_asins()
    print(f"Loaded {len(asins)} ASINs")

    products = fetch_keepa_products(asins)
    print(f"Fetched {len(products)} products from Keepa")

    deals = []
    for product in products:
        deal = build_deal(product)
        if deal:
            deals.append(deal)

    deals.sort(key=lambda item: item["drop_percent"], reverse=True)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "deal_count": len(deals),
                "deals": deals,
            },
            f,
            indent=2,
        )

    print(f"Saved {len(deals)} deals to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
