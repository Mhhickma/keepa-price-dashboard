import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

try:
    from amazon_creatorsapi import AmazonCreatorsApi, Country
    from amazon_creatorsapi.models import GetItemsResource
except Exception:
    AmazonCreatorsApi = None
    Country = None
    GetItemsResource = None

try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(1024 * 1024 * 1024)

KEEPA_API_KEY = os.getenv("KEEPA_API_KEY")
AMAZON_TAG = os.getenv("AMAZON_TAG") or "simplewoodsho-20"
CREATORS_CREDENTIAL_ID = os.getenv("CREATORS_CREDENTIAL_ID")
CREATORS_CREDENTIAL_SECRET = os.getenv("CREATORS_CREDENTIAL_SECRET")
DOMAIN_ID = int(os.getenv("KEEPA_DOMAIN_ID", "1"))
MIN_DROP_PERCENT = float(os.getenv("MIN_DROP_PERCENT", "0"))

# Defaults are set for an approximately 12-hour full spreadsheet rotation.
# With a 15-minute workflow schedule, 48 scan windows/day checks about 300-331 ASINs per run.
BATCH_SIZE = int(os.getenv("KEEPA_BATCH_SIZE", "100"))
REQUEST_DELAY_SECONDS = int(os.getenv("KEEPA_REQUEST_DELAY_SECONDS", "30"))
RATE_LIMIT_WAIT_SECONDS = int(os.getenv("KEEPA_RATE_LIMIT_WAIT_SECONDS", "70"))
MAX_RETRIES = int(os.getenv("KEEPA_MAX_RETRIES", "5"))
SCAN_LIMIT_RAW = os.getenv("SCAN_LIMIT", "auto").strip().lower()
SCAN_RUNS_PER_DAY = max(1, int(os.getenv("SCAN_RUNS_PER_DAY", "48")))
SCAN_LIMIT_BUFFER_PERCENT = max(0, float(os.getenv("SCAN_LIMIT_BUFFER_PERCENT", "10")))
DEAL_TTL_HOURS = int(os.getenv("DEAL_TTL_HOURS", "24"))
LIVE_OFFER_DEBUG_SAMPLE_LIMIT = int(os.getenv("LIVE_OFFER_DEBUG_SAMPLE_LIMIT", "8"))
REQUIRE_PRIME_OR_AMAZON_PRICE_SOURCE = os.getenv("REQUIRE_PRIME_OR_AMAZON_PRICE_SOURCE", "false").strip().lower() not in {"0", "false", "no"}
KEEPA_OFFERS_LIMIT = int(os.getenv("KEEPA_OFFERS_LIMIT", "10"))
KEEPA_LIGHTNING_DEALS_ENABLED = os.getenv("KEEPA_LIGHTNING_DEALS_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
KEEPA_LIGHTNING_DEAL_SCAN_LIMIT = max(0, int(os.getenv("KEEPA_LIGHTNING_DEAL_SCAN_LIMIT", "40")))
KEEPA_LIGHTNING_DEAL_TIME_BUDGET_SECONDS = max(0, int(os.getenv("KEEPA_LIGHTNING_DEAL_TIME_BUDGET_SECONDS", "120")))
ASIN_TOOLS_WEB_APP_URL = os.getenv(
    "ASIN_TOOLS_WEB_APP_URL",
    "https://script.google.com/macros/s/AKfycbxU4HTktR6zH5Wfbk58V24X-HAE9kZYlzdlm1gqMp1NL_ZGzF7p-0VAL5VeGNfnAyxESA/exec",
).strip()
REMOVE_VISIBLE_SHIPPING_ASINS = os.getenv("REMOVE_VISIBLE_SHIPPING_ASINS", "true").strip().lower() not in {"0", "false", "no"}

CREATOR_CONNECTIONS_REPO = os.getenv("CREATOR_CONNECTIONS_REPO", "Mhhickma/Dashboard")
CREATOR_CONNECTIONS_PATH = os.getenv("CREATOR_CONNECTIONS_PATH", "data/creator-connections")
CREATOR_CONNECTIONS_REF = os.getenv("CREATOR_CONNECTIONS_REF", "main")
CREATOR_CONNECTIONS_MAX_FILES = int(os.getenv("CREATOR_CONNECTIONS_MAX_FILES", "40"))
CREATOR_CONNECTIONS_MAX_FILE_AGE_DAYS = int(os.getenv("CREATOR_CONNECTIONS_MAX_FILE_AGE_DAYS", "45"))

# Keepa stats array price indexes. These are fallback tracks only.
# Prime Exclusive is parsed from offers[].isPrimeExcl + offers[].primeExclCSV.
PRICE_TRACKS = [
    {"type": "amazon", "label": "Amazon price", "index": 0, "source_suffix": "amazon"},
    {"type": "new", "label": "New price", "index": 1, "source_suffix": "new"},
    {"type": "new_fba_prime", "label": "New FBA / Prime price", "index": 10, "source_suffix": "new_fba_prime"},
    {"type": "buy_box", "label": "Buy Box price", "index": 18, "source_suffix": "buy_box"},
]
QUALIFYING_PRICE_TRACK_TYPES = {"amazon", "new_fba_prime"}
QUALIFYING_DEAL_PRICE_TYPES = QUALIFYING_PRICE_TRACK_TYPES | {"keepa_preferred_offer", "prime_exclusive_offer", "lightning_deal"}

ASIN_CSV_URL = os.getenv("ASIN_CSV_URL", "").strip()
ASIN_FILE = Path("asins.csv")
OUTPUT_FILE = Path("data/deals.json")
STATE_FILE = Path("data/scan_state.json")
MEMORY_FILE = Path("data/deals_memory.json")
ASIN_RE = re.compile(r"\bB[0-9A-Z]{9}\b")
KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)
NON_AMAZON_PRICE_TYPES = {track["type"] for track in PRICE_TRACKS if track["type"] != "amazon"}
NON_AMAZON_PRICE_TYPES.add("prime_exclusive_offer")
NON_AMAZON_PRICE_TYPES.add("keepa_preferred_offer")
NON_AMAZON_PRICE_TYPES.add("lightning_deal")
LIVE_OFFER_DEBUG_SAMPLES = []
VISIBLE_SHIPPING_ASINS = {}


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def parse_iso_datetime(value):
    if not value:
        return None


def parse_campaign_date(value):
    value = str(value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except Exception:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def keepa_to_dollars(value):
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


def price_from_stats_array(stats, key, price_index):
    values = stats.get(key) or []
    if len(values) <= price_index:
        return None
    return keepa_to_dollars(values[price_index])


def amazon_image_fallback(asin):
    if not asin:
        return None
    return f"https://m.media-amazon.com/images/P/{asin}.01._SL500_.jpg"


def compact_image_url(url, size=500):
    if not url:
        return None
    return re.sub(r"\._SL\d+_\.", f"._SL{size}_.", str(url))


def get_product_image_candidates(product, asin):
    candidates = []
    images_csv = product.get("imagesCSV") or ""
    if images_csv:
        for image_name in images_csv.split(","):
            image_name = image_name.strip()
            if not image_name:
                continue
            if image_name.startswith("http"):
                candidates.append(image_name)
            else:
                candidates.append(f"https://images-na.ssl-images-amazon.com/images/I/{image_name}")
                candidates.append(f"https://m.media-amazon.com/images/I/{image_name}")

    fallback = amazon_image_fallback(asin)
    if fallback:
        candidates.append(fallback)

    return list(dict.fromkeys(candidates))


def get_product_image(product, asin):
    candidates = get_product_image_candidates(product, asin)
    return candidates[0] if candidates else None


def is_weak_image_url(url):
    return not url or "/images/P/" in str(url)


def creator_image_resources():
    return [
        GetItemsResource.IMAGES_DOT_PRIMARY_DOT_HIGH_RES,
        GetItemsResource.IMAGES_DOT_PRIMARY_DOT_LARGE,
        GetItemsResource.IMAGES_DOT_PRIMARY_DOT_MEDIUM,
        GetItemsResource.IMAGES_DOT_PRIMARY_DOT_SMALL,
    ]


def creator_live_offer_resources():
    resources = [
        GetItemsResource.OFFERS_V2_DOT_LISTINGS_DOT_PRICE,
        GetItemsResource.OFFERS_V2_DOT_LISTINGS_DOT_AVAILABILITY,
        GetItemsResource.OFFERS_V2_DOT_LISTINGS_DOT_CONDITION,
        GetItemsResource.OFFERS_V2_DOT_LISTINGS_DOT_IS_BUY_BOX_WINNER,
    ]
    optional_names = [
        "OFFERS_V2_DOT_LISTINGS_DOT_DELIVERY_INFO",
        "OFFERS_V2_DOT_LISTINGS_DOT_DELIVERY_INFO_DOT_IS_PRIME_ELIGIBLE",
        "OFFERS_V2_DOT_LISTINGS_DOT_DELIVERY_INFO_DOT_IS_FREE_SHIPPING_ELIGIBLE",
        "OFFERS_DOT_LISTINGS_DOT_DELIVERY_INFO",
        "OFFERS_DOT_LISTINGS_DOT_DELIVERY_INFO_DOT_IS_PRIME_ELIGIBLE",
        "OFFERS_DOT_LISTINGS_DOT_DELIVERY_INFO_DOT_IS_FREE_SHIPPING_ELIGIBLE",
    ]
    for name in optional_names:
        resource = getattr(GetItemsResource, name, None)
        if resource and resource not in resources:
            resources.append(resource)
    return resources


def creator_image_from_item(item):
    try:
        primary = item.images.primary
        for size in ("large", "medium", "small", "hi_res"):
            image_size = getattr(primary, size, None)
            url = getattr(image_size, "url", None)
            if url:
                return compact_image_url(url, 500)
    except Exception:
        pass
    return None


def fetch_creator_images(asins):
    if not asins:
        return {}
    if not (AmazonCreatorsApi and Country and GetItemsResource):
        print("Amazon Creators API image fallback skipped: package is not installed")
        return {}
    if not (CREATORS_CREDENTIAL_ID and CREATORS_CREDENTIAL_SECRET):
        print("Amazon Creators API image fallback skipped: missing creator credentials")
        return {}

    images = {}
    for index in range(0, len(asins), 10):
        batch = asins[index:index + 10]
        try:
            amazon = AmazonCreatorsApi(
                credential_id=CREATORS_CREDENTIAL_ID,
                credential_secret=CREATORS_CREDENTIAL_SECRET,
                version="3.1",
                tag=AMAZON_TAG,
                country=Country.US,
            )
            for item in amazon.get_items(batch, resources=creator_image_resources()):
                image = creator_image_from_item(item)
                if image:
                    images[str(item.asin).upper()] = image
        except Exception as exc:
            print(f"Warning: creator image batch failed for {batch[0]}-{batch[-1]}: {exc}")
        time.sleep(0.2)
    return images


def creator_live_offer_from_item(item):
    try:
        listings = item.offers_v2.listings or []
    except Exception:
        return None

    selected = None
    for listing in listings:
        try:
            if listing.is_buy_box_winner:
                selected = listing
                break
        except Exception:
            pass
    if selected is None and listings:
        selected = listings[0]
    if selected is None:
        return None

    try:
        condition = selected.condition.value
        if condition and str(condition).lower() != "new":
            return None
    except Exception:
        pass

    try:
        availability = selected.availability.type
        if str(availability or "").upper() == "UNAVAILABLE":
            return None
    except Exception:
        pass

    shipping_status = creator_shipping_status(selected)
    record_live_offer_debug(item, selected, shipping_status)

    try:
        price = round(float(selected.price.money.amount), 2)
    except Exception:
        return None

    try:
        display = selected.price.money.display_amount
    except Exception:
        display = f"${price:,.2f}"

    try:
        currency = selected.price.money.currency
    except Exception:
        currency = "USD"

    return {
        "current_price": price,
        "price": display,
        "currency": currency,
        "availability": str(availability or ""),
        "shipping_status": shipping_status,
    }


def bool_attr(obj, *names):
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if isinstance(value, bool):
            return value
        if value is not None:
            text = str(value).strip().lower()
            if text in {"true", "yes", "1"}:
                return True
            if text in {"false", "no", "0"}:
                return False
    return None


def debug_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    text = str(value)
    if len(text) > 160:
        text = text[:157] + "..."
    return text


def debug_public_attrs(obj):
    if obj is None:
        return {}
    attrs = {}
    try:
        names = [name for name in dir(obj) if not name.startswith("_")]
    except Exception:
        return attrs
    for name in names:
        if name in {"model_config", "model_fields", "model_fields_set"}:
            continue
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if callable(value):
            continue
        if value is None or isinstance(value, (bool, int, float, str)):
            attrs[name] = value
    return attrs


def record_live_offer_debug(item, listing, shipping_status):
    if len(LIVE_OFFER_DEBUG_SAMPLES) >= LIVE_OFFER_DEBUG_SAMPLE_LIMIT:
        return
    sample = {
        "asin": str(getattr(item, "asin", "") or "").upper(),
        "shipping_status": shipping_status,
        "listing_attrs": debug_public_attrs(listing),
        "nested_attrs": {},
    }
    for name in (
        "availability", "condition", "delivery_info", "deliveryInfo", "delivery",
        "fulfillment", "fulfillment_info", "fulfillmentInfo", "merchant_info",
        "merchantInfo", "price", "shipping", "shipping_info", "shippingInfo",
    ):
        try:
            nested = getattr(listing, name)
        except Exception:
            continue
        if nested is None:
            continue
        attrs = debug_public_attrs(nested)
        sample["nested_attrs"][name] = attrs if attrs else debug_value(nested)
    LIVE_OFFER_DEBUG_SAMPLES.append(sample)


def creator_shipping_status(listing):
    delivery_info = None
    for attr in ("delivery_info", "deliveryInfo", "delivery"):
        try:
            delivery_info = getattr(listing, attr)
            if delivery_info:
                break
        except Exception:
            pass

    prime = bool_attr(listing, "is_prime_eligible", "isPrimeEligible", "prime")
    free_shipping = bool_attr(listing, "is_free_shipping_eligible", "isFreeShippingEligible", "free_shipping")
    if delivery_info:
        delivery_prime = bool_attr(delivery_info, "is_prime_eligible", "isPrimeEligible", "prime")
        delivery_free = bool_attr(delivery_info, "is_free_shipping_eligible", "isFreeShippingEligible", "free_shipping")
        prime = prime if prime is not None else delivery_prime
        free_shipping = free_shipping if free_shipping is not None else delivery_free

    return {
        "has_shipping_evidence": prime is not None or free_shipping is not None,
        "prime_or_free_shipping": bool(prime or free_shipping),
        "is_prime_eligible": bool(prime),
        "is_free_shipping_eligible": bool(free_shipping),
    }


def fetch_creator_live_offers(asins):
    if not asins:
        return {}
    if not (AmazonCreatorsApi and Country and GetItemsResource):
        print("Amazon Creators API live price skipped: package is not installed")
        return {}
    if not (CREATORS_CREDENTIAL_ID and CREATORS_CREDENTIAL_SECRET):
        print("Amazon Creators API live price skipped: missing creator credentials")
        return {}

    offers = {}
    for index in range(0, len(asins), 10):
        batch = asins[index:index + 10]
        try:
            amazon = AmazonCreatorsApi(
                credential_id=CREATORS_CREDENTIAL_ID,
                credential_secret=CREATORS_CREDENTIAL_SECRET,
                version="3.1",
                tag=AMAZON_TAG,
                country=Country.US,
            )
            for item in amazon.get_items(batch, resources=creator_live_offer_resources()):
                offer = creator_live_offer_from_item(item)
                if offer:
                    offers[str(item.asin).upper()] = offer
        except Exception as exc:
            print(f"Warning: creator live price batch failed for {batch[0]}-{batch[-1]}: {exc}")
        time.sleep(0.2)
    return offers


def enrich_deal_images_with_creator_api(deals):
    needs_image = [
        str(deal.get("asin") or "").upper()
        for deal in deals
        if deal.get("asin") and is_weak_image_url(deal.get("image"))
    ]
    needs_image = list(dict.fromkeys(needs_image))
    if not needs_image:
        return 0

    creator_images = fetch_creator_images(needs_image)
    updated = 0
    for deal in deals:
        asin = str(deal.get("asin") or "").upper()
        image = creator_images.get(asin)
        if not image:
            continue
        existing_candidates = deal.get("image_candidates") or []
        deal["image"] = image
        deal["image_candidates"] = list(dict.fromkeys([image, *existing_candidates]))
        updated += 1

    if updated:
        print(f"Updated {updated} regular dashboard deal images from Amazon Creators API")
    return updated


def asins_from_csv_text(csv_text, source_name):
    asins = []
    seen = set()
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        raise ValueError(f"No rows found in {source_name}")

    def add_asin(value):
        raw = str(value or "").strip().upper()
        if not raw or raw in ("ASIN", "ASINS"):
            return
        match = ASIN_RE.search(raw)
        asin = match.group(0) if match else raw
        if len(asin) != 10:
            print(f"Skipping invalid ASIN value: {raw}")
            return
        if asin in seen:
            return
        seen.add(asin)
        asins.append(asin)

    max_columns = max(len(row) for row in rows)
    for column_index in range(max_columns):
        for row in rows[1:]:
            if len(row) > column_index:
                add_asin(row[column_index])

    print(f"Loaded {len(asins)} unique ASINs from {source_name}")
    print(f"ASIN scan order: all used columns left to right ({max_columns} columns)")
    return asins


def read_asins_from_google_sheet():
    print(f"Reading ASINs from Google Sheet CSV: {ASIN_CSV_URL}")
    response = requests.get(ASIN_CSV_URL, timeout=45)
    response.raise_for_status()
    return asins_from_csv_text(response.text, "Google Sheet CSV")


def read_asins_from_local_file():
    if not ASIN_FILE.exists():
        raise FileNotFoundError("Missing asins.csv")
    return asins_from_csv_text(ASIN_FILE.read_text(encoding="utf-8"), "asins.csv")


def read_all_asins():
    try:
        return read_asins_from_google_sheet() if ASIN_CSV_URL else read_asins_from_local_file()
    except Exception as exc:
        if ASIN_CSV_URL:
            print(f"Could not read Google Sheet CSV: {exc}")
            print("Falling back to local asins.csv")
            return read_asins_from_local_file()
        raise


def load_json_file(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Could not read {path}; using fallback. Error: {exc}")
        return fallback


def load_scan_state():
    state = load_json_file(STATE_FILE, {"next_start_index": 0})
    if not isinstance(state, dict):
        return {"next_start_index": 0}
    if not isinstance(state.get("next_start_index"), int):
        state["next_start_index"] = 0
    return state


def save_scan_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_deal_memory():
    payload = load_json_file(MEMORY_FILE, {})
    if isinstance(payload, dict) and isinstance(payload.get("deals"), dict):
        return payload["deals"]
    if isinstance(payload, dict):
        return payload
    return {}


def save_deal_memory(memory):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(
        json.dumps({"updated_at": iso_now(), "deal_ttl_hours": DEAL_TTL_HOURS, "deals": memory}, indent=2),
        encoding="utf-8",
    )


def purge_expired_deals(memory):
    cutoff = utc_now() - timedelta(hours=DEAL_TTL_HOURS)
    kept = {}
    expired_count = 0
    for asin, deal in memory.items():
        posted_at = parse_iso_datetime(deal.get("posted_at") or deal.get("first_seen_at") or deal.get("checked_at"))
        if posted_at and posted_at > cutoff:
            kept[asin] = deal
        else:
            expired_count += 1
    if expired_count:
        print(f"Purged {expired_count} expired deals older than {DEAL_TTL_HOURS} hours")
    return kept, expired_count


def merge_deals_with_memory(memory, new_deals):
    now_iso = iso_now()
    expires_at = (utc_now() + timedelta(hours=DEAL_TTL_HOURS)).isoformat()
    added_count = 0
    updated_count = 0
    for deal in new_deals:
        asin = deal.get("asin")
        if not asin:
            continue
        previous = memory.get(asin, {})
        posted_at = previous.get("posted_at") or previous.get("first_seen_at") or now_iso
        merged = {
            **previous,
            **deal,
            "posted_at": posted_at,
            "first_seen_at": posted_at,
            "last_checked_at": now_iso,
            "expires_at": expires_at,
        }
        if asin in memory:
            updated_count += 1
        else:
            added_count += 1
        memory[asin] = merged
    return memory, added_count, updated_count


def select_asins_for_run(all_asins):
    total = len(all_asins)
    if total == 0:
        return [], {"next_start_index": 0}, 0, 0
    if SCAN_LIMIT_RAW in ("auto", "dynamic"):
        daily_buffer = 1 + (SCAN_LIMIT_BUFFER_PERCENT / 100)
        limit = int((total * daily_buffer + SCAN_RUNS_PER_DAY - 1) // SCAN_RUNS_PER_DAY)
        print(f"Auto scan limit: {limit} ASINs per run for {total} total ASINs")
    else:
        limit = int(SCAN_LIMIT_RAW)
    limit = min(limit if limit > 0 else total, total)

    state = load_scan_state()
    start_index = state.get("next_start_index", 0)
    if start_index >= total or start_index < 0:
        start_index = 0

    end_index = start_index + limit
    wrapped = end_index > total
    if wrapped:
        selected = all_asins[start_index:] + all_asins[: end_index % total]
        next_start_index = end_index % total
    else:
        selected = all_asins[start_index:end_index]
        next_start_index = 0 if end_index >= total else end_index

    new_state = {
        "next_start_index": next_start_index,
        "last_start_index": start_index,
        "last_end_index": next_start_index if wrapped else end_index,
        "last_scan_limit": limit,
        "last_total_asins": total,
        "last_wrapped": wrapped,
        "last_scan_at": iso_now(),
    }
    print(f"Rotating scan: total ASINs={total}, start row={start_index + 2}, count={len(selected)}, next start row={next_start_index + 2}")
    return selected, new_state, start_index, next_start_index


def fetch_keepa_batch(url, params, batch_number):
    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.get(url, params=params, timeout=60)
        if response.status_code == 429:
            wait_seconds = RATE_LIMIT_WAIT_SECONDS * attempt
            print(f"Keepa rate limit on batch {batch_number}. Waiting {wait_seconds} seconds before retry {attempt}/{MAX_RETRIES}...")
            time.sleep(wait_seconds)
            continue
        if response.status_code >= 400:
            print(f"Keepa error {response.status_code} on batch {batch_number}: {response.text[:500]}")
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"Keepa rate limit did not clear after {MAX_RETRIES} retries on batch {batch_number}")


def fetch_keepa_products(asins):
    if not KEEPA_API_KEY:
        raise RuntimeError("Missing KEEPA_API_KEY environment variable")
    url = "https://api.keepa.com/product"
    all_products = []
    for i in range(0, len(asins), BATCH_SIZE):
        batch = asins[i : i + BATCH_SIZE]
        batch_number = (i // BATCH_SIZE) + 1
        print(f"Fetching batch {batch_number}: {len(batch)} ASINs")
        params = {
            "key": KEEPA_API_KEY,
            "domain": DOMAIN_ID,
            "asin": ",".join(batch),
            "stats": 7,
            "history": 1,
        }
        if KEEPA_OFFERS_LIMIT > 0:
            params["offers"] = KEEPA_OFFERS_LIMIT
        payload = fetch_keepa_batch(url, params, batch_number)
        products = payload.get("products", [])
        if not products and KEEPA_OFFERS_LIMIT > 0:
            print(f"Keepa returned 0 products with offers={KEEPA_OFFERS_LIMIT} on batch {batch_number}; retrying batch without offers...")
            fallback_params = dict(params)
            fallback_params.pop("offers", None)
            payload = fetch_keepa_batch(url, fallback_params, batch_number)
            products = payload.get("products", [])
        all_products.extend(products)
        tokens_left = payload.get("tokensLeft")
        refill_in = payload.get("refillIn")
        if tokens_left is not None:
            print(f"Keepa tokens left after batch {batch_number}: {tokens_left}")
        if refill_in is not None:
            print(f"Keepa refill in: {refill_in} ms")
        if i + BATCH_SIZE < len(asins):
            print(f"Waiting {REQUEST_DELAY_SECONDS} seconds before next batch...")
            time.sleep(REQUEST_DELAY_SECONDS)
    return all_products


def extract_lightning_deal_items(payload):
    if not payload:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    items = []
    for key in ("lightningDeal", "lightningDeals", "lightning_deals", "deal", "deals", "value", "data", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            items.append(value)

    if payload.get("asin") or payload.get("ASIN"):
        items.append(payload)

    seen = set()
    unique_items = []
    for item in items:
        marker = json.dumps(item, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        unique_items.append(item)
    return unique_items


def parse_keepa_money_value(value):
    if value in (None, "", -1, 0):
        return None
    if isinstance(value, list):
        for item in reversed(value):
            parsed = parse_keepa_money_value(item)
            if parsed:
                return parsed
        return None
    try:
        numeric = float(value)
    except Exception:
        return None
    if numeric <= 0:
        return None
    if isinstance(value, int) or (isinstance(value, str) and "." not in value):
        return round(numeric / 100, 2)
    return round(numeric, 2)


def lightning_deal_price(item):
    for key in ("dealPrice", "lightningDealPrice", "currentPrice", "price", "deal_price"):
        price = parse_keepa_money_value(item.get(key))
        if price:
            return price
    return None


def lightning_deal_shipping_cents(item):
    for key in ("shipping", "shippingCost", "shippingPrice", "shipping_cents", "deliveryPrice"):
        value = item.get(key)
        if value in (None, "", -1):
            continue
        try:
            return int(float(value))
        except Exception:
            continue
    return None


def lightning_deal_time(item, *keys):
    for key in keys:
        value = item.get(key)
        if value in (None, "", -1, 0):
            continue
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                continue
        if isinstance(value, (int, float)):
            if value > 10_000_000_000:
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            if value > 1_000_000_000:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            return keepa_minutes_to_datetime(value)
    return None


def lightning_deal_is_active(item):
    state = str(item.get("dealState") or item.get("state") or item.get("status") or "").strip().upper()
    if state in {"AVAILABLE", "ACTIVE", "RUNNING", "LIVE"}:
        return True
    if state in {"UPCOMING", "EXPIRED", "ENDED", "CANCELED", "CANCELLED", "SOLDOUT", "SOLD_OUT"}:
        return False
    if item.get("isActive") is True:
        return True
    now = utc_now()
    starts_at = lightning_deal_time(item, "startTime", "start", "startsAt", "startDate")
    ends_at = lightning_deal_time(item, "endTime", "end", "endsAt", "endDate")
    if starts_at and ends_at:
        return starts_at <= now <= ends_at
    return False


def lightning_deal_has_free_shipping_signal(item):
    shipping_cents = lightning_deal_shipping_cents(item)
    if isinstance(shipping_cents, int) and shipping_cents > 0:
        return False
    if isinstance(shipping_cents, int) and shipping_cents == 0:
        return True
    return any(bool(item.get(key)) for key in (
        "isAmazon",
        "isFBA",
        "isPrime",
        "isPrimeEligible",
        "primeEligible",
        "isFulfilledByAmazon",
        "fulfilledByAmazon",
    ))


def fetch_keepa_lightning_deals(asins):
    if not KEEPA_LIGHTNING_DEALS_ENABLED or not asins:
        return {}
    if not KEEPA_API_KEY:
        return {}

    url = "https://api.keepa.com/lightningdeal"
    deals = {}
    skipped_shipping = 0
    started_at = time.monotonic()
    checked = 0

    for index, asin in enumerate(asins[:KEEPA_LIGHTNING_DEAL_SCAN_LIMIT], start=1):
        if KEEPA_LIGHTNING_DEAL_TIME_BUDGET_SECONDS and time.monotonic() - started_at >= KEEPA_LIGHTNING_DEAL_TIME_BUDGET_SECONDS:
            print(f"Stopping Lightning Deal checks after {checked} ASINs to keep the scan inside the workflow time budget")
            break
        asin = str(asin or "").upper()
        if not asin:
            continue
        checked += 1
        params = {"key": KEEPA_API_KEY, "domain": DOMAIN_ID, "asin": asin, "state": "AVAILABLE"}
        try:
            payload = fetch_keepa_batch(url, params, f"lightning-deal-{index}")
        except Exception as exc:
            print(f"Keepa Lightning Deal lookup failed for {asin}; continuing: {exc}")
            continue

        for item in extract_lightning_deal_items(payload):
            item_asin = str(item.get("asin") or item.get("ASIN") or item.get("productAsin") or asin).upper()
            if item_asin != asin:
                continue
            if not lightning_deal_is_active(item):
                continue
            price = lightning_deal_price(item)
            if not price:
                continue
            if not lightning_deal_has_free_shipping_signal(item):
                shipping_cents = lightning_deal_shipping_cents(item)
                if isinstance(shipping_cents, int) and shipping_cents > 0:
                    mark_visible_shipping_asin(asin, f"Lightning Deal shows separate shipping cost of ${shipping_cents / 100:.2f}", shipping_cents)
                skipped_shipping += 1
                continue
            item = dict(item)
            item["_current_price"] = price
            item["_shipping_cents"] = lightning_deal_shipping_cents(item)
            deals[asin] = item
            break

    print(f"Keepa Lightning Deals checked {checked} ASINs and matched scanned ASINs: {len(deals)}")
    if skipped_shipping:
        print(f"Skipped {skipped_shipping} Lightning Deals without free/Prime/FBA shipping evidence")
    return deals


def keepa_minutes_to_datetime(minutes):
    if not isinstance(minutes, (int, float)):
        return None
    return KEEPA_EPOCH + timedelta(minutes=minutes)


def normalize_keepa_csv(raw_csv):
    if not raw_csv:
        return []
    if isinstance(raw_csv, str):
        try:
            raw_csv = json.loads(raw_csv)
        except Exception:
            raw_csv = [part.strip() for part in raw_csv.split(",") if part.strip()]
    if not isinstance(raw_csv, list):
        return []
    values = []
    for item in raw_csv:
        try:
            values.append(int(float(item)))
        except Exception:
            continue
    return values


def decode_keepa_price_csv(raw_csv):
    values = normalize_keepa_csv(raw_csv)
    points = []
    for i in range(0, len(values) - 1, 2):
        dt = keepa_minutes_to_datetime(values[i])
        price_cents = values[i + 1]
        if not dt or price_cents is None or price_cents <= 0:
            continue
        points.append((dt, round(price_cents / 100, 2)))
    points.sort(key=lambda item: item[0])
    return points


def latest_price_from_points(points):
    if not points:
        return None
    return points[-1][1]


def window_stats_from_points(points, days):
    if not points:
        return None, None
    now = utc_now()
    start = now - timedelta(days=days)
    relevant = []
    carry_price = None
    carry_time = start

    for dt, price in points:
        if dt <= start:
            carry_price = price
            carry_time = start
        elif dt <= now:
            relevant.append((dt, price))

    segments = []
    if carry_price is not None:
        last_time = carry_time
        last_price = carry_price
    elif relevant:
        last_time = relevant[0][0]
        last_price = relevant[0][1]
        relevant = relevant[1:]
    else:
        last_time = points[-1][0]
        last_price = points[-1][1]

    for dt, price in relevant:
        duration = max(0, (dt - last_time).total_seconds())
        if duration > 0 and last_price and last_price > 0:
            segments.append((duration, last_price))
        last_time = dt
        last_price = price

    duration = max(0, (now - last_time).total_seconds())
    if duration > 0 and last_price and last_price > 0:
        segments.append((duration, last_price))

    if not segments:
        prices = [price for _, price in points if price and price > 0]
        if not prices:
            return None, None
        return round(sum(prices) / len(prices), 2), min(prices)

    total_seconds = sum(duration for duration, _ in segments)
    avg_price = sum(duration * price for duration, price in segments) / total_seconds if total_seconds else None
    min_price = min(price for _, price in segments)
    return round(avg_price, 2) if avg_price else None, round(min_price, 2)


def best_price_days_from_points(points, current_price):
    if not points or not current_price:
        return 0, None, None
    current_cents = int(round(current_price * 100))
    best_date = None
    best_price = None
    last_seen_date = None
    for dt, price in points:
        price_cents = int(round(price * 100))
        last_seen_date = dt
        if price_cents <= current_cents:
            best_date = dt
            best_price = price
    if not last_seen_date:
        return 0, None, None
    if not best_date:
        best_date = points[0][0]
    days = max(0, int((utc_now() - best_date).total_seconds() // 86400))
    return days, best_price, best_date.date().isoformat()


def best_price_days_for_track(product, track_index, current_price):
    csv_tracks = product.get("csv") or []
    if track_index >= len(csv_tracks) or not isinstance(csv_tracks[track_index], list):
        return 0, None, None
    points = decode_keepa_price_csv(csv_tracks[track_index])
    return best_price_days_from_points(points, current_price)


def prime_exclusive_offer_points(product):
    points = []
    offers = product.get("offers") or []
    if not isinstance(offers, list):
        return points
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        if not offer.get("isPrimeExcl"):
            continue
        prime_points = decode_keepa_price_csv(offer.get("primeExclCSV"))
        points.extend(prime_points)
    points.sort(key=lambda item: item[0])
    return points


def latest_offer_price_and_shipping(offer):
    values = normalize_keepa_csv(offer.get("offerCSV"))
    latest = None
    for i in range(0, len(values) - 2, 3):
        dt = keepa_minutes_to_datetime(values[i])
        price_cents = values[i + 1]
        shipping_cents = values[i + 2]
        if not dt or not isinstance(price_cents, int) or price_cents <= 0:
            continue
        latest = (dt, round(price_cents / 100, 2), shipping_cents)

    if latest:
        return latest

    price = keepa_to_dollars(offer.get("price"))
    shipping = offer.get("shipping")
    if price:
        return utc_now(), price, shipping if isinstance(shipping, int) else None
    return None, None, None


def offer_is_buy_box_candidate(offer):
    return any(bool(offer.get(key)) for key in (
        "isBuyBoxWinner",
        "isBuyBox",
        "buyBoxWinner",
        "is_buy_box_winner",
        "buy_box_winner",
    ))


def mark_visible_shipping_asin(asin, reason, shipping_cents=None):
    asin = str(asin or "").upper()
    if not ASIN_RE.fullmatch(asin):
        return
    VISIBLE_SHIPPING_ASINS.setdefault(asin, {
        "asin": asin,
        "reason": reason,
        "shipping_cents": shipping_cents,
    })


def visible_shipping_removal_reason(product):
    asin = str(product.get("asin") or "").upper()
    offers = product.get("offers") or []
    if not isinstance(offers, list):
        return None

    shipping_offers = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue

        last_seen, price, shipping_cents = latest_offer_price_and_shipping(offer)
        if not price or not isinstance(shipping_cents, int) or shipping_cents <= 0:
            continue

        is_amazon = bool(offer.get("isAmazon"))
        is_fba = bool(offer.get("isFBA"))
        is_prime = bool(offer.get("isPrime")) or is_amazon or is_fba
        is_buy_box = offer_is_buy_box_candidate(offer)
        if not (is_buy_box or is_amazon or is_fba or is_prime):
            continue

        shipping_offers.append({
            "is_buy_box": is_buy_box,
            "is_amazon": is_amazon,
            "is_fba": is_fba,
            "is_prime": is_prime,
            "shipping_cents": shipping_cents,
            "price": price,
            "last_seen": last_seen,
        })

    if not shipping_offers:
        return None

    shipping_offers.sort(key=lambda item: (
        0 if item["is_buy_box"] else 1,
        0 if item["is_amazon"] else 1,
        0 if item["is_fba"] else 1,
        0 if item["is_prime"] else 1,
        item["price"],
    ))
    selected = shipping_offers[0]
    label = "Buy Box" if selected["is_buy_box"] else "Amazon/FBA/Prime offer"
    reason = f"{label} shows separate shipping cost of ${selected['shipping_cents'] / 100:.2f}"
    return {"asin": asin, "reason": reason, "shipping_cents": selected["shipping_cents"]}


def preferred_keepa_offer_candidates(product):
    offers = product.get("offers") or []
    if not isinstance(offers, list):
        return []

    candidates = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue

        is_amazon = bool(offer.get("isAmazon"))
        is_fba = bool(offer.get("isFBA"))
        is_prime = bool(offer.get("isPrime")) or is_amazon or is_fba
        if not (is_amazon or is_fba or is_prime):
            continue

        last_seen, price, shipping_cents = latest_offer_price_and_shipping(offer)
        if not price:
            continue
        if isinstance(shipping_cents, int) and shipping_cents > 0:
            continue

        shipping_visible = isinstance(shipping_cents, int) and shipping_cents >= 0
        price_index = 0 if is_amazon else 10
        candidates.append({
            "offer": offer,
            "current_price": price,
            "last_seen": last_seen,
            "shipping_cents": shipping_cents,
            "shipping_visible": shipping_visible,
            "free_shipping_seen": shipping_visible and shipping_cents == 0,
            "is_amazon": is_amazon,
            "is_fba": is_fba,
            "is_prime": is_prime,
            "price_index": price_index,
            "price_type_label": "Amazon offer" if is_amazon else "FBA / Prime offer",
        })

    candidates.sort(key=lambda item: (
        0 if item["is_amazon"] else 1,
        0 if item["free_shipping_seen"] else 1,
        0 if item["is_fba"] else 1,
        0 if item["is_prime"] else 1,
        item["current_price"],
    ))
    return candidates


def parse_coupon_value(raw_value, current_price):
    if raw_value in (None, "", 0, -1):
        return None
    try:
        value = int(raw_value)
    except Exception:
        return None

    if value > 0:
        amount = round(value / 100, 2)
        return {
            "type": "amount",
            "value": value,
            "amount": amount,
            "label": f"${amount:g} coupon",
        }

    percent = abs(value)
    amount = round((current_price * percent) / 100, 2) if current_price else None
    return {
        "type": "percent",
        "value": value,
        "percent": percent,
        "amount": amount,
        "label": f"{percent:g}% coupon",
    }


def coupon_for_product(product, current_price):
    coupons = product.get("coupon")
    if not isinstance(coupons, list):
        return None

    labels = ["one-time", "subscribe & save"]
    for index, raw_value in enumerate(coupons[:2]):
        coupon = parse_coupon_value(raw_value, current_price)
        if coupon:
            coupon["kind"] = labels[index] if index < len(labels) else "coupon"
            if coupon.get("amount"):
                coupon["effective_price"] = round(max(0, current_price - coupon["amount"]), 2)
            return coupon
    return None


def apply_coupon_details(deal, product):
    coupon = coupon_for_product(product, deal.get("current_price"))
    if not coupon:
        return deal
    deal["coupon"] = coupon
    deal["coupon_label"] = coupon["label"]
    deal["after_coupon_price"] = coupon.get("effective_price")
    return deal


def build_track_presence_summary(products):
    summary = []
    for track in PRICE_TRACKS:
        current_count = 0
        avg30_count = 0
        lower_than_amazon_count = 0
        sample_asins = []
        for product in products:
            stats = product.get("stats") or {}
            current = price_from_stats_array(stats, "current", track["index"])
            avg30 = price_from_stats_array(stats, "avg30", track["index"])
            amazon_current = price_from_stats_array(stats, "current", 0)
            if current:
                current_count += 1
                if len(sample_asins) < 5:
                    sample_asins.append({"asin": product.get("asin"), "current_price": current, "amazon_current_price": amazon_current})
                if amazon_current and current < amazon_current:
                    lower_than_amazon_count += 1
            if avg30:
                avg30_count += 1
        summary.append({
            "price_type": track["type"],
            "label": track["label"],
            "keepa_price_index": track["index"],
            "products_with_current_price": current_count,
            "products_with_avg30_price": avg30_count,
            "products_lower_than_amazon_current": lower_than_amazon_count,
            "sample_current_prices": sample_asins,
        })

    prime_current_count = 0
    prime_lower_than_amazon_count = 0
    prime_sample_asins = []
    for product in products:
        points = prime_exclusive_offer_points(product)
        current = latest_price_from_points(points)
        amazon_current = price_from_stats_array(product.get("stats") or {}, "current", 0)
        if current:
            prime_current_count += 1
            if len(prime_sample_asins) < 10:
                prime_sample_asins.append({"asin": product.get("asin"), "current_price": current, "amazon_current_price": amazon_current})
            if amazon_current and current < amazon_current:
                prime_lower_than_amazon_count += 1
    summary.append({
        "price_type": "prime_exclusive_offer",
        "label": "New, Prime Exclusive",
        "keepa_source": "offers[].isPrimeExcl + primeExclCSV",
        "products_with_current_price": prime_current_count,
        "products_with_avg30_price": prime_current_count,
        "products_lower_than_amazon_current": prime_lower_than_amazon_count,
        "sample_current_prices": prime_sample_asins,
    })
    return summary


def raw_keepa_diagnostics(products):
    sample_products = []
    products_with_stats = 0
    products_with_csv = 0
    products_with_offers = 0
    products_with_prime_exclusive_offer = 0
    products_with_images_csv = 0

    for product in products:
        stats = product.get("stats") or {}
        csv_tracks = product.get("csv") or []
        offers = product.get("offers") or []
        images_csv = product.get("imagesCSV") or ""
        has_stats = isinstance(stats, dict) and bool(stats)
        has_csv = isinstance(csv_tracks, list) and any(isinstance(track, list) and track for track in csv_tracks)
        has_offers = isinstance(offers, list) and bool(offers)
        has_images_csv = bool(str(images_csv).strip())
        has_prime_exclusive = has_offers and any(
            isinstance(offer, dict) and offer.get("isPrimeExcl")
            for offer in offers
        )

        products_with_stats += 1 if has_stats else 0
        products_with_csv += 1 if has_csv else 0
        products_with_offers += 1 if has_offers else 0
        products_with_images_csv += 1 if has_images_csv else 0
        products_with_prime_exclusive_offer += 1 if has_prime_exclusive else 0

        if len(sample_products) < 8:
            current = stats.get("current") if isinstance(stats, dict) else []
            avg = stats.get("avg") if isinstance(stats, dict) else []
            avg30 = stats.get("avg30") if isinstance(stats, dict) else []
            sample_products.append({
                "asin": product.get("asin"),
                "has_stats": has_stats,
                "has_csv": has_csv,
                "has_offers": has_offers,
                "has_images_csv": has_images_csv,
                "has_prime_exclusive_offer": has_prime_exclusive,
                "raw_current_indexes": {
                    str(track["index"]): current[track["index"]] if isinstance(current, list) and len(current) > track["index"] else None
                    for track in PRICE_TRACKS
                },
                "raw_avg_indexes": {
                    str(track["index"]): avg[track["index"]] if isinstance(avg, list) and len(avg) > track["index"] else None
                    for track in PRICE_TRACKS
                },
                "raw_avg30_indexes": {
                    str(track["index"]): avg30[track["index"]] if isinstance(avg30, list) and len(avg30) > track["index"] else None
                    for track in PRICE_TRACKS
                },
                "csv_track_lengths": [
                    len(track) if isinstance(track, list) else 0
                    for track in (csv_tracks[:20] if isinstance(csv_tracks, list) else [])
                ],
                "offer_count": len(offers) if isinstance(offers, list) else 0,
            })

    diagnostics = {
        "products_returned": len(products),
        "products_with_stats": products_with_stats,
        "products_with_csv": products_with_csv,
        "products_with_offers": products_with_offers,
        "products_with_images_csv": products_with_images_csv,
        "products_with_prime_exclusive_offer": products_with_prime_exclusive_offer,
        "sample_products": sample_products,
    }
    print(f"Raw Keepa diagnostics: {json.dumps(diagnostics)}")
    return diagnostics


def qualification_for_prices(current_price, avg_7_price, avg_30_price, best_price_days):
    drop_percent = round(((avg_7_price - current_price) / avg_7_price) * 100, 1)
    drop_30_percent = round(((avg_30_price - current_price) / avg_30_price) * 100, 1)
    qualification_reasons = []
    if drop_30_percent >= 5:
        qualification_reasons.append("5%+ below 30-day average")
    if drop_percent >= 5 and drop_30_percent >= 5:
        qualification_reasons.append("5%+ below both 7-day and 30-day averages")
    if best_price_days >= 90:
        qualification_reasons.append("best price in 90+ days")
    if drop_30_percent < MIN_DROP_PERCENT and not qualification_reasons:
        return None
    if not qualification_reasons:
        return None
    return drop_percent, drop_30_percent, qualification_reasons


def base_deal(product, asin, title, current_price, avg_7_price, min_7_price, avg_30_price, drop_percent, drop_30_percent, qualification_reasons, source, price_type, price_type_label, amazon_current_price, best_price_days, previous_price, previous_date, keepa_price_index=None):
    checked_at = iso_now()
    deal = {
        "asin": asin,
        "title": title,
        "current_price": current_price,
        "avg_7_price": avg_7_price,
        "min_7_price": min_7_price,
        "avg_30_price": avg_30_price,
        "min_30_price": None,
        "drop_percent": drop_percent,
        "drop_30_percent": drop_30_percent,
        "price_stats_source": source,
        "image": get_product_image(product, asin),
        "image_candidates": get_product_image_candidates(product, asin),
        "amazon_url": f"https://www.amazon.com/dp/{asin}?tag={AMAZON_TAG}",
        "checked_at": checked_at,
        "last_checked_at": checked_at,
        "price_type": price_type,
        "price_type_label": price_type_label,
        "keepa_price_index": keepa_price_index,
        "amazon_current_price": amazon_current_price,
        "best_price_days": best_price_days,
        "best_price_message": f"best price in {best_price_days} days" if best_price_days else "",
        "best_price_previous_price": previous_price,
        "best_price_previous_date": previous_date,
        "qualification_reasons": qualification_reasons,
    }
    return apply_coupon_details(deal, product)


def build_deal_candidate(product, track):
    asin = product.get("asin")
    title = product.get("title") or asin
    stats = product.get("stats") or {}
    price_index = track["index"]

    current_price = price_from_stats_array(stats, "current", price_index)
    avg_7_price = price_from_stats_array(stats, "avg", price_index)
    min_7_price = price_from_stats_array(stats, "minInInterval", price_index)
    avg_30_price = price_from_stats_array(stats, "avg30", price_index)
    amazon_current_price = price_from_stats_array(stats, "current", 0)

    if not current_price or not avg_7_price or not min_7_price or not avg_30_price:
        return None
    if current_price >= avg_30_price:
        return None

    best_price_days, previous_price, previous_date = best_price_days_for_track(product, price_index, current_price)
    qualified = qualification_for_prices(current_price, avg_7_price, avg_30_price, best_price_days)
    if not qualified:
        return None
    drop_percent, drop_30_percent, qualification_reasons = qualified

    return base_deal(
        product, asin, title, current_price, avg_7_price, min_7_price, avg_30_price,
        drop_percent, drop_30_percent, qualification_reasons,
        f"keepa_stats_30_day_threshold_{track['source_suffix']}",
        track["type"], track["label"], amazon_current_price, best_price_days,
        previous_price, previous_date, price_index,
    )


def comparison_track_for_lightning_deal(product):
    stats = product.get("stats") or {}
    for price_index, label, price_type in (
        (0, "Amazon price", "amazon"),
        (10, "New FBA / Prime price", "new_fba_prime"),
    ):
        avg_7_price = price_from_stats_array(stats, "avg", price_index)
        min_7_price = price_from_stats_array(stats, "minInInterval", price_index)
        avg_30_price = price_from_stats_array(stats, "avg30", price_index)
        if avg_7_price and min_7_price and avg_30_price:
            return price_index, label, price_type, avg_7_price, min_7_price, avg_30_price
    return None


def build_lightning_deal_candidate(product, lightning_deal):
    if not lightning_deal:
        return None

    asin = product.get("asin")
    title = product.get("title") or asin
    current_price = lightning_deal.get("_current_price") or lightning_deal_price(lightning_deal)
    if not current_price:
        return None

    comparison = comparison_track_for_lightning_deal(product)
    if not comparison:
        return None
    price_index, comparison_label, comparison_type, avg_7_price, min_7_price, avg_30_price = comparison

    best_price_days, previous_price, previous_date = best_price_days_for_track(product, price_index, current_price)
    drop_percent = round(((avg_7_price - current_price) / avg_7_price) * 100, 1)
    drop_30_percent = round(((avg_30_price - current_price) / avg_30_price) * 100, 1)
    qualification_reasons = ["active Lightning Deal"]
    qualified = qualification_for_prices(current_price, avg_7_price, avg_30_price, best_price_days)
    if qualified:
        drop_percent, drop_30_percent, price_reasons = qualified
        qualification_reasons.extend(price_reasons)

    amazon_current_price = price_from_stats_array(product.get("stats") or {}, "current", 0)
    deal = base_deal(
        product, asin, title, current_price, avg_7_price, min_7_price, avg_30_price,
        drop_percent, drop_30_percent, qualification_reasons,
        f"keepa_lightningdeal_vs_{comparison_type}",
        "lightning_deal", f"Lightning Deal vs {comparison_label}", amazon_current_price, best_price_days,
        previous_price, previous_date, price_index,
    )
    deal["lightning_deal"] = {
        "deal_state": lightning_deal.get("dealState") or lightning_deal.get("state") or lightning_deal.get("status"),
        "shipping_cents": lightning_deal.get("_shipping_cents"),
        "is_prime": bool(lightning_deal.get("isPrime") or lightning_deal.get("isPrimeEligible") or lightning_deal.get("primeEligible")),
        "is_fba": bool(lightning_deal.get("isFBA") or lightning_deal.get("isFulfilledByAmazon") or lightning_deal.get("fulfilledByAmazon")),
        "is_amazon": bool(lightning_deal.get("isAmazon")),
        "starts_at": (lightning_deal_time(lightning_deal, "startTime", "start", "startsAt", "startDate") or None),
        "ends_at": (lightning_deal_time(lightning_deal, "endTime", "end", "endsAt", "endDate") or None),
    }
    if deal["lightning_deal"]["starts_at"]:
        deal["lightning_deal"]["starts_at"] = deal["lightning_deal"]["starts_at"].isoformat()
    if deal["lightning_deal"]["ends_at"]:
        deal["lightning_deal"]["ends_at"] = deal["lightning_deal"]["ends_at"].isoformat()
    return deal


def build_preferred_keepa_offer_candidate(product):
    asin = product.get("asin")
    title = product.get("title") or asin
    stats = product.get("stats") or {}

    for offer_candidate in preferred_keepa_offer_candidates(product):
        current_price = offer_candidate["current_price"]
        price_index = offer_candidate["price_index"]
        avg_7_price = price_from_stats_array(stats, "avg", price_index)
        min_7_price = price_from_stats_array(stats, "minInInterval", price_index)
        avg_30_price = price_from_stats_array(stats, "avg30", price_index)
        amazon_current_price = price_from_stats_array(stats, "current", 0)

        if not avg_7_price or not min_7_price or not avg_30_price:
            continue
        if current_price >= avg_30_price:
            continue

        best_price_days, previous_price, previous_date = best_price_days_for_track(product, price_index, current_price)
        qualified = qualification_for_prices(current_price, avg_7_price, avg_30_price, best_price_days)
        if not qualified:
            continue
        drop_percent, drop_30_percent, qualification_reasons = qualified

        deal = base_deal(
            product, asin, title, current_price, avg_7_price, min_7_price, avg_30_price,
            drop_percent, drop_30_percent, qualification_reasons,
            "keepa_preferred_offer_with_shipping_filter",
            "keepa_preferred_offer", offer_candidate["price_type_label"], amazon_current_price, best_price_days,
            previous_price, previous_date, price_index,
        )
        deal["keepa_offer"] = {
            "is_amazon": offer_candidate["is_amazon"],
            "is_fba": offer_candidate["is_fba"],
            "is_prime": offer_candidate["is_prime"],
            "shipping_cents": offer_candidate["shipping_cents"],
            "shipping_visible": offer_candidate["shipping_visible"],
            "free_shipping_seen": offer_candidate["free_shipping_seen"],
            "last_seen": offer_candidate["last_seen"].isoformat() if offer_candidate.get("last_seen") else None,
        }
        if offer_candidate["free_shipping_seen"]:
            deal["qualification_reasons"] = ["Keepa offer shows free shipping"] + deal["qualification_reasons"]
        return deal

    return None


def build_prime_exclusive_offer_candidate(product):
    asin = product.get("asin")
    title = product.get("title") or asin
    points = prime_exclusive_offer_points(product)
    current_price = latest_price_from_points(points)
    if not current_price:
        return None

    avg_7_price, min_7_price = window_stats_from_points(points, 7)
    avg_30_price, _ = window_stats_from_points(points, 30)
    amazon_current_price = price_from_stats_array(product.get("stats") or {}, "current", 0)
    if not avg_7_price or not min_7_price or not avg_30_price:
        return None
    if current_price >= avg_30_price:
        return None

    best_price_days, previous_price, previous_date = best_price_days_from_points(points, current_price)
    qualified = qualification_for_prices(current_price, avg_7_price, avg_30_price, best_price_days)
    if not qualified:
        return None
    drop_percent, drop_30_percent, qualification_reasons = qualified

    return base_deal(
        product, asin, title, current_price, avg_7_price, min_7_price, avg_30_price,
        drop_percent, drop_30_percent, qualification_reasons,
        "keepa_offers_prime_exclusive_csv",
        "prime_exclusive_offer", "New, Prime Exclusive", amazon_current_price, best_price_days,
        previous_price, previous_date, None,
    )


def build_live_buy_box_candidate(product, live_offer):
    if not live_offer:
        return None

    asin = product.get("asin")
    title = product.get("title") or asin
    stats = product.get("stats") or {}
    current_price = live_offer.get("current_price")
    if not current_price:
        return None
    if REQUIRE_PRIME_OR_AMAZON_PRICE_SOURCE and not live_offer_matches_prime_or_amazon_track(stats, current_price):
        return None

    comparison_tracks = [
        {"type": "live_buy_box", "label": "Live Buy Box price", "index": 18, "source_suffix": "buy_box"},
        {"type": "live_buy_box_new", "label": "Live Buy Box price vs New history", "index": 1, "source_suffix": "new"},
        {"type": "live_buy_box_amazon", "label": "Live Buy Box price vs Amazon history", "index": 0, "source_suffix": "amazon"},
    ]

    candidates = []
    for track in comparison_tracks:
        price_index = track["index"]
        avg_7_price = price_from_stats_array(stats, "avg", price_index)
        min_7_price = price_from_stats_array(stats, "minInInterval", price_index)
        avg_30_price = price_from_stats_array(stats, "avg30", price_index)
        keepa_current_price = price_from_stats_array(stats, "current", price_index)
        amazon_current_price = price_from_stats_array(stats, "current", 0) or current_price

        if not avg_7_price or not min_7_price or not avg_30_price:
            continue
        if current_price >= avg_7_price and current_price >= avg_30_price:
            continue
        if current_price >= avg_30_price:
            continue

        best_price_days, previous_price, previous_date = best_price_days_for_track(product, price_index, current_price)
        qualified = qualification_for_prices(current_price, avg_7_price, avg_30_price, best_price_days)
        if not qualified:
            continue
        drop_percent, drop_30_percent, qualification_reasons = qualified
        qualification_reasons = ["live Buy Box price"] + qualification_reasons

        candidate = base_deal(
            product, asin, title, current_price, avg_7_price, min_7_price, avg_30_price,
            drop_percent, drop_30_percent, qualification_reasons,
            f"creator_live_buy_box_vs_keepa_{track['source_suffix']}",
            track["type"], track["label"], amazon_current_price, best_price_days,
            previous_price, previous_date, price_index,
        )
        candidate["price"] = live_offer.get("price") or f"${current_price:,.2f}"
        candidate["currency"] = live_offer.get("currency") or "USD"
        candidate["availability"] = live_offer.get("availability") or ""
        candidate["shipping_status"] = live_offer.get("shipping_status") or {}
        candidate["keepa_current_price"] = keepa_current_price
        if keepa_current_price and current_price < keepa_current_price:
            candidate["live_price_lower_than_keepa"] = True
            candidate["live_price_savings_vs_keepa"] = round(keepa_current_price - current_price, 2)
            candidate["qualification_reasons"] = ["Amazon live price below Keepa"] + candidate["qualification_reasons"]
        candidates.append(candidate)

    return max(candidates, key=deal_rank) if candidates else None


def live_offer_matches_prime_or_amazon_track(stats, current_price):
    for price_index in (0, 10):
        track_price = price_from_stats_array(stats, "current", price_index)
        if prices_match(track_price, current_price):
            return True
    return False


def prices_match(left, right):
    if not left or not right:
        return False
    return abs(float(left) - float(right)) <= max(0.05, float(right) * 0.01)


def deal_rank(deal):
    price_type = deal.get("price_type")
    current = float(deal.get("current_price") or 0)
    amazon_current = float(deal.get("amazon_current_price") or current or 0)
    savings_vs_amazon = max(0, amazon_current - current)
    return (
        4 if price_type == "lightning_deal" else
        3 if str(price_type or "").startswith("live_buy_box") else
        3 if price_type == "keepa_preferred_offer" else
        2 if price_type == "prime_exclusive_offer" else 1 if price_type in NON_AMAZON_PRICE_TYPES else 0,
        savings_vs_amazon,
        float(deal.get("drop_30_percent") or 0),
        float(deal.get("drop_percent") or 0),
        int(deal.get("best_price_days") or 0),
    )


def build_deal(product, live_offer=None, require_live_offer=False, lightning_deal=None):
    candidates = []
    lightning_deal_candidate = build_lightning_deal_candidate(product, lightning_deal)
    if lightning_deal_candidate:
        candidates.append(lightning_deal_candidate)
    preferred_offer_candidate = build_preferred_keepa_offer_candidate(product)
    if preferred_offer_candidate:
        candidates.append(preferred_offer_candidate)
    prime_offer_candidate = build_prime_exclusive_offer_candidate(product)
    if prime_offer_candidate:
        candidates.append(prime_offer_candidate)
    for track in PRICE_TRACKS:
        if track["type"] not in QUALIFYING_PRICE_TRACK_TYPES:
            continue
        candidate = build_deal_candidate(product, track)
        if candidate:
            candidates.append(candidate)
    if not candidates:
        return None
    return max(candidates, key=deal_rank)


def keep_only_qualifying_price_types(memory):
    kept = {}
    removed = 0
    for asin, deal in memory.items():
        if deal.get("price_type") in QUALIFYING_DEAL_PRICE_TYPES:
            kept[asin] = deal
        else:
            removed += 1
    return kept, removed


def normalize_commission(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    number = raw[:-1].strip() if raw.endswith("%") else raw
    try:
        numeric = float(number)
        return f"{int(numeric)}%" if numeric.is_integer() else f"{numeric:g}%"
    except ValueError:
        return raw


def commission_number(value):
    try:
        return float(normalize_commission(value).replace("%", ""))
    except ValueError:
        return 0.0


def campaign_from_row(row, today):
    start_date = parse_campaign_date(row.get("Campaign Start Date"))
    end_date = parse_campaign_date(row.get("Campaign End Date"))
    return {
        "campaign_id": str(row.get("Campaign Id", "")).strip(),
        "campaign_name": str(row.get("Campaign Name", "")).strip(),
        "campaign_brand": str(row.get("Brand Name", "")).strip(),
        "commission_rate": normalize_commission(row.get("Commission Rate", "")),
        "campaign_start_date": str(row.get("Campaign Start Date", "")).strip(),
        "campaign_end_date": str(row.get("Campaign End Date", "")).strip(),
        "recommended": str(row.get("Recommended", "")).strip().lower() == "true",
        "active": (start_date is None or start_date <= today) and (end_date is None or end_date >= today),
    }


def campaign_rank(campaign):
    return (
        1 if campaign.get("recommended") else 0,
        commission_number(campaign.get("commission_rate")),
        campaign.get("campaign_end_date") or "",
    )


def github_api_get(url, timeout=45):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def creator_connection_file_commit_date(path):
    api_url = (
        f"https://api.github.com/repos/{CREATOR_CONNECTIONS_REPO}/commits"
        f"?path={path}&sha={CREATOR_CONNECTIONS_REF}&per_page=1"
    )
    try:
        commits = github_api_get(api_url).json()
        commit = commits[0].get("commit", {}) if commits else {}
        author = commit.get("author", {})
        return parse_iso_datetime(author.get("date"))
    except Exception as exc:
        print(f"Could not read Creator Connections commit date for {path}: {exc}")
        return None


def creator_connection_file_urls():
    # Both dashboards consume the same checkout of uploaded CSV parts.
    shared = Path(CREATOR_CONNECTIONS_PATH)
    if shared.is_dir():
        files = [{"name": path.name, "local_path": path} for path in sorted(shared.glob("*.csv"), reverse=True)]
        return files, {"files_available": len(files), "files_selected": len(files),
                       "latest_csv_file": files[0]["name"] if files else "",
                       "latest_csv_updated_at": "", "source": "shared_uploads"}
    if CREATOR_CONNECTIONS_MAX_FILES <= 0:
        return [], {
            "files_available": 0,
            "files_selected": 0,
            "latest_csv_file": "",
            "latest_csv_updated_at": "",
            "max_files": CREATOR_CONNECTIONS_MAX_FILES,
            "max_file_age_days": CREATOR_CONNECTIONS_MAX_FILE_AGE_DAYS,
        }

    api_url = (
        f"https://api.github.com/repos/{CREATOR_CONNECTIONS_REPO}/contents/"
        f"{CREATOR_CONNECTIONS_PATH}?ref={CREATOR_CONNECTIONS_REF}"
    )
    files = github_api_get(api_url).json()
    csv_files = []
    for item in files:
        if item.get("type") == "file" and item.get("name", "").lower().endswith(".csv"):
            if item.get("download_url") and item.get("path"):
                csv_files.append({
                    "name": item.get("name", ""),
                    "path": item["path"],
                    "download_url": item["download_url"],
                    "updated_at": creator_connection_file_commit_date(item["path"]),
                })

    csv_files.sort(key=lambda item: item.get("updated_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    latest_updated_at = csv_files[0].get("updated_at") if csv_files else None
    cutoff = utc_now() - timedelta(days=CREATOR_CONNECTIONS_MAX_FILE_AGE_DAYS)
    selected = [
        item for item in csv_files
        if item.get("updated_at") is None or item.get("updated_at") >= cutoff
    ][:CREATOR_CONNECTIONS_MAX_FILES]
    return selected, {
        "files_available": len(csv_files),
        "files_selected": len(selected),
        "latest_csv_file": csv_files[0].get("name") if csv_files else "",
        "latest_csv_updated_at": latest_updated_at.isoformat() if latest_updated_at else "",
        "max_files": CREATOR_CONNECTIONS_MAX_FILES,
        "max_file_age_days": CREATOR_CONNECTIONS_MAX_FILE_AGE_DAYS,
    }


def find_creator_campaigns_for_asins(target_asins):
    target_asins = {str(asin).strip().upper() for asin in target_asins if asin}
    if not target_asins:
        return {}, {"files_scanned": 0, "rows_scanned": 0, "asins_matched": 0}

    try:
        files, file_stats = creator_connection_file_urls()
    except Exception as exc:
        print(f"Could not list Creator Connections files: {exc}")
        return {}, {"files_scanned": 0, "rows_scanned": 0, "asins_matched": 0}

    today = utc_now().date()
    matches = {}
    files_scanned = 0
    rows_scanned = 0
    print(f"Checking Creator campaign status for {len(target_asins)} dashboard ASINs...")

    for item in files:
        files_scanned += 1
        try:
            with (open(item["local_path"], encoding="utf-8-sig", newline="") if "local_path" in item
                  else requests.get(item["download_url"], stream=True, timeout=180)) as response:
                if "local_path" in item:
                    reader = csv.DictReader(response)
                else:
                    response.raise_for_status()
                    reader = csv.DictReader(response.iter_lines(decode_unicode=True))
                for row in reader:
                    rows_scanned += 1
                    row_asins = set(ASIN_RE.findall(str(row.get("ASIN List", "")).upper()))
                    relevant_asins = target_asins.intersection(row_asins)
                    if not relevant_asins:
                        continue
                    campaign = campaign_from_row(row, today)
                    if not campaign.get("active"):
                        continue
                    for asin in relevant_asins:
                        existing = matches.get(asin)
                        if not existing or campaign_rank(campaign) > campaign_rank(existing):
                            matches[asin] = campaign
        except Exception as exc:
            print(f"Could not scan Creator Connections file {item.get('name', '')}: {exc}")

    print(f"Creator Connections: matched {len(matches)} ASINs from {files_scanned} files and {rows_scanned} rows")
    return matches, {
        "files_scanned": files_scanned,
        "rows_scanned": rows_scanned,
        "asins_matched": len(matches),
        **file_stats,
    }


def apply_creator_campaigns(memory, campaign_by_asin):
    matched = 0
    for asin, deal in memory.items():
        campaign = campaign_by_asin.get(str(asin).upper())
        if campaign:
            deal["has_creator_campaign"] = True
            deal["creator_campaign"] = campaign
            deal["creator_commission_rate"] = campaign.get("commission_rate", "")
            matched += 1
        else:
            deal.pop("has_creator_campaign", None)
            deal.pop("creator_campaign", None)
            deal.pop("creator_commission_rate", None)
    return matched


def call_asin_tools(action, params, timeout=60):
    if not ASIN_TOOLS_WEB_APP_URL:
        raise RuntimeError("Missing ASIN_TOOLS_WEB_APP_URL")
    response = requests.get(
        ASIN_TOOLS_WEB_APP_URL,
        params={"action": action, **params},
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        text = response.text.strip()
        match = re.search(r"\((\{.*\})\)\s*;?\s*$", text)
        if match:
            return json.loads(match.group(1))
        raise


def remove_asins_from_source_sheet(asins, reason):
    asins = sorted({str(asin or "").upper() for asin in asins if ASIN_RE.fullmatch(str(asin or "").upper())})
    if not REMOVE_VISIBLE_SHIPPING_ASINS or not asins:
        return {"attempted": False, "requested": len(asins), "removed": 0, "not_found": []}
    if not ASIN_CSV_URL:
        print("Visible-shipping ASIN removal skipped because the scan is using local asins.csv")
        return {"attempted": False, "requested": len(asins), "removed": 0, "not_found": asins}

    removed = []
    not_found = []
    failed = []
    print(f"Removing {len(asins)} ASINs from source sheet because {reason}")

    for index in range(0, len(asins), 50):
        chunk = asins[index:index + 50]
        try:
            result = call_asin_tools("removeAsins", {"asins": "\n".join(chunk)})
        except Exception as exc:
            print(f"Bulk remove failed for {len(chunk)} ASINs; trying one at a time: {exc}")
            result = {"ok": False, "error": "bulk_failed"}

        if result and result.get("ok"):
            removed.extend(result.get("removed_asins") or chunk)
            not_found.extend(result.get("not_found") or [])
            continue

        if result and not re.search(r"unknown action|bulk_failed", str(result.get("error", "")), re.I):
            print(f"Bulk remove returned an error: {result.get('error')}")

        for asin in chunk:
            try:
                single = call_asin_tools("removeAsin", {"asin": asin})
                if single and single.get("ok"):
                    removed.append(asin)
                else:
                    not_found.append(asin)
            except Exception as exc:
                print(f"Could not remove {asin} from source sheet: {exc}")
                failed.append(asin)

    return {
        "attempted": True,
        "requested": len(asins),
        "removed": len(set(removed)),
        "removed_asins": sorted(set(removed)),
        "not_found": sorted(set(not_found)),
        "failed": sorted(set(failed)),
    }


def main():
    print("Starting Keepa price scan with stats fallback price tracks...")

    all_asins = read_all_asins()
    asins, new_state, start_index, next_start_index = select_asins_for_run(all_asins)

    print(f"Loaded {len(all_asins)} total ASINs from source")
    print(f"Loaded {len(asins)} ASINs for this run")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Request delay seconds: {REQUEST_DELAY_SECONDS}")
    print(f"Scan windows per day: {SCAN_RUNS_PER_DAY}")
    print(f"ASIN source: {'Google Sheet CSV' if ASIN_CSV_URL else 'local asins.csv'}")

    memory = load_deal_memory()
    memory, expired_count = purge_expired_deals(memory)
    campaign_target_asins = set(asins) | set(memory.keys())
    campaign_by_asin, campaign_stats = find_creator_campaigns_for_asins(campaign_target_asins)

    products = fetch_keepa_products(asins)
    print(f"Fetched {len(products)} products from Keepa")
    if asins and not products:
        raise RuntimeError("Keepa returned zero products for a non-empty scan window; keeping the previous dashboard data instead of writing an empty file.")
    price_track_scan_summary = build_track_presence_summary(products)
    keepa_raw_diagnostics = raw_keepa_diagnostics(products)
    live_offer_by_asin = {}
    live_prime_offer_count = sum(1 for offer in live_offer_by_asin.values() if offer and not offer.get("shipping_rejected"))
    lightning_deal_by_asin = fetch_keepa_lightning_deals(asins)
    print("Amazon Creators API live Buy Box pricing is not used for deal qualification")
    print("Deal qualification uses Keepa Lightning Deals, preferred offers, Amazon, New FBA/Prime, and Prime Exclusive offer pricing")
    require_live_offer = False

    scan_deals = []
    skipped = 0
    missing_images = 0
    non_amazon_scan_deals = 0
    prime_exclusive_scan_deals = 0
    lightning_deal_scan_deals = 0

    for product in products:
        try:
            asin = str(product.get("asin") or "").upper()
            if asin in VISIBLE_SHIPPING_ASINS:
                continue
            shipping_removal = visible_shipping_removal_reason(product)
            if shipping_removal:
                mark_visible_shipping_asin(
                    asin,
                    shipping_removal["reason"],
                    shipping_removal.get("shipping_cents"),
                )
                continue
            deal = build_deal(
                product,
                live_offer_by_asin.get(asin),
                require_live_offer=require_live_offer,
                lightning_deal=lightning_deal_by_asin.get(asin),
            )
        except Exception as exc:
            skipped += 1
            print(f"Skipped {product.get('asin', 'unknown ASIN')}: {exc}")
            continue
        if deal:
            if not deal.get("image"):
                missing_images += 1
                print(f"No image found for {deal.get('asin')}")
            if deal.get("price_type") in NON_AMAZON_PRICE_TYPES:
                non_amazon_scan_deals += 1
            if deal.get("price_type") == "prime_exclusive_offer":
                prime_exclusive_scan_deals += 1
            if deal.get("price_type") == "lightning_deal":
                lightning_deal_scan_deals += 1
            scan_deals.append(deal)

    visible_shipping_asins = sorted(VISIBLE_SHIPPING_ASINS)
    shipping_removal_summary = remove_asins_from_source_sheet(
        visible_shipping_asins,
        "Keepa showed a visible separate shipping cost",
    )
    if visible_shipping_asins:
        before_memory_shipping_purge = len(memory)
        memory = {
            asin: deal for asin, deal in memory.items()
            if str(asin).upper() not in VISIBLE_SHIPPING_ASINS
        }
        purged_shipping_memory_count = before_memory_shipping_purge - len(memory)
        if purged_shipping_memory_count:
            print(f"Removed {purged_shipping_memory_count} visible-shipping deals from 24-hour memory")
    else:
        purged_shipping_memory_count = 0

    memory, added_count, updated_count = merge_deals_with_memory(memory, scan_deals)
    memory, disallowed_price_type_removed_count = keep_only_qualifying_price_types(memory)
    creator_campaign_deal_count = apply_creator_campaigns(memory, campaign_by_asin)
    all_deals = list(memory.values())
    creator_image_update_count = enrich_deal_images_with_creator_api(all_deals)
    all_deals.sort(key=lambda item: item.get("posted_at") or item.get("checked_at") or "", reverse=True)
    non_amazon_active_deals = sum(1 for deal in all_deals if deal.get("price_type") in NON_AMAZON_PRICE_TYPES)
    prime_exclusive_active_deals = sum(1 for deal in all_deals if deal.get("price_type") == "prime_exclusive_offer")
    lightning_deal_active_deals = sum(1 for deal in all_deals if deal.get("price_type") == "lightning_deal")

    output_payload = {
        "updated_at": iso_now(),
        "asin_source": "Google Sheet CSV" if ASIN_CSV_URL else "local asins.csv",
        "comparison_window": "Deals qualify when active Keepa Lightning Deals are found with Prime/FBA/free-shipping evidence, or when Keepa preferred offers, Amazon, New FBA/Prime, or Prime Exclusive offer pricing is at least 5% below the 30-day average, at least 5% below both the 7-day and 30-day averages, or at a best price in 90+ days",
        "deal_ttl_hours": DEAL_TTL_HOURS,
        "deal_count": len(all_deals),
        "new_scan_deal_count": len(scan_deals),
        "new_deals_added": added_count,
        "existing_deals_updated": updated_count,
        "expired_deals_removed": expired_count,
        "disallowed_price_type_deals_removed": disallowed_price_type_removed_count,
        "skipped_count": skipped,
        "missing_image_count": missing_images,
        "non_amazon_scan_deal_count": non_amazon_scan_deals,
        "non_amazon_active_deal_count": non_amazon_active_deals,
        "prime_exclusive_scan_deal_count": prime_exclusive_scan_deals,
        "prime_exclusive_active_deal_count": prime_exclusive_active_deals,
        "lightning_deal_scan_deal_count": lightning_deal_scan_deals,
        "lightning_deal_active_deal_count": lightning_deal_active_deals,
        "lightning_deal_matches_in_scan": len(lightning_deal_by_asin),
        "visible_shipping_asins_removed_from_source": shipping_removal_summary,
        "visible_shipping_asins_detected": list(VISIBLE_SHIPPING_ASINS.values()),
        "visible_shipping_memory_deals_removed": purged_shipping_memory_count,
        "creator_campaign_deal_count": creator_campaign_deal_count,
        "creator_image_update_count": creator_image_update_count,
        "creator_connections": {
            "repo": CREATOR_CONNECTIONS_REPO,
            "path": CREATOR_CONNECTIONS_PATH,
            "ref": CREATOR_CONNECTIONS_REF,
            **campaign_stats,
        },
        "scan_window": {
            "total_asins": len(all_asins),
            "start_index": start_index,
            "start_sheet_row": start_index + 2,
            "next_start_index": next_start_index,
            "next_start_sheet_row": next_start_index + 2,
            "scan_count": len(asins),
        },
        "settings": {
            "min_drop_percent": MIN_DROP_PERCENT,
            "batch_size": BATCH_SIZE,
            "request_delay_seconds": REQUEST_DELAY_SECONDS,
            "rate_limit_wait_seconds": RATE_LIMIT_WAIT_SECONDS,
            "scan_limit": SCAN_LIMIT_RAW,
            "scan_runs_per_day": SCAN_RUNS_PER_DAY,
            "scan_limit_buffer_percent": SCAN_LIMIT_BUFFER_PERCENT,
            "deal_ttl_hours": DEAL_TTL_HOURS,
            "keepa_stats_days": 7,
            "keepa_product_params": {"stats": 7, "history": 1, "offers": KEEPA_OFFERS_LIMIT},
            "keepa_lightning_deals_enabled": KEEPA_LIGHTNING_DEALS_ENABLED,
            "live_buy_box_source": "disabled_for_deal_qualification",
            "requires_live_offer": require_live_offer,
            "shipping_filter_mode": "prefer Keepa isAmazon/isFBA/isPrime offers, require Prime/FBA/free-shipping evidence for Lightning Deals, and skip visible shipping above $0",
            "requires_prime_or_amazon_price_source": True,
            "qualifying_price_types": ["lightning_deal", "keepa_preferred_offer", "amazon", "new_fba_prime", "prime_exclusive_offer"],
            "live_offer_debug_sample_limit": LIVE_OFFER_DEBUG_SAMPLE_LIMIT,
            "keepa_price_tracks": [
                {"price_type": track["type"], "label": track["label"], "keepa_price_index": track["index"]}
                for track in PRICE_TRACKS
            ],
            "prime_exclusive_source": "offers[].isPrimeExcl + primeExclCSV",
            "lightning_deal_source": "Keepa /lightningdeal endpoint",
        },
        "price_track_scan_summary": price_track_scan_summary,
        "live_buy_box_scan_summary": {
            "products_with_live_prime_or_free_shipping_buy_box_price": live_prime_offer_count,
            "products_with_missing_shipping_evidence": sum(1 for offer in live_offer_by_asin.values() if offer and not offer.get("shipping_rejected") and not (offer.get("shipping_status") or {}).get("has_shipping_evidence")),
            "products_rejected_for_shipping": 0,
            "live_buy_box_qualifies_against_keepa_history": sum(1 for deal in scan_deals if str(deal.get("price_type") or "").startswith("live_buy_box")),
        },
        "live_offer_debug_samples": LIVE_OFFER_DEBUG_SAMPLES,
        "keepa_raw_diagnostics": keepa_raw_diagnostics,
        "deals": all_deals,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    save_deal_memory(memory)
    save_scan_state(new_state)

    print(f"Found {len(scan_deals)} price drops in this scan")
    print(f"Non-Amazon price source deals in this scan: {non_amazon_scan_deals}")
    print(f"Prime Exclusive offer deals in this scan: {prime_exclusive_scan_deals}")
    print(f"Prime Exclusive active deals: {prime_exclusive_active_deals}")
    print(f"Lightning Deal matches in this scan: {len(lightning_deal_by_asin)}")
    print(f"Lightning Deal active deals: {lightning_deal_active_deals}")
    print(f"Added {added_count} new deals and updated {updated_count} existing deals")
    print(f"Marked {creator_campaign_deal_count} active deals with Creator campaign commission data")
    print(f"Saved {len(all_deals)} active 24-hour deals to {OUTPUT_FILE}")
    print(f"Saved deal memory to {MEMORY_FILE}")
    print(f"Saved next scan start index {new_state['next_start_index']} to {STATE_FILE}")
    if skipped:
        print(f"Skipped {skipped} products because their Keepa data format was incomplete or unexpected")
    if missing_images:
        print(f"{missing_images} deals did not include an image from Keepa or Amazon fallback")


if __name__ == "__main__":
    main()
