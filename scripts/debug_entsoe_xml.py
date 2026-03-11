#!/usr/bin/env python3
"""Debug: probe A15 and A37 endpoints for CZ/RO aFRR."""

import io
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta


def local_to_utc_str(d, winter_offset=1, summer_offset=2):
    year = d.year
    mar31 = date(year, 3, 31)
    dst_start = mar31 - timedelta(days=(mar31.weekday() + 1) % 7)
    oct31 = date(year, 10, 31)
    dst_end = oct31 - timedelta(days=(oct31.weekday() + 1) % 7)
    offset = summer_offset if dst_start <= d < dst_end else winter_offset
    utc_start = datetime(d.year, d.month, d.day) - timedelta(hours=offset)
    utc_end = utc_start + timedelta(hours=24)
    return utc_start.strftime("%Y%m%d%H%M"), utc_end.strftime("%Y%m%d%H%M")


def fetch(url, api_key):
    url = url + f"&securityToken={api_key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "debug/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        print(f"  HTTP {exc.code}: {body[:200]}")
        return None

    if "zip" in ct or "octet" in ct:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            return zf.read(zf.namelist()[0]).decode("utf-8")
    else:
        text = raw.decode("utf-8")
        if "No matching data" in text:
            print("  ** NO DATA **")
            return None
        return text


def dump_xml(xml_str, max_ts=5, max_points=10):
    root = ET.fromstring(xml_str)
    rtag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    print(f"  Root: {rtag}")

    total_ts = sum(1 for e in root if (e.tag.split("}")[-1] if "}" in e.tag else e.tag) == "TimeSeries")
    print(f"  Total TimeSeries: {total_ts}")

    ts_idx = 0
    for el in root:
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag != "TimeSeries":
            continue
        ts_idx += 1
        if ts_idx > max_ts:
            break
        print(f"\n  TimeSeries #{ts_idx}")
        for child in el:
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == "Period":
                resolution = ""
                interval_start = ""
                interval_end = ""
                points = []
                for pel in child:
                    ptag = pel.tag.split("}")[-1] if "}" in pel.tag else pel.tag
                    if ptag == "resolution":
                        resolution = pel.text
                    elif ptag == "timeInterval":
                        for ti in pel:
                            ttag = ti.tag.split("}")[-1] if "}" in ti.tag else ti.tag
                            if ttag == "start":
                                interval_start = ti.text
                            elif ttag == "end":
                                interval_end = ti.text
                    elif ptag == "Point":
                        fields = {}
                        for f in pel:
                            ft = f.tag.split("}")[-1] if "}" in f.tag else f.tag
                            fields[ft] = f.text
                        points.append(fields)
                print(f"    Period: {interval_start} -> {interval_end}, res={resolution}, {len(points)} points")
                for p in points[:max_points]:
                    print(f"      {p}")
                if len(points) > max_points:
                    print(f"      ... {len(points) - max_points} more")
            else:
                print(f"    {ctag}: {child.text}")


def main():
    api_key = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ENTSOE_API_KEY")
    if not api_key:
        sys.exit("Usage: python debug_entsoe_xml.py <API_KEY>")

    d = date(2025, 12, 1)
    ps, pe = local_to_utc_str(d)

    # Test 1: A15 - Procured balancing capacity (aFRR, CZ)
    print(f"\n{'='*60}")
    print(f"  A15 (Procured capacity) - aFRR CZ - {d}")
    print(f"{'='*60}")
    xml = fetch(
        f"https://web-api.tp.entsoe.eu/api?"
        f"documentType=A15&processType=A51"
        f"&area_Domain=10YCZ-CEPS-----N"
        f"&periodStart={ps}&periodEnd={pe}"
        f"&Type_MarketAgreement.Type=A01",
        api_key,
    )
    if xml:
        dump_xml(xml)
    time.sleep(2)

    # Test 2: A15 - Procured balancing capacity (aFRR, RO)
    ps_ro, pe_ro = local_to_utc_str(d, winter_offset=2, summer_offset=3)
    print(f"\n{'='*60}")
    print(f"  A15 (Procured capacity) - aFRR RO - {d}")
    print(f"{'='*60}")
    xml = fetch(
        f"https://web-api.tp.entsoe.eu/api?"
        f"documentType=A15&processType=A51"
        f"&area_Domain=10YRO-TEL------P"
        f"&periodStart={ps_ro}&periodEnd={pe_ro}"
        f"&Type_MarketAgreement.Type=A01",
        api_key,
    )
    if xml:
        dump_xml(xml)
    time.sleep(2)

    # Test 3: A15 - Procured balancing capacity (mFRR, RO)
    print(f"\n{'='*60}")
    print(f"  A15 (Procured capacity) - mFRR RO - {d}")
    print(f"{'='*60}")
    xml = fetch(
        f"https://web-api.tp.entsoe.eu/api?"
        f"documentType=A15&processType=A47"
        f"&area_Domain=10YRO-TEL------P"
        f"&periodStart={ps_ro}&periodEnd={pe_ro}"
        f"&Type_MarketAgreement.Type=A01",
        api_key,
    )
    if xml:
        dump_xml(xml)
    time.sleep(2)

    # Test 4: A37 - Reserve bids (aFRR, CZ) - recent date due to 93-day retention
    d2 = date(2026, 2, 10)
    ps2, pe2 = local_to_utc_str(d2)
    print(f"\n{'='*60}")
    print(f"  A37 (Reserve bids) - aFRR CZ - {d2}")
    print(f"{'='*60}")
    xml = fetch(
        f"https://web-api.tp.entsoe.eu/api?"
        f"documentType=A37&businessType=B74&processType=A51"
        f"&connecting_Domain=10YCZ-CEPS-----N"
        f"&periodStart={ps2}&periodEnd={pe2}",
        api_key,
    )
    if xml:
        dump_xml(xml)
    time.sleep(2)

    # Test 5: A37 - Reserve bids (mFRR, CZ)
    print(f"\n{'='*60}")
    print(f"  A37 (Reserve bids) - mFRR CZ - {d2}")
    print(f"{'='*60}")
    xml = fetch(
        f"https://web-api.tp.entsoe.eu/api?"
        f"documentType=A37&businessType=B74&processType=A47"
        f"&connecting_Domain=10YCZ-CEPS-----N"
        f"&periodStart={ps2}&periodEnd={pe2}",
        api_key,
    )
    if xml:
        dump_xml(xml)


if __name__ == "__main__":
    main()
