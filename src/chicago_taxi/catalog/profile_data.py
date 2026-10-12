import ast
import csv
import json
import os
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import requests

DATASET_ID = "wrvz-psew"
METADATA_URL = f"https://data.cityofchicago.org/api/views/{DATASET_ID}.json"
RAW_DIR = Path(os.getenv("RAW_DATA_DIR", "data/raw"))
CATALOG_DIR = Path(os.getenv("CATALOG_DIR", "data/catalog"))
NOTES = [
    "Selon la documentation de la Ville, les horodatages des courses sont arrondis au quart d’heure.",
    "Les identifiants de taxi sont cohérents, mais ne révèlent pas les numéros de médaillon.",
    "Certaines informations géographiques sont masquées pour protéger la vie privée.",
    "La Ville précise que toutes les courses ne sont pas déclarées.",
]
NULL_VALUES = {"", "null", "none", "nan", "n/a"}


def get_official_metadata():
    response = requests.get(METADATA_URL, timeout=(15, 60))
    response.raise_for_status()
    result = {}
    for item in response.json().get("columns", []):
        name = item.get("fieldName")
        if name and not name.startswith(":"):
            result[name] = {
                "official_name": item.get("name", name),
                "description": item.get("description") or "No official description supplied.",
                "official_type": item.get("dataTypeName") or item.get("renderTypeName") or "Unknown",
            }
    if not result:
        raise RuntimeError("Official metadata response contained no column definitions.")
    return result


def classify_value(value):
    value = value.strip()
    if not value or value.lower() in NULL_VALUES:
        return "null"
    if value.lower() in {"true", "false"}:
        return "boolean"
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return "struct"
        if isinstance(parsed, list):
            return "array"
    except (json.JSONDecodeError, ValueError):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, dict):
                return "struct"
            if isinstance(parsed, list):
                return "array"
        except (ValueError, SyntaxError):
            pass
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return "timestamp" if ("T" in value or " " in value) else "date"
    except ValueError:
        pass
    try:
        int(value)
        return "integer"
    except ValueError:
        pass
    try:
        if Decimal(value).is_finite():
            return "decimal"
    except InvalidOperation:
        pass
    return "string"


def observed_type(types):
    types = set(types)
    if not types:
        return "all_null_or_empty"
    if types == {"integer"}:
        return "integer"
    if types <= {"integer", "decimal"}:
        return "decimal"
    if len(types) == 1:
        return next(iter(types))
    return "mixed: " + ", ".join(sorted(types))


def main():
    files = sorted(RAW_DIR.glob("chicago_taxi_*.csv"))
    if not files:
        raise SystemExit("No monthly CSV files found in data/raw.")
    official = get_official_metadata()
    stats = defaultdict(lambda: {"rows": 0, "nulls": 0, "types": set(), "samples": [], "sample_set": set()})
    header_ref = None
    file_results = []

    for path in files:
        print(f"Profiling {path} ...")
        row_count = 0
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise RuntimeError(f"No header in {path}")
            if header_ref is None:
                header_ref = reader.fieldnames
            elif reader.fieldnames != header_ref:
                raise RuntimeError(f"Monthly CSV schema mismatch in {path.name}")
            for row in reader:
                row_count += 1
                for column in reader.fieldnames:
                    item = stats[column]
                    item["rows"] += 1
                    value = row.get(column)
                    if value is None or value.strip().lower() in NULL_VALUES:
                        item["nulls"] += 1
                        continue
                    item["types"].add(classify_value(value))
                    if value not in item["sample_set"] and len(item["samples"]) < 3:
                        item["samples"].append(value[:120])
                        item["sample_set"].add(value)
        file_results.append({"file": path.name, "rows": row_count})
        print(f"  {row_count:,} rows")

    columns = header_ref or []
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    catalog_csv = CATALOG_DIR / "column_catalog.csv"
    with catalog_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ["column_name", "official_name", "official_description", "official_type", "observed_type", "row_count", "null_count", "null_percentage", "sample_values"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for column in columns:
            item = stats[column]
            definition = official.get(column, {})
            kind = observed_type(item["types"])
            count = item["rows"]
            writer.writerow({
                "column_name": column,
                "official_name": definition.get("official_name", column),
                "official_description": definition.get("description", "Not found in official metadata."),
                "official_type": definition.get("official_type", "Unknown"),
                "observed_type": kind,
                "row_count": count,
                "null_count": item["nulls"],
                "null_percentage": round(item["nulls"] / count * 100, 2) if count else 0,
                "sample_values": " | ".join(item["samples"]),
            })

    total_rows = sum(x["rows"] for x in file_results)
    lines = [
        "# Courses de taxi de Chicago (2013–2023)", "",
        "Source : [jeu de données de la ville de Chicago](https://data.cityofchicago.org/d/wrvz-psew)",
        f"Identifiant du jeu de données : `{DATASET_ID}`",
        f"Adresse des métadonnées officielles : `{METADATA_URL}`",
        f"Fichiers profilés : {len(file_results)}",
        f"Nombre total de lignes profilées : {total_rows:,}",
        f"Colonnes documentées : {len(columns)}", "", "## Notes sur le jeu de données", "",
    ]
    lines.extend(f"- {note}" for note in NOTES)
    lines.extend(["", "## Fichiers locaux", "", "| Fichier | Lignes |", "|---|---:|"])
    lines.extend(f"| `{x['file']}` | {x['rows']:,} |" for x in file_results)
    lines.extend(["", "## Catalogue des colonnes", "", "Le fichier `column_catalog.csv` rassemble les définitions officielles, les types observés, les taux de valeurs nulles et des exemples.", "", "Les noms et descriptions officiels sont conservés dans la langue de la source. Les types observés décrivent uniquement l’échantillon local.", ""])
    (CATALOG_DIR / "data_catalog.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {catalog_csv}")
    print(f"Wrote {CATALOG_DIR / 'data_catalog.md'}")


if __name__ == "__main__":
    main()
