# Data Methodology

## Data Sources

### OTE Day-Ahead Market (CZ)

- **Source:** OTE, a.s. (Czech electricity market operator)
- **Endpoint:** `https://www.ote-cr.cz/en/short-term-markets/electricity/day-ahead-market/@@chart-data?report_date=YYYY-MM-DD`
- **Products:** Hourly and quarter-hourly electricity prices and volumes
- **Update schedule:** Daily at 3 PM UTC via GitHub Actions (`fetch-ote-data.yml`)
- **Script:** `scripts/fetch_ote.py`

### ENTSO-E Balancing Capacity (aFRR & mFRR)

- **Source:** ENTSO-E Transparency Platform
- **Endpoint:** `https://web-api.tp.entsoe.eu/api` (document type A15 — accepted bids)
- **Products:** aFRR (process type A51) and mFRR (process type A47)
- **Area:** CZ (10YCZ-CEPS-----N)
- **Update schedule:** Daily at 4 PM UTC via GitHub Actions (`fetch-entsoe-data.yml`)
- **Script:** `scripts/fetch_entsoe.py`

### ENTSO-E Day-Ahead Prices (RO)

- **Source:** ENTSO-E Transparency Platform
- **Endpoint:** `https://web-api.tp.entsoe.eu/api` (document type A44, process type A01)
- **Bidding zone:** Romania (10YRO-TEL------P)
- **Timezone:** EET/EEST (UTC+2 winter, UTC+3 summer). DST switches on same dates as CET (last Sunday of March/October).
- **Update schedule:** Daily at 4:30 PM UTC via GitHub Actions (`fetch-ro-dam-data.yml`)
- **Script:** `scripts/fetch_ro_dam.py`

## Data Schemas

### OTE Hourly (`data/hourly/YYYY.csv`)

| Column | Description |
|--------|-------------|
| date | Delivery date (YYYY-MM-DD) |
| hour | Hour of day (0-23) |
| price | Day-ahead price (EUR/MWh) |
| volume | Traded volume (MWh) |

### OTE Quarter-Hourly (`data/qh/YYYY.csv`)

| Column | Description |
|--------|-------------|
| date | Delivery date (YYYY-MM-DD) |
| quarter_hour | Quarter-hour index (0-95) |
| price | Day-ahead price (EUR/MWh) |
| volume | Traded volume (MWh) |

Quarter-hourly data is available from 2025-10-01 onwards (API format change).

### RO Day-Ahead Hourly (`data/ro/hourly/YYYY.csv`)

| Column | Description |
|--------|-------------|
| date | Delivery date (YYYY-MM-DD) |
| hour | Hour of day (0-23) |
| interval_start | ISO 8601 interval start (YYYY-MM-DDThh:mm:ss) |
| price_eur_mwh | Day-ahead price (EUR/MWh) |

No volume data -- the A44 document type only returns prices.

### ENTSO-E aFRR/mFRR (`data/entsoe/{afrr,mfrr}/YYYY.csv`)

| Column | Description |
|--------|-------------|
| date | Delivery date (YYYY-MM-DD) |
| block | Block index (0-5) |
| block_start | Block start time (00:00, 04:00, ..., 20:00) |
| direction | `up` (upward regulation) or `down` (downward regulation) |
| count | Number of accepted bids |
| max_price | Maximum accepted bid price (EUR/MW) |
| p10 | 10th percentile price (EUR/MW) |
| p25 | 25th percentile price (EUR/MW) |
| p50 | 50th percentile (median) price (EUR/MW) |
| p75 | 75th percentile price (EUR/MW) |
| p90 | 90th percentile price (EUR/MW) |
| total_volume | Total accepted volume (MW) |

Each delivery day has 6 blocks of 4 hours. Not all blocks have bids in both directions — rows are only present for block/direction combinations that had activity (or are NaN placeholders).

## Missing Data Treatment

When the ENTSO-E API returns no data for a given date (HTTP 400, 409, or empty response), the fetch script writes **NaN placeholder rows** instead of silently skipping the date. These rows have:

- `count = 0`
- All price columns empty (no value)
- `total_volume = 0`

This ensures consumers can distinguish between "no data was available at the source" and "the date was not yet fetched."

## Known Data Gaps

| Dataset | Date | Reason |
|---------|------|--------|
| mFRR | 2025-10-23 | ENTSO-E API returns HTTP 400 (no data available). Filled with NaN placeholder rows. |
