#!/usr/bin/env python3
"""Fetch CZ aFRR/mFRR balancing capacity clearing prices from ENTSO-E.

Usage:
    Backfill:  python scripts/fetch_entsoe.py --from 2024-01-01 --to 2026-02-11
    Daily:     python scripts/fetch_entsoe.py  (fetches yesterday + today)
    API key:   --api-key KEY  or env ENTSOE_API_KEY
"""

import argparse
import csv
import io
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_URL = "https://web-api.tp.entsoe.eu/api"

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data" / "entsoe"
AFRR_DIR = DATA_DIR / "afrr"
MFRR_DIR = DATA_DIR / "mfrr"

DELAY_BETWEEN_REQUESTS = 2.0  # seconds — ENTSO-E allows 400/min, we stay safe

# Process types for each product
PRODUCTS = {
    "afrr": {"processType": "A51", "dir": None},  # dirs set in ensure_dirs
    "mfrr": {"processType": "A47", "dir": None},
}

# Direction mapping
DIRECTION_MAP = {"A01": "up", "A02": "down", "A03": "both"}

# PSR type mapping
PSR_TYPE_MAP = {"A03": "mixed", "A04": "generation", "A05": "load"}

# Resolution to number of blocks per day
RESOLUTION_BLOCKS = {"PT60M": 24, "PT4H": 6, "PT15M": 96}

CSV_HEADER = ["date", "block", "block_start", "direction", "psr_type", "price_eur_mw", "volume_mw"]


def ensure_dirs():
    AFRR_DIR.mkdir(parents=True, exist_ok=True)
    MFRR_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCTS["afrr"]["dir"] = AFRR_DIR
    PRODUCTS["mfrr"]["dir"] = MFRR_DIR


def load_existing_dates(csv_path: Path) -> set:
    """Return set of date strings already present in a CSV file."""
    dates = set()
    if not csv_path.exists():
        return dates
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return dates
        for row in reader:
            if row:
                dates.add(row[0])
    return dates


def write_csv(csv_path: Path, rows: list):
    """Append rows to CSV. Create file with header if it doesn't exist."""
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def cet_to_utc_str(d: date) -> tuple:
    """Convert a CET delivery date to ENTSO-E UTC period strings.

    CET midnight = 23:00 UTC previous day (winter) or 22:00 UTC (summer).
    Returns (periodStart, periodEnd) in YYYYMMDDHHmm format.
    """
    # Use CET offset: check if date falls in DST (CEST = UTC+2) or not (CET = UTC+1)
    # DST in Europe: last Sunday of March to last Sunday of October
    year = d.year

    # Find last Sunday of March
    mar31 = date(year, 3, 31)
    dst_start = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)

    # Find last Sunday of October
    oct31 = date(year, 10, 31)
    dst_end = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)

    if dst_start <= d < dst_end:
        # CEST: UTC+2, so CET midnight = 22:00 UTC previous day
        utc_start = datetime(d.year, d.month, d.day, 0, 0) - timedelta(hours=2)
    else:
        # CET: UTC+1, so CET midnight = 23:00 UTC previous day
        utc_start = datetime(d.year, d.month, d.day, 0, 0) - timedelta(hours=1)

    utc_end = utc_start + timedelta(hours=24)

    return (utc_start.strftime("%Y%m%d%H%M"), utc_end.strftime("%Y%m%d%H%M"))


def fetch_xml(api_key: str, process_type: str, period_start: str, period_end: str, retries: int = 1) -> str | None:
    """Fetch ENTSO-E API and return decompressed XML string, or None on error."""
    params = (
        f"documentType=A81"
        f"&businessType=B95"
        f"&Type_MarketAgreement.Type=A01"
        f"&controlArea_Domain=10YCZ-CEPS-----N"
        f"&processType={process_type}"
        f"&periodStart={period_start}"
        f"&periodEnd={period_end}"
        f"&securityToken={api_key}"
    )
    url = f"{BASE_URL}?{params}"

    for attempt in range(1 + retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "AlgoEnergy-DataCollector/1.0",
                    "Accept": "application/xml, application/zip",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read()

                if "zip" in content_type or "octet" in content_type:
                    # ZIP-compressed XML
                    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                        names = zf.namelist()
                        if not names:
                            print(f"    [ERROR] Empty ZIP archive.")
                            return None
                        return zf.read(names[0]).decode("utf-8")
                else:
                    # Plain XML (some responses aren't zipped)
                    text = raw.decode("utf-8")
                    # Check for error responses
                    if "<Reason>" in text and "No matching data" in text:
                        return None
                    return text

        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                # 409 = no data available
                return None
            if attempt < retries:
                print(f"    [WARN] HTTP {exc.code} for {process_type}. Retrying...")
                time.sleep(3)
            else:
                print(f"    [ERROR] HTTP {exc.code} for {process_type}: {exc}. Skipping.")
                return None
        except (urllib.error.URLError, OSError) as exc:
            if attempt < retries:
                print(f"    [WARN] Attempt {attempt + 1} failed: {exc}. Retrying...")
                time.sleep(3)
            else:
                print(f"    [ERROR] Failed to fetch: {exc}. Skipping.")
                return None
    return None


def is_4h_blocks(points: list) -> bool:
    """Check if sparse positions fall on 4-hour boundaries (1,5,9,13,17,21)."""
    return all((pos - 1) % 4 == 0 for pos, _, _ in points)


def parse_xml(xml_str: str, delivery_date: str) -> list:
    """Parse ENTSO-E Balancing XML into CSV rows.

    Filters:
    - Only standard_MarketProduct (skips specific/non-standard contracts)
    - Keeps: single-point series (flat daily price) → 1 row
    - Keeps: 4h-block series (positions at 4h boundaries) → 6 rows
    - Skips: hourly bid series (individual hour positions)

    Returns list of [date, block, block_start, direction, psr_type, price, volume] rows.
    """
    root = ET.fromstring(xml_str)
    rows = []

    for ts in root:
        if not ts.tag.endswith("TimeSeries"):
            continue

        # Extract metadata — namespace-agnostic
        direction_code = ""
        psr_code = ""
        has_standard = False
        has_original = False
        for child in ts:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "flowDirection.direction":
                direction_code = child.text or ""
            elif tag == "mktPSRType.psrType":
                psr_code = child.text or ""
            elif tag == "standard_MarketProduct.marketProductType":
                has_standard = True
            elif tag == "original_MarketProduct.marketProductType":
                has_original = True

        # Skip specific/non-standard contracts
        if has_original and not has_standard:
            continue

        direction = DIRECTION_MAP.get(direction_code, direction_code.lower())
        psr_type = PSR_TYPE_MAP.get(psr_code, psr_code.lower())

        # Find Period element
        for period_el in ts:
            if not period_el.tag.endswith("Period"):
                continue

            resolution = ""
            points = []
            for pel in period_el:
                ptag = pel.tag.split("}")[-1] if "}" in pel.tag else pel.tag
                if ptag == "resolution":
                    resolution = pel.text or ""
                elif ptag == "Point":
                    pos = None
                    qty = ""
                    price = ""
                    for field in pel:
                        ftag = field.tag.split("}")[-1] if "}" in field.tag else field.tag
                        if ftag == "position":
                            pos = int(field.text)
                        elif ftag == "quantity":
                            qty = field.text or ""
                        elif ftag == "procurement_Price.amount":
                            price = field.text or ""
                    if pos is not None:
                        points.append((pos, qty, price))

            if not resolution or not points:
                continue

            points.sort(key=lambda p: p[0])

            # Single point = flat daily value → 1 row
            if len(points) == 1:
                qty, price = points[0][1], points[0][2]
                rows.append([delivery_date, 0, "00:00", direction, psr_type, price, qty])
                continue

            # 4h blocks within PT60M (positions at 1,5,9,13,17,21) → 6 rows
            if resolution == "PT60M" and is_4h_blocks(points):
                for pos, qty, price in points:
                    block_idx = (pos - 1) // 4  # 0-5
                    hour = block_idx * 4
                    block_start = f"{hour:02d}:00"
                    rows.append([delivery_date, block_idx, block_start, direction, psr_type, price, qty])
                continue

            # Native PT4H resolution → 6 rows
            if resolution == "PT4H":
                for pos, qty, price in points:
                    block_idx = pos - 1  # 0-5
                    hour = block_idx * 4
                    block_start = f"{hour:02d}:00"
                    rows.append([delivery_date, block_idx, block_start, direction, psr_type, price, qty])
                continue

            # Anything else (hourly bids) → skip

    return rows


def fetch_product_date(api_key: str, product: str, d: date, existing_dates: set) -> tuple:
    """Fetch one product for one date. Returns (rows, fetched_from_api)."""
    date_str = d.isoformat()
    if date_str in existing_dates:
        return [], False

    process_type = PRODUCTS[product]["processType"]
    period_start, period_end = cet_to_utc_str(d)

    xml_str = fetch_xml(api_key, process_type, period_start, period_end)
    if xml_str is None:
        return [], True  # API was called but no data

    rows = parse_xml(xml_str, date_str)
    return rows, True


def date_range(start: date, end: date):
    """Yield dates from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description="Fetch ENTSO-E balancing capacity data.")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--api-key", dest="api_key", help="ENTSO-E API key (or env ENTSOE_API_KEY)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ENTSOE_API_KEY")
    if not api_key:
        print("ERROR: No API key. Use --api-key or set ENTSOE_API_KEY env var.")
        sys.exit(1)

    ensure_dirs()

    if args.from_date and args.to_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        end = datetime.strptime(args.to_date, "%Y-%m-%d").date()
        print(f"Backfill mode: {start} to {end}")
    else:
        today = date.today()
        yesterday = today - timedelta(days=1)
        start = yesterday
        end = today
        print(f"Daily mode: {start} to {end}")

    dates = list(date_range(start, end))
    total = len(dates)

    for product in PRODUCTS:
        product_dir = PRODUCTS[product]["dir"]
        print(f"\n=== {product.upper()} ===")

        years = sorted(set(d.year for d in dates))
        existing_by_year = {}
        for year in years:
            existing_by_year[year] = load_existing_dates(product_dir / f"{year}.csv")

        fetched = 0
        skipped = 0
        api_calls = 0

        for i, d in enumerate(dates):
            date_str = d.isoformat()
            year = d.year
            existing = existing_by_year[year]

            print(f"  [{i + 1}/{total}] {date_str}...", end=" ")

            if date_str in existing:
                print("[SKIP]")
                skipped += 1
                continue

            rows, called_api = fetch_product_date(api_key, product, d, existing)
            if called_api:
                api_calls += 1

            if rows:
                csv_path = product_dir / f"{year}.csv"
                write_csv(csv_path, rows)
                existing.add(date_str)
                fetched += 1
                print(f"[OK] {len(rows)} rows")
            else:
                skipped += 1
                print("[NO DATA]")

            # Rate limiting
            if called_api and i < total - 1:
                time.sleep(DELAY_BETWEEN_REQUESTS)

        print(f"  {product.upper()} done. Fetched: {fetched}, Skipped: {skipped}, API calls: {api_calls}")

    print("\nAll done.")


if __name__ == "__main__":
    main()
