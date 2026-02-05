#!/usr/bin/env python3
"""Fetch CZ Day-Ahead electricity prices and volumes from OTE.

Usage:
    Backfill:  python scripts/fetch_ote.py --from 2024-01-01 --to 2026-02-05
    Daily:     python scripts/fetch_ote.py  (fetches yesterday + today)
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_URL = (
    "https://www.ote-cr.cz/en/short-term-markets/electricity/"
    "day-ahead-market/@@chart-data?report_date={}&time_resolution=60"
)

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
HOURLY_DIR = DATA_DIR / "hourly"
QH_DIR = DATA_DIR / "qh"

DELAY_BETWEEN_REQUESTS = 1.5  # seconds — be polite to OTE servers


def ensure_dirs():
    HOURLY_DIR.mkdir(parents=True, exist_ok=True)
    QH_DIR.mkdir(parents=True, exist_ok=True)


def load_existing_dates(csv_path: Path) -> set[str]:
    """Return set of date strings already present in a CSV file."""
    dates: set[str] = set()
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


def fetch_json(report_date: str, retries: int = 1) -> dict | None:
    """Fetch chart-data JSON for a given date. Retry once on failure."""
    url = BASE_URL.format(report_date)
    for attempt in range(1 + retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "AlgoEnergy-DataCollector/1.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as exc:
            if attempt < retries:
                print(f"  [WARN] Attempt {attempt + 1} failed for {report_date}: {exc}. Retrying...")
                time.sleep(2)
            else:
                print(f"  [ERROR] Failed to fetch {report_date}: {exc}. Skipping.")
                return None
    return None


def parse_data(data: dict, report_date: str):
    """Parse OTE JSON into quarter-hourly and hourly rows.

    OTE API returns two different formats:
    - Before 2025-10-01: 2 series, 24 hourly points (volume + price)
    - From 2025-10-01:   3 series, 96 QH points (volume + 15min price + 60min ref price)

    Returns (qh_rows, hourly_rows) or (None, None) on parse error.
    qh_rows may be empty for pre-QH-market dates.
    """
    try:
        series = data["data"]["dataLine"]
    except (KeyError, TypeError):
        print(f"  [ERROR] Unexpected JSON structure for {report_date}. Skipping.")
        return None, None

    num_series = len(series)
    if num_series < 2:
        print(f"  [ERROR] Expected at least 2 series, got {num_series} for {report_date}. Skipping.")
        return None, None

    if num_series >= 3:
        # New format (from 2025-10-01): 3 series, 96 quarter-hourly points
        # [0] Volume (MWh) 96pts, [1] 15-min price (EUR/MWh) 96pts, [2] 60-min ref price 96pts
        volume_points = series[0].get("point", [])
        price_qh_points = series[1].get("point", [])
        price_h_points = series[2].get("point", [])

        n_pts = min(len(volume_points), len(price_qh_points))
        if n_pts < 96:
            print(
                f"  [WARN] Incomplete QH data for {report_date}: "
                f"vol={len(volume_points)}, qh_price={len(price_qh_points)}, "
                f"h_price={len(price_h_points)} points. Proceeding with available data."
            )

        # Build quarter-hourly rows
        qh_rows = []
        for i in range(min(96, n_pts)):
            hour = i // 4
            minute = (i % 4) * 15
            price = price_qh_points[i].get("y", "")
            volume = volume_points[i].get("y", "")
            qh_rows.append([report_date, hour, minute, price, volume])

        # Build hourly rows from QH data
        hourly_rows = []
        for h in range(24):
            base = h * 4
            # Hourly price from series 2
            if base < len(price_h_points):
                h_price = price_h_points[base].get("y", "")
            else:
                h_price = ""

            # Sum the 4 quarter-hourly volumes
            h_volume = 0.0
            count = 0
            for qi in range(4):
                idx = base + qi
                if idx < len(volume_points):
                    val = volume_points[idx].get("y")
                    if val is not None:
                        h_volume += float(val)
                        count += 1
            h_volume = round(h_volume, 2) if count > 0 else ""

            hourly_rows.append([report_date, h, h_price, h_volume])

        return qh_rows, hourly_rows

    else:
        # Old format (before 2025-10-01): 2 series, 24 hourly points
        # [0] Volume (MWh) 24pts, [1] Price (EUR/MWh) 24pts
        volume_points = series[0].get("point", [])
        price_points = series[1].get("point", [])

        n_pts = min(len(volume_points), len(price_points))
        if n_pts < 24:
            print(
                f"  [WARN] Incomplete hourly data for {report_date}: "
                f"vol={len(volume_points)}, price={len(price_points)} points."
            )

        # No QH data available for old format
        qh_rows = []

        # Build hourly rows directly
        hourly_rows = []
        for h in range(min(24, n_pts)):
            price = price_points[h].get("y", "")
            volume = volume_points[h].get("y", "")
            hourly_rows.append([report_date, h, price, volume])

        return qh_rows, hourly_rows


def write_csv(csv_path: Path, header: list[str], rows: list[list], existing_dates: set[str]):
    """Append rows to CSV. Create file with header if it doesn't exist."""
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)
        writer.writerows(rows)


def process_date(report_date: str, qh_existing: set[str], hourly_existing: set[str]):
    """Fetch and store data for a single date. Returns True if data was written."""
    year = report_date[:4]

    hourly_done = report_date in hourly_existing
    qh_done = report_date in qh_existing

    # If hourly is done, and either QH is done or date is before QH market start
    if hourly_done and (qh_done or report_date < "2025-10-01"):
        print(f"  [SKIP] {report_date} already in CSVs.")
        return False

    data = fetch_json(report_date)
    if data is None:
        return False

    qh_rows, hourly_rows = parse_data(data, report_date)
    if qh_rows is None and hourly_rows is None:
        return False

    qh_csv = QH_DIR / f"{year}.csv"
    hourly_csv = HOURLY_DIR / f"{year}.csv"

    wrote_something = False

    if qh_rows and not qh_done:
        write_csv(qh_csv, ["date", "hour", "minute", "price_eur_mwh", "volume_mwh"], qh_rows, qh_existing)
        qh_existing.add(report_date)
        wrote_something = True

    if hourly_rows and not hourly_done:
        write_csv(hourly_csv, ["date", "hour", "price_eur_mwh", "volume_mwh"], hourly_rows, hourly_existing)
        hourly_existing.add(report_date)
        wrote_something = True

    if not wrote_something:
        print(f"  [SKIP] {report_date} already in CSVs.")
        return False

    qh_label = f"{len(qh_rows)} QH" if qh_rows else "no QH"
    print(f"  [OK] {report_date}: {qh_label} rows, {len(hourly_rows)} hourly rows.")
    return True


def date_range(start: date, end: date):
    """Yield dates from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description="Fetch OTE Day-Ahead market data.")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

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

    # Group dates by year for efficient existing-date checks
    dates = list(date_range(start, end))
    years = sorted(set(d.year for d in dates))

    # Pre-load existing dates per year
    qh_existing_by_year: dict[int, set[str]] = {}
    hourly_existing_by_year: dict[int, set[str]] = {}
    for year in years:
        qh_existing_by_year[year] = load_existing_dates(QH_DIR / f"{year}.csv")
        hourly_existing_by_year[year] = load_existing_dates(HOURLY_DIR / f"{year}.csv")

    total = len(dates)
    fetched = 0
    skipped = 0

    for i, d in enumerate(dates):
        date_str = d.isoformat()
        year = d.year
        print(f"[{i + 1}/{total}] Processing {date_str}...")

        written = process_date(
            date_str,
            qh_existing_by_year[year],
            hourly_existing_by_year[year],
        )

        if written:
            fetched += 1
            # Rate limiting — be polite to OTE servers
            if i < total - 1:
                time.sleep(DELAY_BETWEEN_REQUESTS)
        else:
            skipped += 1

    print(f"\nDone. Fetched: {fetched}, Skipped: {skipped}, Total: {total}")


if __name__ == "__main__":
    main()
