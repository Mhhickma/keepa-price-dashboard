"""Bounded Creator Connections scanner. No API calls occur during import."""
import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

UTC = timezone.utc
EPOCH = datetime(2011, 1, 1, tzinfo=UTC).timestamp()
ASIN = re.compile(r"(?<![A-Z0-9])[A-Z0-9]{10}(?![A-Z0-9])")
DAY = 86400


def number(value):
    try:
        n = float(str(value).replace(",", "").replace("$", "").replace("%", "").strip())
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def date(value):
    text = str(value or "").strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                pass
    return None


def normalized(row):
    return {re.sub(r"[^a-z0-9]", "", k.lower()): v for k, v in row.items() if k}


def campaign(row):
    r = normalized(row)
    def get(*keys):
        return next((r[k] for k in keys if r.get(k) not in (None, "")), "")
    commission = number(get("commissionrate", "commission", "commissionpercentage"))
    # A bare 0.10 is a fraction; explicit 0.10% remains 0.10 percent.
    if commission is not None and commission <= 1 and "%" not in str(get("commissionrate", "commission", "commissionpercentage")):
        commission *= 100
    result = dict(
        campaign_id=get("campaignid"), name=get("campaignname", "name"),
        brand=get("brandname", "brand"), commission=commission,
        start=date(get("campaignstartdate", "startdate")), end=date(get("campaignenddate", "enddate")),
        budget=number(get("campaignbudget", "budget")),
        budget_remaining=number(get("budgetremaining", "remainingbudget", "campaignbudgetremaining")),
        available_slots=number(get("availablecreatorslots", "availableslots", "creatorslotsavailable", "availablecreator slots")),
        total_slots=number(get("totalcreatorslots", "totalslots", "creatorslots")),
        recommended=str(get("recommended")).lower() in ("true", "yes", "1"),
        status=str(get("campaignstatus", "status")).strip().lower(),
    )
    identity = result["campaign_id"] or json.dumps([result[k] for k in ("name", "brand", "start", "end")])
    result["key"] = hashlib.sha256(identity.encode()).hexdigest()
    return result


def active(c, today):
    return bool(c["start"] and c["end"] and c["start"] <= today <= c["end"]
                and c["status"] not in {"inactive", "paused", "cancelled", "canceled", "ended", "expired", "closed", "upcoming"}
                and c["commission"] is not None and c["commission"] >= 10)


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
      PRAGMA journal_mode=DELETE;
      CREATE TABLE IF NOT EXISTS sources(path TEXT PRIMARY KEY, digest TEXT, rownum INTEGER, complete INTEGER, priority TEXT);
      CREATE TABLE IF NOT EXISTS campaigns(source TEXT, id TEXT, payload TEXT, PRIMARY KEY(source,id));
      CREATE TABLE IF NOT EXISTS links(source TEXT, id TEXT, asin TEXT, PRIMARY KEY(source,id,asin));
      CREATE INDEX IF NOT EXISTS links_asin ON links(asin);
      CREATE INDEX IF NOT EXISTS links_id ON links(id);
      CREATE TABLE IF NOT EXISTS cache(asin TEXT PRIMARY KEY, fetched REAL, payload TEXT);
      CREATE TABLE IF NOT EXISTS selected(asin TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS failures(asin TEXT PRIMARY KEY, code TEXT, attempts INTEGER, last_at REAL);
    """)
    return db


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def import_sources(db, paths, deadline):
    """Checkpoint logical CSV rows; never split multiline records or collect all ASINs."""
    csv.field_size_limit(16 * 1024 * 1024)
    names = {str(p.as_posix()) for p in paths}
    with db:
        for (old,) in db.execute("SELECT path FROM sources").fetchall():
            if old not in names:
                for table in ("campaigns", "links"):
                    db.execute(f"DELETE FROM {table} WHERE source=?", (old,))
                db.execute("DELETE FROM sources WHERE path=?", (old,))
    for path in paths:
        name, digest = path.as_posix(), digest_file(path)
        source = db.execute("SELECT digest,rownum,complete FROM sources WHERE path=?", (name,)).fetchone()
        if source and source[0] == digest and source[2]:
            continue
        if not source or source[0] != digest:
            with db:
                for table in ("campaigns", "links"):
                    db.execute(f"DELETE FROM {table} WHERE source=?", (name,))
                # New upload filenames contain an ISO-sortable UTC timestamp. Legacy paths sort first.
                db.execute("INSERT OR REPLACE INTO sources VALUES(?,?,0,0,?)", (name, digest, path.name if re.match(r"\d{8}T", path.name) else "0" + path.name))
            source = (digest, 0, 0)
        rownum = 0
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = normalized({k: "" for k in reader.fieldnames or []})
            if "asinlist" not in headers:
                raise ValueError("CSV requires an ASIN List column")
            for rownum, row in enumerate(reader, 1):
                if rownum <= source[1]:
                    continue
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("Malformed CSV row; import stopped without querying Keepa")
                c = campaign(row)
                db.execute("INSERT OR REPLACE INTO campaigns VALUES(?,?,?)", (name, c["key"], json.dumps(c)))
                for match in ASIN.finditer(str(normalized(row).get("asinlist", "")).upper()):
                    db.execute("INSERT OR IGNORE INTO links VALUES(?,?,?)", (name, c["key"], match.group()))
                if rownum % 500 == 0:
                    db.execute("UPDATE sources SET rownum=? WHERE path=?", (rownum, name))
                    db.commit()
                    if time.monotonic() >= deadline:
                        return False
        db.execute("UPDATE sources SET rownum=?,complete=1 WHERE path=?", (rownum, name))
        db.commit()
    return True


def eligible_index(db, today):
    db.executescript("DROP TABLE IF EXISTS temp.eligible; CREATE TEMP TABLE eligible(id TEXT PRIMARY KEY,source TEXT,payload TEXT);")
    # Repeated snapshots of one campaign are not separate campaigns. Latest file wins,
    # including a newer cancellation or commission reduction.
    query = """SELECT id,source,payload FROM (
      SELECT c.*, ROW_NUMBER() OVER(PARTITION BY c.id ORDER BY s.priority DESC,s.path DESC) AS n
      FROM campaigns c JOIN sources s ON c.source=s.path WHERE s.complete=1) WHERE n=1"""
    for cid, source, payload in db.execute(query):
        if active(json.loads(payload), today):
            db.execute("INSERT INTO eligible VALUES(?,?,?)", (cid, source, payload))


def campaigns_for(db, asin):
    return [json.loads(row[0]) for row in db.execute("""SELECT e.payload FROM eligible e
      JOIN links l ON l.id=e.id WHERE l.asin=? GROUP BY e.id ORDER BY e.id""", (asin,))]


def points(values):
    if not isinstance(values, list) or len(values) % 2:
        return []
    result = {}
    for i in range(0, len(values), 2):
        stamp, value = number(values[i]), number(values[i+1])
        if stamp is not None:
            result[EPOCH + stamp * 60] = value
    return sorted(result.items())


def average(values, now, days):
    """Time-weighted step history. Require an anchor and full valid window coverage."""
    history = [(t, v) for t, v in points(values) if t <= now]
    start = now - days * DAY
    anchors = [(t, v) for t, v in history if t <= start]
    if not anchors or anchors[-1][1] is None:
        return None
    prev, value, total = start, anchors[-1][1], 0
    for stamp, next_value in history:
        if stamp <= start:
            continue
        if value is None:
            return None
        total += (stamp - prev) * value
        prev, value = stamp, next_value
    if value is None:
        return None
    return (total + (now - prev) * value) / (days * DAY)


def apparel(p):
    nodes = [str(n.get("name", "")).lower() for n in (p.get("categoryTree") or []) if isinstance(n, dict)]
    detail = " ".join(nodes[1:] + [str(p.get(k) or "").lower() for k in ("productGroup", "type", "itemTypeKeyword", "title")])
    product_type = str(p.get("type") or "").lower().replace("_", " ")
    if re.search(r"\b(ppe|personal protective|safety (?:vest|glasses|goggles|equipment|gloves|boots)|protective (?:coverall|clothing|gear|gloves)|welding (?:gloves|helmet|apron|jacket)|work gloves|respirator|hard hat|tool belt|fall protection|tactical vest)\b", detail) and product_type not in {"shirt", "t shirt", "sweatshirt"}:
        return False
    # Do not reject the broad Clothing, Shoes & Jewelry root or words like 'outdoors'.
    if any(n in {"clothing", "apparel", "shirts", "pants", "dresses", "underwear", "sleepwear", "socks", "jackets & coats", "fashion hoodies & sweatshirts"} for n in nodes):
        return True
    group = str(p.get("websiteDisplayGroupName") or p.get("binding") or "").lower()
    if group in {"apparel", "clothing"} or product_type in {"shirt", "pants", "dress", "skirt", "coat", "jacket", "sock", "underwear", "sweatshirt"}:
        return True
    broad_clothing = bool(nodes and "clothing" in nodes[0])
    if (not nodes or broad_clothing) and re.search(r"\b(t-shirt|tshirt|sweatshirt|hoodie|lingerie|pajamas|leggings|blouse|swimsuit)\b", detail):
        return True
    if broad_clothing and len(nodes) == 1:
        return None
    return False if nodes or group else None


def evaluate(p, campaigns, now, fetched, ttl_hours=24):
    campaigns = [c for c in campaigns if active(c, datetime.fromtimestamp(now, UTC).date().isoformat())]
    def stat(index):
        values = (p.get("stats") or {}).get("current") or []
        return number(values[index]) if len(values) > index else None
    current = number(p.get("monthlySold"))
    last_sold = number(p.get("lastSoldUpdate"))
    sold_fresh = last_sold is not None and 0 <= now - (EPOCH + last_sold * 60) <= 30 * DAY
    avg = average(p.get("monthlySoldHistory"), now, 90) if sold_fresh else None
    growth = (current / avg - 1) * 100 if current is not None and avg is not None and avg > 0 else None
    videos = p.get("videos")
    # Live refresh must have succeeded. Missing metadata is not zero videos.
    video_known = isinstance(videos, list) and p.get("offersSuccessful") is True
    unique = {}
    if video_known:
        for video in videos:
            if not isinstance(video, dict) or not video.get("url") or not video.get("creator"):
                video_known = False
                break
            unique[video["url"]] = video
    merchant = any(v["creator"] in {"Seller", "Merchant", "Brand", "Vendor"} for v in unique.values()) if video_known else None
    total = len(unique) if video_known else None
    influencers = sum(v["creator"] == "Influencer" for v in unique.values()) if video_known else None
    community = sum(v["creator"] in {"Influencer", "Customer", "ThirdParty"} for v in unique.values()) if video_known else None
    rank = stat(3)
    csvs = p.get("csv") or []
    ranks = csvs[3] if len(csvs) > 3 else None
    refs = points(p.get("salesRankReferenceHistory"))
    ref = p.get("salesRankReference")
    comparable = not any(t >= now - 90 * DAY and v != ref for t, v in refs)
    bsr30 = average(ranks, now, 30) if comparable else None
    bsr90 = average(ranks, now, 90) if comparable else None
    best = max(campaigns, key=lambda c: (c["commission"], c["budget_remaining"] or 0, c["available_slots"] or 0)) if campaigns else {}
    price_value, price_source = None, None
    for index, label in ((18, "Buy Box including shipping"), (0, "Amazon"), (1, "New offer")):
        candidate = stat(index)
        if candidate is not None and candidate > 0:
            price_value, price_source = candidate / 100, label
            break
    clothing = apparel(p)
    checks = {
        "active_campaign_and_commission": bool(campaigns), "not_apparel": clothing is False,
        "merchant_video": merchant is True, "fewer_than_5_videos": total is not None and total < 5,
        "sales_growth": current is not None and avg is not None and avg > 0 and current * 10 >= 11 * avg,
        "fresh_cache": 0 <= now - fetched <= ttl_hours * 3600,
        "standard_product": p.get("productType") == 0,
    }
    result = dict(asin=p.get("asin"), title=p.get("title"), brand=p.get("brand") or best.get("brand"),
        category=" > ".join(n.get("name", "") for n in (p.get("categoryTree") or []) if isinstance(n, dict)),
        browse_nodes=p.get("categoryTree"), price=price_value, price_source=price_source,
        commission=best.get("commission"), monthly_sold=current, monthly_sold_90=avg, growth=growth,
        sales_trend="unavailable" if growth is None else "pass" if checks["sales_growth"] else "fail",
        bsr=rank, bsr30=(1-rank/bsr30)*100 if rank and bsr30 else None,
        bsr90=(1-rank/bsr90)*100 if rank and bsr90 else None,
        merchant_video=merchant, total_videos=total, influencer_videos=influencers, community_videos=community,
        video_scope="Keepa observed carousel/community videos; not a guaranteed Amazon-wide total",
        budget_remaining=best.get("budget_remaining"), available_slots=best.get("available_slots"),
        campaigns=campaigns, qualifying_campaign_count=len(campaigns), recommended=any(c["recommended"] for c in campaigns),
        checks=checks, qualified=all(checks.values()), fetched_at=fetched,
        failed_filters=[k for k, v in checks.items() if not v], score=None,
        valid_until=min([fetched+ttl_hours*3600] + [datetime.fromisoformat(c["end"]).replace(tzinfo=UTC).timestamp()+DAY for c in campaigns]))
    if result["qualified"]:
        result["score"] = round(25*min(math.log10(1+(current or 0))/4, 1) + 25*min(max(growth or 0, 0)/100, 1)
            + 20*(5-total)/5 + 15*min(best["commission"]/30, 1) + 5*min(price_value or 0, 200)/200
            + 5*min(best.get("budget_remaining") or 0, 10000)/10000
            + 5*min(best.get("available_slots") or 0, 100)/100, 2)
    return result


class Keepa:
    def __init__(self, key, deadline, budget, session=None):
        self.key, self.deadline, self.budget = key, deadline, budget
        self.session = session or requests.Session()
        self.consumed, self.reserved, self.balance, self.refill = 0, 0, None, None
        self.usage_unknown = False

    def fetch(self, asins):
        for attempt in range(5):
            # offers=20 costs up to two pages at six tokens/page per ASIN.
            reserve = len(asins) * 12
            if self.reserved + reserve > self.budget or time.monotonic() + 65 >= self.deadline:
                return None, "paused_budget_or_time"
            self.reserved += reserve
            wait = 0
            try:
                response = self.session.get("https://api.keepa.com/product", params={
                    "key": self.key, "domain": 1, "asin": ",".join(asins), "history": 1,
                    "stats": 90, "videos": 1, "offers": 20, "only-live-offers": 1,
                }, timeout=(10, 50))
                payload = response.json()
                used = number(payload.get("tokensConsumed"))
                if used is not None:
                    self.consumed += used
                else:
                    self.usage_unknown = True
                self.balance = payload.get("tokensLeft", self.balance)
                self.refill = payload.get("refillRate", self.refill)
                if response.status_code == 200 and not payload.get("error") and isinstance(payload.get("products"), list):
                    return payload["products"], None
                if response.status_code not in (429, 500, 502, 503, 504):
                    return None, f"http_{response.status_code}"
                wait = max(number(response.headers.get("Retry-After")) or 0, (number(payload.get("refillIn")) or 0)/1000)
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                # Never stringify requests exceptions: they can include the API key URL.
                self.usage_unknown = True
            wait = max(wait, 2 ** (attempt + 1) + random.random())
            if time.monotonic() + wait + 65 >= self.deadline:
                return None, "paused_backoff"
            time.sleep(wait)
        return None, "retry_exhausted"


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def export(db, output, args, api, phase):
    now = time.time()
    eligible_index(db, datetime.now(UTC).date().isoformat())
    counts = {"evaluated": 0, "qualified": 0, "unavailable_sales_trend": 0}
    # The browser loads at most 10k compact rows; full detail remains in the checkpoint.
    rows, pages = [], []
    for asin, fetched, payload in db.execute("SELECT c.asin,c.fetched,c.payload FROM cache c JOIN selected s ON c.asin=s.asin ORDER BY c.asin"):
        result = evaluate(json.loads(payload), campaigns_for(db, asin), now, fetched, args.cache_hours)
        counts["evaluated"] += 1
        counts["qualified"] += int(result["qualified"])
        counts["unavailable_sales_trend"] += int(result["sales_trend"] == "unavailable")
        # Cap public export explicitly; bounded runs do not imply unbounded browser work.
        if len(rows) >= 10000 or phase == "import_paused":
            continue
        details = result.pop("campaigns")
        result["detail_file"] = f"details/{asin}.json"
        atomic_json(output / result["detail_file"], {"campaigns": details})
        rows.append(result)
    for i in range(0, len(rows), 250):
        name = f"page-{i//250:04}.json"
        atomic_json(output/name, rows[i:i+250])
        pages.append(name)
    failures = [dict(asin=a, code=c, attempts=n, last_at=t) for a,c,n,t in db.execute("SELECT * FROM failures LIMIT 10000")]
    atomic_json(output/"failures.json", failures)
    status = dict(updated_at=datetime.now(UTC).isoformat(), phase=phase, pages=pages, counts=counts,
        selected=db.execute("SELECT COUNT(*) FROM selected").fetchone()[0], limit=args.limit, batch_size=args.batch_size,
        tokens_consumed=api.consumed if api and not api.usage_unknown else None, tokens_reserved=api.reserved if api else 0,
        tokens_left=api.balance if api else None, refill_rate=api.refill if api else None,
        token_budget=args.token_budget, cache_hours=args.cache_hours, exported=len(rows),
        truncated=counts["evaluated"] > len(rows), failed=db.execute("SELECT COUNT(*) FROM failures").fetchone()[0])
    atomic_json(output/"status.json", status)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/creator-connections")
    parser.add_argument("--state", default=".influencer-state/checkpoint.sqlite")
    parser.add_argument("--output", default="data/influencer")
    parser.add_argument("--limit", type=int, default=100, help="Total unique ASIN cohort across resumes, not per batch")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--token-budget", type=int, default=1500)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--cache-hours", type=int, default=24)
    parser.add_argument("--offline", action="store_true", help="Import/export only; never call Keepa")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 10000 or not 1 <= args.batch_size <= 100 or args.token_budget < 12 or args.seconds < 1 or not 1 <= args.cache_hours <= 168:
        parser.error("limit 1..10000; batch 1..100; budget >=12; seconds >=1; cache hours 1..168 required")
    paths = sorted(Path(args.input).glob("*.csv"))
    db = connect(Path(args.state))
    api, phase = None, "no_input"
    try:
        if not paths:
            # Remove cached campaign memberships when the authoritative source set is empty.
            import_sources(db, [], time.monotonic())
            return export(db, Path(args.output), args, api, phase)
        deadline = time.monotonic() + args.seconds
        if not import_sources(db, paths, deadline):
            phase = "import_paused"
            return export(db, Path(args.output), args, api, phase)
        eligible_index(db, datetime.now(UTC).date().isoformat())
        count = db.execute("SELECT COUNT(*) FROM selected").fetchone()[0]
        if args.limit < count:
            raise ValueError("Limit is below the existing cohort; restore original limit or explicitly reset checkpoint")
        db.execute("""INSERT OR IGNORE INTO selected SELECT DISTINCT l.asin FROM links l
          JOIN eligible e ON e.id=l.id WHERE l.asin NOT IN (SELECT asin FROM selected)
          ORDER BY l.asin LIMIT ?""", (args.limit-count,))
        db.commit()
        phase = "offline" if args.offline else "complete"
        if not args.offline:
            key = os.getenv("KEEPA_API_KEY")
            if not key:
                raise ValueError("Missing KEEPA_API_KEY environment variable")
            api = Keepa(key, deadline, args.token_budget)
            cutoff = time.time() - args.cache_hours * 3600
            cursor = db.execute("""SELECT s.asin FROM selected s LEFT JOIN cache c ON c.asin=s.asin
              WHERE (c.asin IS NULL OR c.fetched<?) AND EXISTS
              (SELECT 1 FROM links l JOIN eligible e ON e.id=l.id WHERE l.asin=s.asin)
              ORDER BY s.asin""", (cutoff,))
            while True:
                batch = [row[0] for row in cursor.fetchmany(args.batch_size)]
                if not batch:
                    break
                products, error = api.fetch(batch)
                if error and error.startswith("paused"):
                    phase = error
                    break
                found = {p.get("asin"): p for p in products or [] if isinstance(p, dict) and p.get("asin") in batch}
                with db:
                    for asin in batch:
                        if asin in found:
                            # Store only product data, never envelope/request/key.
                            db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (asin, time.time(), json.dumps(found[asin])))
                            db.execute("DELETE FROM failures WHERE asin=?", (asin,))
                        else:
                            db.execute("""INSERT INTO failures VALUES(?,?,1,?) ON CONFLICT(asin) DO UPDATE SET
                              code=excluded.code,attempts=attempts+1,last_at=excluded.last_at""", (asin, error or "product_missing", time.time()))
                            phase = "completed_with_failures"
                print(json.dumps({"batch_size": len(batch), "tokens_consumed": api.consumed if not api.usage_unknown else None, "tokens_left": api.balance}))
                if error in {"http_401", "http_402", "http_403"}:
                    phase = error
                    break
        return export(db, Path(args.output), args, api, phase)
    except Exception:
        db.rollback()
        # Export no qualifying rows while an import is inconsistent.
        atomic_json(Path(args.output)/"status.json", {"phase": "error", "pages": [], "updated_at": datetime.now(UTC).isoformat()})
        raise
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Pipeline stopped ({type(exc).__name__}); checkpoint retained. No request details logged.")
        raise SystemExit(1)
