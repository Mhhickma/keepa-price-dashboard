import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

KEEPA_API_KEY = os.getenv("KEEPA_API_KEY")
AMAZON_TAG = os.getenv("AMAZON_TAG") or "simplewoodsho-20"
DOMAIN_ID = int(os.getenv("KEEPA_DOMAIN_ID", "1"))  # 1 = Amazon US
MIN_DROP_PERCENT = float(os.getenv("MIN_DROP_PERCENT", "5"))
ASIN_FILE = Path("asins.csv")
OUTPUT_FILE = Path("data/deals.json")


def keepa_to_dollars(value):
    """Convert Keepa cents to dollars.

    Keepa sometimes returns a single integer price, and sometimes returns
    a small list such as [timestamp, price]. This keeps the script from
    crashing when Keepa returns the list format.
    """
    if value is None:
        return None

    if isinstance(value, list):
        numeric_values = [item for item in value if isinstance(item, (int, float))]
        if not numeric_values:
            return None
        value = numeric_values[-1]

    if not isinstance(value, (int, float)) or value <= 0:
        return None

    return round(value / 100, 2)


def amazon_image_fallback(asin):
    """Amazon Associates image endpoint fallback by ASIN."""
    if not asin:
        return None
    return (
        "https://ws-na.amazon-adsystem.com/widgets/q?"
        f"_encoding=UTF8&ASIN={asin}&Format=_SL500_&ID=AsinImage"
        "&MarketPlace=US&ServiceVersion=20070822"
    )


def get_product_image(product, asin):
    """Build an image URL from Keepa's imagesCSV field, then fall back to ASIN image."""
    images_csv = product.get("imagesCSV") or ""
    if images_csv:
        first_image = images_csv.split(",")[0].strip()
        if first_image:
            if first_image.startswith("http"):
                return first_image
            return f"https://images-na.ssl-images-amazon.com/images/I/{first_image}"

    return amazon_image_fallback(asin)


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

    batch_size = 20
    for i in range(0, len(asins), batch_size):
        batch = asins[i : i + batch_size]
        params = {
            "key": KEEPA_API_KEY,
            "domain": DOMAIN_ID,
            "asin": ",".join(batch),
            "stats": 7,
            "history": 1,
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

    current = stats.get("current") or []
    avg7 = stats.get("avg") or []
    min7 = stats.get("min") or []

    current_raw = current[0] if len(current) > 0 else None
    avg_7_raw = avg7[0] if len(avg7) > 0 else None
    min_7_raw = min7[0] if len(min7) > 0 else None

    current_price = keepa_to_dollars(current_raw)
    avg_7_price = keepa_to_dollars(avg_7_raw)
    min_7_price = keepa_to_dollars(min_7_raw)

    if not current_price or not avg_7_price or current_price >= avg_7_price:
        return None

    drop_percent = round(((avg_7_price - current_price) / avg_7_price) * 100, 1)
    if drop_percent < MIN_DROP_PERCENT:
        return None

    image = get_product_image(product, asin)
    amazon_url = f"https://www.amazon.com/dp/{asin}?tag={AMAZON_TAG}"

    return {
        "asin": asin,
        "title": title,
        "current_price": current_price,
        "avg_7_price": avg_7_price,
        "min_7_price": min_7_price,
        "drop_percent": drop_percent,
        "image": image,
        "amazon_url": amazon_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    print("Starting Keepa 7-day price scan...")
    asins = read_asins()
    print(f"Loaded {len(asins)} ASINs")

    products = fetch_keepa_products(asins)
    print(f"Fetched {len(products)} products from Keepa")

    deals = []
    skipped = 0
    missing_images = 0
    for product in products:
        try:
            deal = build_deal(product)
        except Exception as exc:
            skipped += 1
            print(f"Skipped {product.get('asin', 'unknown ASIN')}: {exc}")
            continue
        if deal:
            if not deal.get("image"):
                missing_images += 1
                print(f"No image found for {deal.get('asin')}")
            deals.append(deal)

    deals.sort(key=lambda item: item["drop_percent"], reverse=True)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "comparison_window": "7-day average",
                "deal_count": len(deals),
                "skipped_count": skipped,
                "missing_image_count": missing_images,
                "deals": deals,
            },
            f,
            indent=2,
        )

    print(f"Saved {len(deals)} 7-day price drops to {OUTPUT_FILE}")
    if skipped:
        print(f"Skipped {skipped} products because their Keepa data format was incomplete or unexpected")
    if missing_images:
        print(f"{missing_images} deals did not include an image from Keepa or Amazon fallback")


if __name__ == "__main__":
    main()
