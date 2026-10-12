import argparse
import csv
import os
import time
from datetime import date
from pathlib import Path

import requests

API_URL = "https://data.cityofchicago.org/resource/wrvz-psew.json"
METADATA_URL = "https://data.cityofchicago.org/api/views/wrvz-psew.json"
OUTPUT_DIR = Path(os.getenv("RAW_DATA_DIR", "data/raw"))
DEFAULT_MAX_ROWS_PER_FILE = int(os.getenv("MAX_ROWS_PER_FILE", "50000"))
BATCH_SIZE = int(os.getenv("API_BATCH_SIZE", "5000"))
PERIOD_START = os.getenv("EXTRACTION_START_DATE", "2023-01-01")
PERIOD_END_EXCLUSIVE = os.getenv("EXTRACTION_END_DATE_EXCLUSIVE", "2023-04-01")
MAX_RETRIES = 3


def build_periods(start_date, end_date_exclusive):
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date_exclusive)
    if start >= end:
        raise ValueError("EXTRACTION_END_DATE_EXCLUSIVE must be later than EXTRACTION_START_DATE.")

    periods = []
    cursor = start
    while cursor < end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        period_end = min(next_month, end)
        filename = f"chicago_taxi_{cursor.year}_{cursor.month:02d}.csv"
        periods.append((
            f"{cursor.isoformat()}T00:00:00",
            f"{period_end.isoformat()}T00:00:00",
            filename,
        ))
        cursor = period_end
    return periods


def get_dataset_columns(session):
    response = session.get(METADATA_URL, timeout=(15, 60))
    response.raise_for_status()
    metadata = response.json()
    columns = [
        item["fieldName"] for item in metadata.get("columns", [])
        if item.get("fieldName") and not item["fieldName"].startswith(":")
    ]
    if not columns:
        raise RuntimeError("Could not retrieve dataset column metadata.")
    return columns


def request_page(session, token, start_date, end_date, offset, limit):
    params = {
        "$where": f"trip_start_timestamp >= '{start_date}' AND trip_start_timestamp < '{end_date}'",
        "$order": "trip_start_timestamp ASC, trip_id ASC",
        "$limit": limit,
        "$offset": offset,
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                API_URL, headers={"X-App-Token": token},
                params=params, timeout=(15, 60),
            )
            if (response.status_code == 429 or response.status_code >= 500) and attempt < MAX_RETRIES:
                wait = min(2 ** attempt, 30)
                print(f"HTTP {response.status_code}; retrying in {wait}s...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise RuntimeError("Unexpected API response; expected a JSON list.")
            return rows
        except (requests.Timeout, requests.ConnectionError):
            if attempt == MAX_RETRIES:
                raise
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError("API request failed after retries.")


def test_api_connection(session, token, columns):
    print("Testing Chicago Taxi API...")
    rows = request_page(session, token, "2023-01-01T00:00:00", "2023-01-02T00:00:00", 0, 5)
    print(f"SUCCESS: received {len(rows)} records.")
    print(f"Official metadata columns: {len(columns)}")
    if rows:
        extra = set(rows[0]) - set(columns)
        if extra:
            print(f"Warning: API fields not listed in metadata: {sorted(extra)}")
        print("Sample record:")
        print(rows[0])


def extract_month(session, token, columns, start_date, end_date, filename, max_rows, overwrite):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / filename
    temp_file = output_file.with_suffix(".csv.part")
    if output_file.exists() and not overwrite:
        raise FileExistsError(f"{output_file} already exists. Use --overwrite to replace it.")
    if temp_file.exists():
        temp_file.unlink()

    total_rows = 0
    offset = 0
    print(f"\nExtracting {filename}: {start_date} to {end_date}")
    print(f"Maximum rows: {max_rows:,}")
    try:
        with temp_file.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=columns, extrasaction="raise")
            writer.writeheader()
            while total_rows < max_rows:
                limit = min(BATCH_SIZE, max_rows - total_rows)
                rows = request_page(session, token, start_date, end_date, offset, limit)
                if not rows:
                    break
                returned = set().union(*(set(row.keys()) for row in rows))
                unexpected = returned - set(columns)
                if unexpected:
                    raise RuntimeError(f"API returned columns absent from metadata: {sorted(unexpected)}")
                writer.writerows(rows)
                file.flush()
                total_rows += len(rows)
                offset += len(rows)
                print(f"Retrieved {total_rows:,} rows for {filename}")
                if len(rows) < limit:
                    break
                time.sleep(0.2)

        if total_rows == 0:
            raise RuntimeError(f"No records returned for {filename}.")
        temp_file.replace(output_file)
        print(f"Finished: {output_file} ({total_rows:,} rows, {len(columns)} columns)")
        return {"file": str(output_file), "rows": total_rows}
    except Exception:
        if temp_file.exists():
            temp_file.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description="Extract Chicago Taxi Trips using Socrata.")
    parser.add_argument("--max-rows-per-file", type=int, default=DEFAULT_MAX_ROWS_PER_FILE)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--test-api", action="store_true")
    args = parser.parse_args()
    if args.max_rows_per_file <= 0:
        parser.error("--max-rows-per-file must be positive.")
    if BATCH_SIZE <= 0:
        parser.error("API_BATCH_SIZE must be positive.")
    try:
        periods = build_periods(PERIOD_START, PERIOD_END_EXCLUSIVE)
    except ValueError as exc:
        parser.error(str(exc))
    token = os.getenv("SOCRATA_APP_TOKEN", "").strip()
    if not token:
        raise SystemExit("SOCRATA_APP_TOKEN is missing. Set it in your local environment.")

    with requests.Session() as session:
        columns = get_dataset_columns(session)
        if args.test_api:
            test_api_connection(session, token, columns)
            return
        for start_date, end_date, filename in periods:
            extract_month(
                session, token, columns, start_date, end_date, filename,
                args.max_rows_per_file, args.overwrite,
            )


if __name__ == "__main__":
    main()
