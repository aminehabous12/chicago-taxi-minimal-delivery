import csv
from chicago_taxi.catalog import profile_data


def test_classify_value_handles_common_types():
    assert profile_data.classify_value("") == "null"
    assert profile_data.classify_value("true") == "boolean"
    assert profile_data.classify_value('{"key": 1}') == "struct"
    assert profile_data.classify_value("[1, 2]") == "array"
    assert profile_data.classify_value("2023-01-01T12:30:00") == "timestamp"
    assert profile_data.classify_value("2023-01-01") == "date"
    assert profile_data.classify_value("12") == "integer"
    assert profile_data.classify_value("12.50") == "decimal"
    assert profile_data.classify_value("unknown") == "string"


def test_observed_type_widens_integer_and_decimal_values():
    assert profile_data.observed_type({"integer"}) == "integer"
    assert profile_data.observed_type({"integer", "decimal"}) == "decimal"
    assert profile_data.observed_type({"integer", "string"}) == "mixed: integer, string"
    assert profile_data.observed_type(set()) == "all_null_or_empty"


def test_main_builds_catalog_without_network_access(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    catalog_dir = tmp_path / "catalog"
    raw_dir.mkdir()
    with (raw_dir / "chicago_taxi_2023_01.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trip_id", "fare"])
        writer.writeheader()
        writer.writerows([{"trip_id": "1", "fare": "10.50"}, {"trip_id": "2", "fare": ""}])

    monkeypatch.setattr(profile_data, "RAW_DIR", raw_dir)
    monkeypatch.setattr(profile_data, "CATALOG_DIR", catalog_dir)
    monkeypatch.setattr(
        profile_data,
        "get_official_metadata",
        lambda: {
            "trip_id": {"official_name": "Trip ID", "description": "Unique identifier", "official_type": "text"},
            "fare": {"official_name": "Fare", "description": "Trip fare", "official_type": "number"},
        },
    )

    profile_data.main()

    with (catalog_dir / "column_catalog.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["column_name"] for row in rows] == ["trip_id", "fare"]
    assert rows[0]["observed_type"] == "integer"
    assert rows[1]["null_count"] == "1"
    report = (catalog_dir / "data_catalog.md").read_text(encoding="utf-8")
    assert "Nombre total de lignes profilées : 2" in report
