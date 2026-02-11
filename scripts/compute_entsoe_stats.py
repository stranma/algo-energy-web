#!/usr/bin/env python3
"""Compute percentile statistics for ENTSO-E balancing capacity clearing prices.

Reads aFRR/mFRR CSVs and computes per (block, direction) percentiles.

Output: data/entsoe/afrr_stats.csv and data/entsoe/mfrr_stats.csv
"""

import csv
import math
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data" / "entsoe"

PRODUCTS = {
    "afrr": DATA_DIR / "afrr",
    "mfrr": DATA_DIR / "mfrr",
}

STATS_HEADER = ["product", "block", "block_start", "direction", "count", "max_price", "p10", "p25", "p50", "p75", "p90", "avg_volume"]


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


def load_data(product_dir: Path) -> dict:
    """Load prices and volumes grouped by (block, block_start, direction).

    Returns dict: (block, block_start, direction) -> {"prices": [...], "volumes": [...]}.
    """
    groups = {}
    csv_files = sorted(product_dir.glob("*.csv"))

    for csv_path in csv_files:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                price_str = row.get("price_eur_mw", "").strip()
                if not price_str:
                    continue
                try:
                    price = float(price_str)
                except ValueError:
                    continue

                volume = 0.0
                vol_str = row.get("volume_mw", "").strip()
                if vol_str:
                    try:
                        volume = float(vol_str)
                    except ValueError:
                        pass

                block = row.get("block", "")
                block_start = row.get("block_start", "")
                direction = row.get("direction", "")
                key = (block, block_start, direction)

                if key not in groups:
                    groups[key] = {"prices": [], "volumes": []}
                groups[key]["prices"].append(price)
                groups[key]["volumes"].append(volume)

    return groups


def compute_stats(product: str, groups: dict) -> list:
    """Compute stats rows sorted by (block, direction)."""
    rows = []
    for key in sorted(groups.keys(), key=lambda k: (int(k[0]) if k[0].isdigit() else 0, k[2])):
        block, block_start, direction = key
        prices = sorted(groups[key]["prices"])
        volumes = groups[key]["volumes"]
        count = len(prices)
        if count == 0:
            continue

        avg_vol = sum(volumes) / len(volumes) if volumes else 0.0

        rows.append([
            product,
            block,
            block_start,
            direction,
            count,
            f"{max(prices):.2f}",
            f"{percentile(prices, 10):.2f}",
            f"{percentile(prices, 25):.2f}",
            f"{percentile(prices, 50):.2f}",
            f"{percentile(prices, 75):.2f}",
            f"{percentile(prices, 90):.2f}",
            f"{avg_vol:.1f}",
        ])

    return rows


def main():
    for product, product_dir in PRODUCTS.items():
        if not product_dir.exists():
            print(f"[{product.upper()}] No data directory found. Skipping.")
            continue

        csv_files = list(product_dir.glob("*.csv"))
        if not csv_files:
            print(f"[{product.upper()}] No CSV files found. Skipping.")
            continue

        print(f"[{product.upper()}] Loading data from {len(csv_files)} file(s)...")
        groups = load_data(product_dir)

        if not groups:
            print(f"[{product.upper()}] No price data found. Skipping.")
            continue

        rows = compute_stats(product, groups)
        output_path = DATA_DIR / f"{product}_stats.csv"

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(STATS_HEADER)
            writer.writerows(rows)

        print(f"[{product.upper()}] Wrote {len(rows)} rows to {output_path.name}")

    print("Done.")


if __name__ == "__main__":
    main()
