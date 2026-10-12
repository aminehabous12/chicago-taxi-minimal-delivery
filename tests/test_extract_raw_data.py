import csv

import pytest

from chicago_taxi.ingestion import extract_raw_data


class FakeResponse:
    def __init__(self, rows, status_code=200):
        self.rows = rows
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.rows


class FakeSession:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        page = next(self.pages)
        if isinstance(page, tuple):
            status_code, rows = page
            return FakeResponse(rows, status_code)
        return FakeResponse(page)


def test_build_periods_splits_date_range_into_monthly_files():
    periods = extract_raw_data.build_periods("2023-01-15", "2023-03-01")

    assert periods == [
        ("2023-01-15T00:00:00", "2023-02-01T00:00:00", "chicago_taxi_2023_01.csv"),
        ("2023-02-01T00:00:00", "2023-03-01T00:00:00", "chicago_taxi_2023_02.csv"),
    ]


def test_build_periods_rejects_empty_or_reversed_range():
    with pytest.raises(ValueError, match="must be later"):
        extract_raw_data.build_periods("2023-03-01", "2023-03-01")


def test_request_page_sends_stable_date_filter_and_order():
    session = FakeSession([[{"trip_id": "1"}]])

    rows = extract_raw_data.request_page(
        session, "token-value", "2023-01-01T00:00:00", "2023-02-01T00:00:00", 0, 5000
    )

    assert rows == [{"trip_id": "1"}]
    url, kwargs = session.calls[0]
    assert url == extract_raw_data.API_URL
    assert kwargs["headers"] == {"X-App-Token": "token-value"}
    assert kwargs["params"]["$where"] == (
        "trip_start_timestamp >= '2023-01-01T00:00:00' AND "
        "trip_start_timestamp < '2023-02-01T00:00:00'"
    )
    assert kwargs["params"]["$order"] == "trip_start_timestamp ASC, trip_id ASC"


def test_request_page_retries_rate_limit(monkeypatch):
    session = FakeSession([(429, []), [{"trip_id": "1"}]])
    monkeypatch.setattr(extract_raw_data.time, "sleep", lambda _: None)

    rows = extract_raw_data.request_page(
        session, "token-value", "2023-01-01T00:00:00", "2023-02-01T00:00:00", 0, 5000
    )

    assert rows == [{"trip_id": "1"}]
    assert len(session.calls) == 2


def test_extract_month_writes_csv_with_metadata_header(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_raw_data, "OUTPUT_DIR", tmp_path)
    session = FakeSession([[{"trip_id": "1", "fare": "12.50"}]])

    result = extract_raw_data.extract_month(
        session,
        "token-value",
        ["trip_id", "fare"],
        "2023-01-01T00:00:00",
        "2023-02-01T00:00:00",
        "sample.csv",
        10,
        False,
    )

    assert result == {"file": str(tmp_path / "sample.csv"), "rows": 1}
    with (tmp_path / "sample.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{"trip_id": "1", "fare": "12.50"}]
    assert not (tmp_path / "sample.csv.part").exists()


def test_extract_month_refuses_to_overwrite_without_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_raw_data, "OUTPUT_DIR", tmp_path)
    (tmp_path / "sample.csv").write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Use --overwrite"):
        extract_raw_data.extract_month(
            FakeSession([]), "token-value", ["trip_id"], "start", "end", "sample.csv", 10, False
        )
