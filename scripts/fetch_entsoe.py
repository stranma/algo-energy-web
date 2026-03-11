#!/usr/bin/env python3
"""Fetch aFRR/mFRR accepted bid distributions from ENTSO-E (A15).

Uses the A15 (procured balancing capacity) endpoint which returns all
individual accepted bids. Aggregates in-memory to per-day/block/direction
percentile statistics. Supports multiple countries (CZ, RO).

Usage:
    CZ daily:    python scripts/fetch_entsoe.py
    RO daily:    python scripts/fetch_entsoe.py --country ro
    CZ backfill: python scripts/fetch_entsoe.py --from 2025-10-02 --to 2026-02-11
    RO backfill: python scripts/fetch_entsoe.py --country ro --from 2025-10-02 --to 2026-03-10
    API key:     --api-key KEY  or env ENTSOE_API_KEY
"""

import argparse
import csv
import io
import math
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_URL = "https://web-api.tp.entsoe.eu/api"

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data" / "entsoe"

DELAY_BETWEEN_REQUESTS = 2.0  # seconds -- ENTSO-E allows 400/min, we stay safe

# Country configurations: EIC code and UTC offsets for local timezone
# Both CET/CEST and EET/EEST use the same EU-wide DST switch dates
COUNTRIES: dict[str, dict[str, str | int]] = {
    "cz": {"eic": "10YCZ-CEPS-----N", "name": "Czech Republic", "winter_utc_offset": 1, "summer_utc_offset": 2},
    "ro": {"eic": "10YRO-TEL------P", "name": "Romania", "winter_utc_offset": 2, "summer_utc_offset": 3},
}

# Process types for each product
PRODUCTS: dict[str, str] = {
    "afrr": "A51",
    "mfrr": "A47",
}

# Direction mapping
DIRECTION_MAP = {"A01": "up", "A02": "down", "A03": "both"}

CSV_HEADER = [
    "date",
    "block",
    "block_start",
    "direction",
    "count",
    "max_price",
    "p10",
    "p25",
    "p50",
    "p75",
    "p90",
    "total_volume",
]


def get_product_dir(country: str, product: str) -> Path:
    """Return data directory for a country/product combination."""
    return DATA_DIR / country / product


def ensure_dirs(country: str):
    for product in PRODUCTS:
        get_product_dir(country, product).mkdir(parents=True, exist_ok=True)


def load_existing_dates(csv_path: Path) -> set:
    """Return set of date strings already present in a CSV file."""
    dates = set()
    if not csv_path.exists():
        return dates
    with open(csv_path, newline="", encoding="utf-8") as f:
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


def local_to_utc_str(d: date, winter_offset: int, summer_offset: int) -> tuple[str, str]:
    """Convert a local delivery date to ENTSO-E UTC period strings.

    Uses EU-wide DST rules (last Sunday of March / October).
    Returns (periodStart, periodEnd) in YYYYMMDDHHmm format.
    """
    year = d.year

    # Find last Sunday of March
    mar31 = date(year, 3, 31)
    dst_start = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)

    # Find last Sunday of October
    oct31 = date(year, 10, 31)
    dst_end = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)

    if dst_start <= d < dst_end:
        utc_start = datetime(d.year, d.month, d.day, 0, 0) - timedelta(hours=summer_offset)
    else:
        utc_start = datetime(d.year, d.month, d.day, 0, 0) - timedelta(hours=winter_offset)

    utc_end = utc_start + timedelta(hours=24)

    return (utc_start.strftime("%Y%m%d%H%M"), utc_end.strftime("%Y%m%d%H%M"))


def percentile(sorted_vals: list, p: float) -> float:
    """Compute p-th percentile (0-100) using linear interpolation."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    k = (p / 100.0) * (n - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])


def fetch_xml(
    api_key: str, process_type: str, period_start: str, period_end: str, area_domain: str, retries: int = 1
) -> str | None:
    """Fetch ENTSO-E A15 API and return decompressed XML string, or None on error."""
    params = (
        f"documentType=A15"
        f"&area_Domain={area_domain}"
        f"&processType={process_type}"
        f"&Type_MarketAgreement.Type=A01"
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
                            print("    [ERROR] Empty ZIP archive.")
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


def parse_and_aggregate(xml_str: str, delivery_date: str) -> list:
    """Parse A15 XML: collect all accepted bids, aggregate per (block, direction).

    Each TimeSeries = one accepted bid provider with price + volume per block.
    We group all bids by (block_idx, block_start, direction), then compute
    percentile statistics for each group.

    Returns list of CSV rows sorted by (block_idx, direction).
    """
    root = ET.fromstring(xml_str)

    # Collect bids: key = (block_idx, block_start, direction) -> list of (price, volume)
    bids = defaultdict(list)

    for ts in root:
        if not ts.tag.endswith("TimeSeries"):
            continue

        # Extract metadata — namespace-agnostic
        direction_code = ""
        has_standard = False
        has_original = False
        for child in ts:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "flowDirection.direction":
                direction_code = child.text or ""
            elif tag == "standard_MarketProduct.marketProductType":
                has_standard = True
            elif tag == "original_MarketProduct.marketProductType":
                has_original = True

        # Skip specific/non-standard contracts
        if has_original and not has_standard:
            continue

        direction = DIRECTION_MAP.get(direction_code, direction_code.lower())

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
                    qty = 0.0
                    price = 0.0
                    for field in pel:
                        ftag = field.tag.split("}")[-1] if "}" in field.tag else field.tag
                        if ftag == "position":
                            pos = int(field.text or 0)
                        elif ftag == "quantity":
                            try:
                                qty = float(field.text or 0)
                            except (ValueError, TypeError):
                                qty = 0.0
                        elif ftag == "procurement_Price.amount":
                            try:
                                price = float(field.text or 0)
                            except (ValueError, TypeError):
                                price = 0.0
                    if pos is not None:
                        points.append((pos, qty, price))

            if not resolution or not points:
                continue

            points.sort(key=lambda p: p[0])

            # Determine block mapping based on resolution / point positions
            if resolution == "PT4H":
                # Native 4h blocks: position 1-6 -> block 0-5
                for pos, qty, price in points:
                    if qty == 0 and price == 0:
                        continue
                    block_idx = pos - 1
                    hour = block_idx * 4
                    block_start = f"{hour:02d}:00"
                    bids[(block_idx, block_start, direction)].append((price, qty))
            elif resolution == "PT60M":
                # Hourly positions (1-24) -> map to 4h blocks via (pos-1)//4
                # Works for CZ sparse 4h-boundary positions and RO dense hourly data
                for pos, qty, price in points:
                    if qty == 0 and price == 0:
                        continue
                    block_idx = (pos - 1) // 4
                    hour = block_idx * 4
                    block_start = f"{hour:02d}:00"
                    bids[(block_idx, block_start, direction)].append((price, qty))

    # Aggregate each group into stats
    rows = []
    for key in sorted(bids.keys(), key=lambda k: (k[0], k[2])):
        block_idx, block_start, direction = key
        entries = bids[key]
        if not entries:
            continue

        prices = sorted(e[0] for e in entries)
        total_vol = sum(e[1] for e in entries)
        count = len(prices)

        rows.append(
            [
                delivery_date,
                block_idx,
                block_start,
                direction,
                count,
                f"{max(prices):.2f}",
                f"{percentile(prices, 10):.2f}",
                f"{percentile(prices, 25):.2f}",
                f"{percentile(prices, 50):.2f}",
                f"{percentile(prices, 75):.2f}",
                f"{percentile(prices, 90):.2f}",
                f"{total_vol:.1f}",
            ]
        )

    return rows


BLOCKS = [(0, "00:00"), (1, "04:00"), (2, "08:00"), (3, "12:00"), (4, "16:00"), (5, "20:00")]


def nan_placeholder_rows(date_str: str) -> list:
    """Return 12 NaN placeholder rows (6 blocks x 2 directions) for a missing date."""
    rows = []
    for block_idx, block_start in BLOCKS:
        for direction in ["down", "up"]:
            rows.append([date_str, block_idx, block_start, direction, 0, "", "", "", "", "", "", 0])
    return rows


def date_range(start: date, end: date):
    """Yield dates from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description="Fetch ENTSO-E balancing capacity data (A15 accepted bids).")
    parser.add_argument("--country", choices=list(COUNTRIES.keys()), default="cz", help="Country code (default: cz)")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--api-key", dest="api_key", help="ENTSO-E API key (or env ENTSOE_API_KEY)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ENTSOE_API_KEY")
    if not api_key:
        print("ERROR: No API key. Use --api-key or set ENTSOE_API_KEY env var.")
        sys.exit(1)

    country = args.country
    cfg = COUNTRIES[country]
    area_domain = str(cfg["eic"])
    winter_offset = int(cfg["winter_utc_offset"])
    summer_offset = int(cfg["summer_utc_offset"])

    ensure_dirs(country)
    print(f"Country: {cfg['name']} ({country.upper()})")

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

    for product, process_type in PRODUCTS.items():
        product_dir = get_product_dir(country, product)
        print(f"\n=== {product.upper()} ({country.upper()}) ===")

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

            period_start, period_end = local_to_utc_str(d, winter_offset, summer_offset)

            xml_str = fetch_xml(api_key, process_type, period_start, period_end, area_domain)
            api_calls += 1

            csv_path = product_dir / f"{year}.csv"

            if xml_str is None:
                rows = nan_placeholder_rows(date_str)
                write_csv(csv_path, rows)
                existing.add(date_str)
                fetched += 1
                print(f"[NO DATA] -> {len(rows)} NaN rows")
            else:
                rows = parse_and_aggregate(xml_str, date_str)
                if rows:
                    write_csv(csv_path, rows)
                    existing.add(date_str)
                    fetched += 1
                    print(f"[OK] {len(rows)} rows")
                else:
                    rows = nan_placeholder_rows(date_str)
                    write_csv(csv_path, rows)
                    existing.add(date_str)
                    fetched += 1
                    print(f"[NO BIDS] -> {len(rows)} NaN rows")

            # Rate limiting after each API call
            time.sleep(DELAY_BETWEEN_REQUESTS)

        print(f"  {product.upper()} done. Fetched: {fetched}, Skipped: {skipped}, API calls: {api_calls}")

    print("\nAll done.")


if __name__ == "__main__":
    main()
