"""Daily ingestion of return airfares from Perth (PER) to 43 major cities.

Uses the Travelpayouts Aviasales Data API (v3 prices_for_dates), which serves
the cheapest fares cached from real Aviasales user searches in the last 48
hours. For each destination we first ask for exact dates (departing 30 days
from today, returning 7 days later). If the cache has nothing for those exact
dates, we fall back to the whole departure month and mark the row "ok_flex"
so strict and flexible observations stay distinguishable.

Because every Aviasales website keeps its own cache per market, the month
fallback walks through several markets (au, us, gb, ru) and keeps the first
hit. The market that supplied each price is recorded in the row.

One tidy row per destination per day is appended to data/prices.csv and the
raw API responses are archived under data/raw/<snapshot_date>/.

Environment variables:
    TRAVELPAYOUTS_TOKEN   API token from the Travelpayouts profile (required)

The run only fails when nothing at all could be fetched, so one bad
destination never kills the daily run.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
ROUTES_FILE = ROOT / "config" / "routes.csv"
PRICES_FILE = ROOT / "data" / "prices.csv"
RAW_DIR = ROOT / "data" / "raw"

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
ORIGIN = "PER"
CURRENCY = "aud"
DAYS_AHEAD = 30          # target departure is 30 days from snapshot date
STAY_NIGHTS = 7          # target return is 7 days after departure
LIMIT = 30               # offers per request, cheapest first
MARKETS = ["au", "us", "gb", "ru"]   # caches to try, in order
REQUEST_PAUSE_SECONDS = 0.6
MAX_RETRIES = 3

FIELDNAMES = [
    "snapshot_date",
    "origin",
    "destination",
    "city",
    "country",
    "region",
    "departure_date",
    "return_date",
    "currency",
    "price_total",
    "carrier",
    "outbound_stops",
    "return_stops",
    "outbound_duration_min",
    "return_duration_min",
    "offers_returned",
    "market",
    "status",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")


def get_token() -> str:
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        log.error("TRAVELPAYOUTS_TOKEN is not set")
        sys.exit(1)
    return token


def query_prices(
    session: requests.Session,
    token: str,
    destination: str,
    departure_at: str,
    return_at: str | None,
    market: str | None = None,
) -> dict | None:
    """Call prices_for_dates with retries. Returns parsed JSON or None."""
    params = {
        "origin": ORIGIN,
        "destination": destination,
        "departure_at": departure_at,
        "one_way": "false",
        "direct": "false",
        "sorting": "price",
        "currency": CURRENCY,
        "limit": LIMIT,
    }
    if return_at:
        params["return_at"] = return_at
    if market:
        params["market"] = market
    headers = {"X-Access-Token": token, "Accept-Encoding": "gzip, deflate"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(API_URL, params=params, headers=headers, timeout=60)
            if resp.status_code == 429:
                wait = 2**attempt
                log.warning("%s rate limited, retrying in %ss", destination, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success", False):
                log.warning("%s API error: %s", destination, payload.get("error"))
                return None
            return payload
        except requests.RequestException as exc:
            log.warning("%s attempt %s failed: %s", destination, attempt, exc)
            time.sleep(2**attempt)
    return None


def cheapest_offer_row(payload: dict) -> dict | None:
    """Pick the cheapest ticket from a prices_for_dates payload and flatten it."""
    offers = payload.get("data") or []
    priced = [o for o in offers if o.get("price") is not None]
    if not priced:
        return None
    best = min(priced, key=lambda o: float(o["price"]))
    departure = (best.get("departure_at") or "")[:10]
    return_at = (best.get("return_at") or "")[:10]
    return {
        "departure_date": departure,
        "return_date": return_at,
        "currency": CURRENCY.upper(),
        "price_total": float(best["price"]),
        "carrier": best.get("airline", ""),
        "outbound_stops": best.get("transfers", ""),
        "return_stops": best.get("return_transfers", ""),
        "outbound_duration_min": best.get("duration_to", ""),
        "return_duration_min": best.get("duration_back", ""),
        "offers_returned": len(priced),
    }


def load_routes() -> list[dict]:
    with open(ROUTES_FILE, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def migrate_schema_if_needed() -> None:
    """Rewrite prices.csv when the column set changed (adds blank new columns)."""
    if not PRICES_FILE.exists():
        return
    with open(PRICES_FILE, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames == FIELDNAMES:
            return
        old_rows = list(reader)
    log.info("Migrating prices.csv to the new column set")
    with open(PRICES_FILE, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in old_rows:
            writer.writerow({key: row.get(key, "") for key in FIELDNAMES})


def append_rows(rows: list[dict]) -> None:
    PRICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    migrate_schema_if_needed()
    is_new = not PRICES_FILE.exists()
    with open(PRICES_FILE, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def archive_raw(snapshot_date: str, destination: str, payloads: dict) -> None:
    day_dir = RAW_DIR / snapshot_date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{ORIGIN}-{destination}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payloads, fh, separators=(",", ":"))


def main() -> None:
    perth_now = datetime.now(ZoneInfo("Australia/Perth"))
    snapshot_date = perth_now.date().isoformat()
    departure = (perth_now.date() + timedelta(days=DAYS_AHEAD)).isoformat()
    return_ = (perth_now.date() + timedelta(days=DAYS_AHEAD + STAY_NIGHTS)).isoformat()
    departure_month = departure[:7]
    log.info("Snapshot %s | departure %s | return %s", snapshot_date, departure, return_)

    routes = load_routes()
    session = requests.Session()
    token = get_token()

    rows: list[dict] = []
    errors = 0
    for route in routes:
        dest = route["iata"].strip().upper()
        base = {
            "snapshot_date": snapshot_date,
            "origin": ORIGIN,
            "destination": dest,
            "city": route["city"],
            "country": route["country"],
            "region": route["region"],
            "departure_date": departure,
            "return_date": return_,
            "currency": CURRENCY.upper(),
        }

        attempts: dict[str, dict | None] = {}

        # 1. Exact dates in the default market for the origin.
        exact = query_prices(session, token, dest, departure, return_)
        attempts["exact"] = exact
        flat = cheapest_offer_row(exact) if exact else None
        status, market = "ok", "default"

        # 2. Whole departure month, walking through the market caches.
        if flat is None:
            status = "ok_flex"
            for mkt in MARKETS:
                time.sleep(REQUEST_PAUSE_SECONDS)
                fallback = query_prices(session, token, dest, departure_month, None, mkt)
                attempts[f"month_{mkt}"] = fallback
                flat = cheapest_offer_row(fallback) if fallback else None
                if flat is not None:
                    market = mkt
                    break

        if all(v is None for v in attempts.values()):
            errors += 1
            rows.append({**base, "status": "error"})
            log.error("%s: every request failed after retries", dest)
        elif flat is None:
            rows.append({**base, "status": "no_data"})
            log.info("%s: nothing in any market cache", dest)
        else:
            rows.append({**base, **flat, "market": market, "status": status})
            log.info("%s: %s %s (%s, market %s)",
                     dest, flat["price_total"], flat["currency"], status, market)

        archive_raw(snapshot_date, dest, attempts)
        time.sleep(REQUEST_PAUSE_SECONDS)

    append_rows(rows)
    ok = sum(1 for r in rows if r["status"] in ("ok", "ok_flex"))
    no_data = sum(1 for r in rows if r["status"] == "no_data")
    log.info("Done: %s priced, %s no_data, %s errors out of %s routes",
             ok, no_data, errors, len(rows))
    if ok == 0 and errors > 0:
        log.error("Nothing was fetched at all, marking the run as failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
