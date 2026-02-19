#!/usr/bin/env python3
"""Fetch Romanian Day-Ahead electricity prices from ENTSO-E (A44).

Uses the A44 (day-ahead prices) endpoint for the Romanian bidding zone
(10YRO-TEL------P). Stores hourly prices in data/ro/hourly/YYYY.csv.

Usage:
    Backfill:  python scripts/fetch_ro_dam.py --from 2025-01-02 --to 2025-01-31
    Daily:     python scripts/fetch_ro_dam.py  (fetches yesterday + today)
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
RO_DOMAIN = "10YRO-TEL------P"

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data" / "ro" / "hourly"

DELAY_BETWEEN_REQUESTS = 2.0  # seconds -- ENTSO-E allows 400/min, we stay safe

CSV_HEADER = ["date", "hour", "interval_start", "price_eur_mwh"]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_existing_dates(csv_path: Path) -> set[str]:
    """Return set of date strings already present in a CSV file."""
    dates: set[str] = set()
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


def write_csv(csv_path: Path, rows: list[list[str]]) -> None:
    """Append rows to CSV. Create file with header if it doesn't exist."""
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def eet_to_utc_str(d: date) -> tuple[str, str]:
    """Convert an EET delivery date to ENTSO-E UTC period strings.

    EET midnight = 22:00 UTC previous day (winter) or 21:00 UTC (summer/EEST).
    DST switches on the same dates as CET (last Sunday of March/October).
    Returns (periodStart, periodEnd) in YYYYMMDDHHmm format.
    """
    year = d.year

    # Last Sunday of March
    mar31 = date(year, 3, 31)
    dst_start = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)

    # Last Sunday of October
    oct31 = date(year, 10, 31)
    dst_end = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)

    if dst_start <= d < dst_end:
        # EEST: UTC+3, so EET midnight = 21:00 UTC previous day
        utc_start = datetime(d.year, d.month, d.day, 0, 0) - timedelta(hours=3)
    else:
        # EET: UTC+2, so EET midnight = 22:00 UTC previous day
        utc_start = datetime(d.year, d.month, d.day, 0, 0) - timedelta(hours=2)

    utc_end = utc_start + timedelta(hours=24)

    return (utc_start.strftime("%Y%m%d%H%M"), utc_end.strftime("%Y%m%d%H%M"))


def fetch_xml(api_key: str, period_start: str, period_end: str, retries: int = 1) -> str | None:
    """Fetch ENTSO-E A44 day-ahead prices and return XML string, or None on error."""
    params = (
        f"documentType=A44"
        f"&processType=A01"
        f"&in_Domain={RO_DOMAIN}"
        f"&out_Domain={RO_DOMAIN}"
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
                    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                        names = zf.namelist()
                        if not names:
                            print("    [ERROR] Empty ZIP archive.")
                            return None
                        return zf.read(names[0]).decode("utf-8")
                else:
                    text = raw.decode("utf-8")
                    if "<Reason>" in text and "No matching data" in text:
                        return None
                    return text

        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return None
            if attempt < retries:
                print(f"    [WARN] HTTP {exc.code}. Retrying...")
                time.sleep(3)
            else:
                print(f"    [ERROR] HTTP {exc.code}: {exc}. Skipping.")
                return None
        except (urllib.error.URLError, OSError) as exc:
            if attempt < retries:
                print(f"    [WARN] Attempt {attempt + 1} failed: {exc}. Retrying...")
                time.sleep(3)
            else:
                print(f"    [ERROR] Failed to fetch: {exc}. Skipping.")
                return None
    return None


def parse_prices(xml_str: str, delivery_date: str) -> list[list[str]]:
    """Parse A44 XML and extract hourly prices.

    A44 response contains TimeSeries > Period > Point with position and price.amount.
    Resolution can be PT60M (24 hourly points) or PT15M (96 quarter-hourly points).
    For PT15M, we average every 4 quarter-hourly prices into one hourly price.

    Returns list of CSV rows sorted by hour.
    """
    root = ET.fromstring(xml_str)

    hourly_prices: dict[int, float] = {}

    for ts in root:
        if not ts.tag.endswith("TimeSeries"):
            continue

        for period_el in ts:
            if not period_el.tag.endswith("Period"):
                continue

            resolution = ""
            points: list[tuple[int, float]] = []
            for child in period_el:
                ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if ctag == "resolution":
                    resolution = child.text or ""
                elif ctag == "Point":
                    position = None
                    price = None
                    for field in child:
                        ftag = field.tag.split("}")[-1] if "}" in field.tag else field.tag
                        if ftag == "position":
                            position = int(field.text or "0")
                        elif ftag == "price.amount":
                            try:
                                price = float(field.text or "0")
                            except (ValueError, TypeError):
                                price = None
                    if position is not None and price is not None:
                        points.append((position, price))

            if not points:
                continue

            if resolution == "PT15M":
                # Average 4 quarter-hourly prices per hour
                for pos, price in points:
                    hour = (pos - 1) // 4
                    if hour not in hourly_prices:
                        hourly_prices[hour] = 0.0
                    hourly_prices[hour] += price / 4.0
            else:
                # PT60M or unspecified: position 1-24 maps to hour 0-23
                # Prefer hourly data if we already have QH-derived values
                for pos, price in points:
                    hourly_prices[pos - 1] = price

    rows: list[list[str]] = []
    for hour in sorted(hourly_prices.keys()):
        interval_start = f"{delivery_date}T{hour:02d}:00:00"
        rows.append([delivery_date, str(hour), interval_start, f"{hourly_prices[hour]:.2f}"])

    return rows


def nan_placeholder_rows(date_str: str) -> list[list[str]]:
    """Return 24 NaN placeholder rows for a missing date."""
    rows: list[list[str]] = []
    for hour in range(24):
        interval_start = f"{date_str}T{hour:02d}:00:00"
        rows.append([date_str, str(hour), interval_start, ""])
    return rows


def date_range(start: date, end: date):
    """Yield dates from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Romanian Day-Ahead prices from ENTSO-E (A44).")
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

    years = sorted(set(d.year for d in dates))
    existing_by_year: dict[int, set[str]] = {}
    for year in years:
        existing_by_year[year] = load_existing_dates(DATA_DIR / f"{year}.csv")

    fetched = 0
    skipped = 0

    for i, d in enumerate(dates):
        date_str = d.isoformat()
        year = d.year
        existing = existing_by_year[year]

        print(f"  [{i + 1}/{total}] {date_str}...", end=" ")

        if date_str in existing:
            print("[SKIP]")
            skipped += 1
            continue

        period_start, period_end = eet_to_utc_str(d)
        xml_str = fetch_xml(api_key, period_start, period_end)

        csv_path = DATA_DIR / f"{year}.csv"

        if xml_str is None:
            rows = nan_placeholder_rows(date_str)
            write_csv(csv_path, rows)
            existing.add(date_str)
            fetched += 1
            print(f"[NO DATA] -> {len(rows)} NaN rows")
        else:
            rows = parse_prices(xml_str, date_str)
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
                print(f"[EMPTY] -> {len(rows)} NaN rows")

        if i < total - 1:
            time.sleep(DELAY_BETWEEN_REQUESTS)

    print(f"\nDone. Fetched: {fetched}, Skipped: {skipped}, Total: {total}")


if __name__ == "__main__":
    main()
