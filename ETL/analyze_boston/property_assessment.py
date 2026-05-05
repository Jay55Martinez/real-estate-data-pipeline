"""Extract East Boston housing records from Analyze Boston assessments.

This module is intentionally stdlib-only so it can run as the first pipeline
step before the project has a heavier orchestration/runtime layer.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CKAN_PACKAGE_URL = (
    "https://data.boston.gov/api/3/action/package_show?id=property-assessment"
)
SOURCE_NAME = "analyze_boston_assessment"
DEFAULT_ZIP_CODES = ("02128",)
DEFAULT_CITY_NAMES = ("EAST BOSTON",)
DEFAULT_OUTPUT_ROOT = Path("data")
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DEFAULT_DB_ENV_FILE = Path("Database/.env")
RAW_RECORD_BATCH_SIZE = 1_000

HOUSING_LU_CODES = {
    "A",
    "CD",
    "CM",
    "R1",
    "R2",
    "R3",
    "R4",
    "RL - RL",
}
MIXED_USE_LU_CODES = {"RC"}
MIXED_USE_LUC_CODES = {"013", "031"}
RESIDENTIAL_LUC_PREFIXES = ("1",)
NON_VALUE_MARKERS = {"", "NA", "N/A", "NULL", "NONE"}

CORE_COLUMNS = (
    "source_name",
    "source_dataset_url",
    "source_resource_id",
    "source_resource_name",
    "source_resource_url",
    "source_resource_modified_at",
    "source_file",
    "extracted_at",
    "raw_source_record_hash",
    "assessment_year",
    "boston_pid",
    "cm_id",
    "gis_id",
    "address_line1",
    "address_line2",
    "display_address",
    "city",
    "state_code",
    "postal_code",
    "land_use_code",
    "land_use_numeric_code",
    "land_use_description",
    "housing_category",
    "building_type",
    "owner_occupied",
    "owner_name",
    "mail_addressee",
    "mail_street_address",
    "mail_city",
    "mail_state",
    "mail_postal_code",
    "building_sequence",
    "building_count",
    "residential_units",
    "commercial_units",
    "rental_units",
    "land_area_sqft",
    "gross_area_sqft",
    "living_area_sqft",
    "year_built",
    "year_remodeled",
    "bedrooms",
    "full_bathrooms",
    "half_bathrooms",
    "kitchens",
    "total_rooms",
    "parking_spaces",
    "fireplaces",
    "land_value",
    "building_value",
    "special_feature_value",
    "total_assessed_value",
    "gross_tax",
)


@dataclass(frozen=True)
class AssessmentResource:
    """Latest CSV resource discovered from the Analyze Boston CKAN package."""

    fiscal_year: int
    resource_id: str
    name: str
    url: str
    format: str
    size: int | None
    hash: str | None
    created_at: str | None
    metadata_modified_at: str | None
    last_modified_at: str | None


@dataclass
class ExtractCounts:
    records_read: int = 0
    geography_records: int = 0
    housing_records: int = 0
    raw_geography_records_written: int = 0
    core_housing_records_written: int = 0
    raw_housing_jsonl_records_written: int = 0


@dataclass
class LoadCounts:
    source_id: int
    ingestion_run_id: str
    raw_records_read: int = 0
    raw_records_inserted: int = 0
    raw_records_skipped: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def build_database_url(database_url: str | None, env_file: Path):
    if database_url:
        return database_url

    import os

    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    env_values = {**load_env_file(env_file), **os.environ}
    user = env_values.get("POSTGRES_USER")
    password = env_values.get("POSTGRES_PASSWORD")
    database = env_values.get("POSTGRES_DB")
    host = env_values.get("POSTGRES_HOST", "127.0.0.1")
    port = env_values.get("POSTGRES_PORT", "5432")

    missing = [
        name
        for name, value in (
            ("POSTGRES_USER", user),
            ("POSTGRES_PASSWORD", password),
            ("POSTGRES_DB", database),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing database settings: "
            + ", ".join(missing)
            + ". Set DATABASE_URL or fill Database/.env."
        )

    try:
        from sqlalchemy import URL
    except ImportError as exc:
        raise RuntimeError(
            "SQLAlchemy is not installed. Run `pip install -r requirements.txt` "
            "after installing the new database dependencies."
        ) from exc

    return URL.create(
        "postgresql+psycopg2",
        username=user,
        password=password,
        host=host,
        port=int(port),
        database=database,
    )


def create_db_engine(database_url: str | None, env_file: Path):
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise RuntimeError(
            "SQLAlchemy is not installed. Run `pip install -r requirements.txt` "
            "after installing the new database dependencies."
        ) from exc

    return create_engine(build_database_url(database_url, env_file), future=True)


def fetch_json(url: str, timeout: int) -> dict:
    request = Request(url, headers={"User-Agent": "real-estate-data-pipeline/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def resource_display_name(resource: dict) -> str:
    translated = resource.get("name_translated") or {}
    return (
        translated.get("en")
        or resource.get("name")
        or resource.get("description")
        or resource.get("url")
        or ""
    )


def parse_fiscal_year(*values: str | None) -> int | None:
    for value in values:
        if not value:
            continue
        patterns = (
            r"\bFY\s*(20\d{2})\b",
            r"\bfy(20\d{2})\b",
            r"\bproperty[-_ ]assessment[-_ ]fy(20\d{2})\b",
            r"\bdata(20\d{2})[-_ ]?full\b",
        )
        for pattern in patterns:
            match = re.search(pattern, value, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def discover_latest_resource(package_url: str, timeout: int) -> AssessmentResource:
    package = fetch_json(package_url, timeout=timeout)
    if not package.get("success"):
        raise RuntimeError(f"Analyze Boston package request failed: {package!r}")

    candidates: list[AssessmentResource] = []
    for resource in package.get("result", {}).get("resources", []):
        name = resource_display_name(resource)
        url = resource.get("url") or resource.get("original_url") or ""
        fmt = (resource.get("format") or "").upper()
        fiscal_year = parse_fiscal_year(name, url)
        if not fiscal_year:
            continue
        if fmt != "CSV" and not url.lower().endswith(".csv"):
            continue
        candidates.append(
            AssessmentResource(
                fiscal_year=fiscal_year,
                resource_id=resource.get("id") or resource.get("resource_id") or "",
                name=name,
                url=url,
                format=fmt or "CSV",
                size=resource.get("size"),
                hash=resource.get("hash") or None,
                created_at=resource.get("created"),
                metadata_modified_at=resource.get("metadata_modified"),
                last_modified_at=resource.get("last_modified"),
            )
        )

    if not candidates:
        raise RuntimeError("No property assessment CSV resources found in CKAN package.")

    return max(
        candidates,
        key=lambda item: (
            item.fiscal_year,
            item.metadata_modified_at or "",
            item.created_at or "",
        ),
    )


def filename_from_url(url: str, fallback: str) -> str:
    filename = Path(urlparse(url).path).name
    return filename or fallback


def download_resource(
    resource: AssessmentResource,
    raw_dir: Path,
    timeout: int,
    force: bool,
) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / filename_from_url(
        resource.url,
        f"property_assessment_fy{resource.fiscal_year}.csv",
    )

    if destination.exists() and not force:
        return destination

    request = Request(
        resource.url,
        headers={"User-Agent": "real-estate-data-pipeline/0.1"},
    )
    tmp_path: Path | None = None
    try:
        with urlopen(request, timeout=timeout) as response:
            with tempfile.NamedTemporaryFile(
                "wb",
                delete=False,
                dir=raw_dir,
                prefix=f".{destination.name}.",
                suffix=".part",
            ) as tmp:
                tmp_path = Path(tmp.name)
                shutil.copyfileobj(response, tmp, length=DOWNLOAD_CHUNK_SIZE)
        tmp_path.replace(destination)
    except Exception:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()
        raise

    return destination


def normalize_header(header: str | None) -> str:
    return (header or "").strip()


def clean_text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    if cleaned.upper() in NON_VALUE_MARKERS:
        return None
    return cleaned


def clean_identifier(value: str | None) -> str | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    return cleaned.rstrip("_")


def normalize_postal_code(value: str | None) -> str | None:
    cleaned = clean_identifier(value)
    if not cleaned:
        return None
    match = re.search(r"\d{5}", cleaned)
    return match.group(0) if match else cleaned


def parse_integer(value: str | None) -> int | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    numeric = cleaned.replace(",", "")
    try:
        return int(Decimal(numeric))
    except (InvalidOperation, ValueError):
        return None


def parse_decimal_string(value: str | None) -> str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    numeric = (
        cleaned.replace("$", "")
        .replace(",", "")
        .replace(" ", "")
        .replace("\u00a0", "")
    )
    if numeric.startswith("(") and numeric.endswith(")"):
        numeric = f"-{numeric[1:-1]}"
    try:
        return format(Decimal(numeric), "f")
    except (InvalidOperation, ValueError):
        return None


def normalize_bool(value: str | None) -> str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    normalized = cleaned.upper()
    if normalized in {"Y", "YES", "TRUE", "T", "1"}:
        return "true"
    if normalized in {"N", "NO", "FALSE", "F", "0"}:
        return "false"
    return None


def normalize_lu_code(value: str | None) -> str:
    return (clean_text(value) or "").upper()


def normalize_luc(value: str | None) -> str:
    cleaned = clean_identifier(value) or ""
    digits = re.sub(r"\D", "", cleaned)
    return digits.zfill(3) if digits and len(digits) < 3 else digits


def normalize_row(raw_row: dict[str, str]) -> dict[str, str]:
    return {normalize_header(key): (value or "").strip() for key, value in raw_row.items()}


def compact_json(row: dict[str, str]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_record_hash(row: dict[str, str]) -> str:
    return hashlib.sha256(compact_json(row).encode("utf-8")).hexdigest()


def csv_safe(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def address_line1(row: dict[str, str]) -> str | None:
    parts = [clean_text(row.get("ST_NUM")), clean_text(row.get("ST_NUM2")), clean_text(row.get("ST_NAME"))]
    return " ".join(part for part in parts if part) or None


def address_line2(row: dict[str, str]) -> str | None:
    unit = clean_text(row.get("UNIT_NUM"))
    if not unit:
        return None
    if re.match(r"^(unit|apt|suite|ste|#)\b", unit, flags=re.IGNORECASE):
        return unit
    return f"Unit {unit}"


def display_address(row: dict[str, str]) -> str | None:
    line1 = address_line1(row)
    line2 = address_line2(row)
    city = clean_text(row.get("CITY"))
    postal_code = normalize_postal_code(row.get("ZIP_CODE") or row.get("ZIPCODE"))

    if not line1:
        return None

    property_line = " ".join(part for part in (line1, line2) if part)
    locality = ", ".join(part for part in (city, "MA") if part)
    if postal_code:
        locality = f"{locality} {postal_code}" if locality else postal_code
    return ", ".join(part for part in (property_line, locality) if part)


def matches_geography(
    row: dict[str, str],
    zip_codes: set[str],
    city_names: set[str],
    match_mode: str,
) -> bool:
    row_zip = normalize_postal_code(row.get("ZIP_CODE") or row.get("ZIPCODE"))
    row_city = (clean_text(row.get("CITY")) or "").upper()
    zip_match = row_zip in zip_codes if zip_codes else True
    city_match = row_city in city_names if city_names else True

    if match_mode == "all":
        return zip_match and city_match
    return zip_match or city_match


def housing_category(row: dict[str, str], include_mixed_use: bool) -> str | None:
    lu = normalize_lu_code(row.get("LU"))
    luc = normalize_luc(row.get("LUC"))
    description = (clean_text(row.get("LU_DESC")) or "").upper()

    if include_mixed_use and (lu in MIXED_USE_LU_CODES or luc in MIXED_USE_LUC_CODES):
        return "mixed_use_residential"
    if "CONDO MAIN" in description:
        return "residential_condo_main"
    if lu == "CD" or description == "RESIDENTIAL CONDO":
        return "residential_condo"
    if "SINGLE FAM" in description:
        return "single_family"
    if "TWO-FAM" in description:
        return "two_family"
    if "THREE-FAM" in description:
        return "three_family"
    if "APT" in description or "APART" in description:
        return "apartment"
    if "HOUSING" in description:
        return "subsidized_housing"
    if "RES LAND" in description or "RESIDENTIAL LAND" in description:
        return "residential_land"
    if "DWELLING" in description or "RESIDENTIAL" in description:
        return "residential_other"
    if lu in HOUSING_LU_CODES or luc.startswith(RESIDENTIAL_LUC_PREFIXES):
        return "residential_other"
    return None


def is_housing_record(
    row: dict[str, str],
    include_mixed_use: bool,
    include_parking: bool,
    include_condo_main: bool,
) -> bool:
    description = (clean_text(row.get("LU_DESC")) or "").upper()
    if not include_parking and "PARKING" in description:
        return False
    if not include_condo_main and "CONDO MAIN" in description:
        return False
    return housing_category(row, include_mixed_use=include_mixed_use) is not None


def normalize_core_row(
    row: dict[str, str],
    resource: AssessmentResource | None,
    source_csv: Path,
    extracted_at: str,
) -> dict[str, str]:
    assessment_year = (
        resource.fiscal_year
        if resource
        else parse_fiscal_year(source_csv.name)
    )
    category = housing_category(row, include_mixed_use=True)
    core = {
        "source_name": SOURCE_NAME,
        "source_dataset_url": "https://data.boston.gov/dataset/property-assessment",
        "source_resource_id": resource.resource_id if resource else "",
        "source_resource_name": resource.name if resource else source_csv.name,
        "source_resource_url": resource.url if resource else "",
        "source_resource_modified_at": resource.metadata_modified_at if resource else "",
        "source_file": str(source_csv),
        "extracted_at": extracted_at,
        "raw_source_record_hash": source_record_hash(row),
        "assessment_year": assessment_year,
        "boston_pid": clean_identifier(row.get("PID")),
        "cm_id": clean_identifier(row.get("CM_ID")),
        "gis_id": clean_identifier(row.get("GIS_ID")),
        "address_line1": address_line1(row),
        "address_line2": address_line2(row),
        "display_address": display_address(row),
        "city": clean_text(row.get("CITY")),
        "state_code": "MA",
        "postal_code": normalize_postal_code(row.get("ZIP_CODE") or row.get("ZIPCODE")),
        "land_use_code": clean_text(row.get("LU")),
        "land_use_numeric_code": normalize_luc(row.get("LUC")),
        "land_use_description": clean_text(row.get("LU_DESC")),
        "housing_category": category,
        "building_type": clean_text(row.get("BLDG_TYPE")),
        "owner_occupied": normalize_bool(row.get("OWN_OCC")),
        "owner_name": clean_text(row.get("OWNER")),
        "mail_addressee": clean_text(row.get("MAIL_ADDRESSEE")),
        "mail_street_address": clean_text(row.get("MAIL_STREET_ADDRESS")),
        "mail_city": clean_text(row.get("MAIL_CITY")),
        "mail_state": clean_text(row.get("MAIL_STATE")),
        "mail_postal_code": normalize_postal_code(row.get("MAIL_ZIP_CODE") or row.get("MAIL_ZIPCODE")),
        "building_sequence": parse_integer(row.get("BLDG_SEQ")),
        "building_count": parse_integer(row.get("NUM_BLDGS")),
        "residential_units": parse_integer(row.get("RES_UNITS")),
        "commercial_units": parse_integer(row.get("COM_UNITS")),
        "rental_units": parse_integer(row.get("RC_UNITS")),
        "land_area_sqft": parse_integer(row.get("LAND_SF")),
        "gross_area_sqft": parse_integer(row.get("GROSS_AREA")),
        "living_area_sqft": parse_integer(row.get("LIVING_AREA")),
        "year_built": parse_integer(row.get("YR_BUILT")),
        "year_remodeled": parse_integer(row.get("YR_REMODEL")),
        "bedrooms": parse_integer(row.get("BED_RMS")),
        "full_bathrooms": parse_integer(row.get("FULL_BTH")),
        "half_bathrooms": parse_integer(row.get("HLF_BTH")),
        "kitchens": parse_integer(row.get("KITCHENS")),
        "total_rooms": parse_integer(row.get("TT_RMS")),
        "parking_spaces": parse_integer(row.get("NUM_PARKING")),
        "fireplaces": parse_integer(row.get("FIREPLACES")),
        "land_value": parse_decimal_string(row.get("LAND_VALUE")),
        "building_value": parse_decimal_string(row.get("BLDG_VALUE")),
        "special_feature_value": parse_decimal_string(row.get("SFYI_VALUE")),
        "total_assessed_value": parse_decimal_string(row.get("TOTAL_VALUE")),
        "gross_tax": parse_decimal_string(row.get("GROSS_TAX")),
    }
    return {key: csv_safe(core.get(key)) for key in CORE_COLUMNS}


def output_paths(
    output_root: Path,
    fiscal_year: int | None,
    label: str,
) -> dict[str, Path]:
    suffix = f"fy{fiscal_year}" if fiscal_year else "unknown_fy"
    extract_dir = output_root / "extracted" / "analyze_boston"
    return {
        "raw_geography_csv": extract_dir
        / f"{label}_property_assessment_raw_{suffix}.csv",
        "raw_housing_jsonl": extract_dir / f"{label}_housing_raw_{suffix}.jsonl",
        "core_housing_csv": extract_dir / f"{label}_housing_core_{suffix}.csv",
        "manifest": extract_dir / f"{label}_manifest_{suffix}.json",
    }


def extract_housing_records(
    source_csv: Path,
    paths: dict[str, Path],
    resource: AssessmentResource | None,
    zip_codes: Iterable[str],
    city_names: Iterable[str],
    match_mode: str,
    include_mixed_use: bool,
    include_parking: bool,
    include_condo_main: bool,
) -> ExtractCounts:
    counts = ExtractCounts()
    extracted_at = utc_now_iso()
    zip_code_set = {normalize_postal_code(value) or value for value in zip_codes}
    city_name_set = {(value or "").upper() for value in city_names if value}

    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    with source_csv.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise RuntimeError(f"{source_csv} has no CSV header row.")
        normalized_fieldnames = [normalize_header(field) for field in reader.fieldnames]

        with (
            paths["raw_geography_csv"].open("w", newline="", encoding="utf-8") as raw_csv,
            paths["core_housing_csv"].open("w", newline="", encoding="utf-8") as core_csv,
            paths["raw_housing_jsonl"].open("w", encoding="utf-8") as raw_jsonl,
        ):
            raw_writer = csv.DictWriter(raw_csv, fieldnames=normalized_fieldnames)
            core_writer = csv.DictWriter(core_csv, fieldnames=CORE_COLUMNS)
            raw_writer.writeheader()
            core_writer.writeheader()

            for raw_row in reader:
                counts.records_read += 1
                row = normalize_row(raw_row)
                if not matches_geography(row, zip_code_set, city_name_set, match_mode):
                    continue

                counts.geography_records += 1
                raw_writer.writerow({field: row.get(field, "") for field in normalized_fieldnames})
                counts.raw_geography_records_written += 1

                if not is_housing_record(
                    row,
                    include_mixed_use=include_mixed_use,
                    include_parking=include_parking,
                    include_condo_main=include_condo_main,
                ):
                    continue

                counts.housing_records += 1
                source_hash = source_record_hash(row)
                raw_jsonl.write(
                    json.dumps(
                        {
                            "source_name": SOURCE_NAME,
                            "source_record_key": clean_identifier(row.get("PID")),
                            "source_record_hash": source_hash,
                            "observed_at": extracted_at,
                            "payload": row,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                counts.raw_housing_jsonl_records_written += 1
                core_writer.writerow(
                    normalize_core_row(
                        row,
                        resource=resource,
                        source_csv=source_csv,
                        extracted_at=extracted_at,
                    )
                )
                counts.core_housing_records_written += 1

    return counts


def write_manifest(
    manifest_path: Path,
    source_csv: Path,
    resource: AssessmentResource | None,
    counts: ExtractCounts,
    paths: dict[str, Path],
    args: argparse.Namespace,
) -> None:
    payload = {
        "source_name": SOURCE_NAME,
        "source_dataset_url": "https://data.boston.gov/dataset/property-assessment",
        "source_csv": str(source_csv),
        "resource": asdict(resource) if resource else None,
        "run": {
            "completed_at": utc_now_iso(),
            "zip_codes": args.zip_code,
            "city_names": args.city,
            "geography_match_mode": args.match_mode,
            "include_mixed_use": not args.exclude_mixed_use,
            "include_parking": args.include_parking,
            "include_condo_main": not args.exclude_condo_main,
        },
        "counts": asdict(counts),
        "outputs": {name: str(path) for name, path in paths.items()},
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ensure_source(connection, source_url: str) -> int:
    from sqlalchemy import text

    result = connection.execute(
        text(
            """
            INSERT INTO sources (source_name, source_type, source_url, notes)
            VALUES (:source_name, :source_type, :source_url, :notes)
            ON CONFLICT (source_name) DO UPDATE
            SET
                source_type = EXCLUDED.source_type,
                source_url = EXCLUDED.source_url,
                notes = EXCLUDED.notes
            RETURNING source_id
            """
        ),
        {
            "source_name": SOURCE_NAME,
            "source_type": "csv",
            "source_url": source_url,
            "notes": "Analyze Boston annual property assessment CSV",
        },
    )
    return int(result.scalar_one())


def start_ingestion_run(connection, source_id: int, source_query: dict[str, Any]) -> str:
    from sqlalchemy import text

    result = connection.execute(
        text(
            """
            INSERT INTO ingestion_runs (source_id, status, source_query)
            VALUES (:source_id, 'running', CAST(:source_query AS jsonb))
            RETURNING ingestion_run_id
            """
        ),
        {
            "source_id": source_id,
            "source_query": json.dumps(source_query, sort_keys=True),
        },
    )
    return str(result.scalar_one())


def finish_ingestion_run(
    connection,
    ingestion_run_id: str,
    status: str,
    records_read: int,
    records_inserted: int,
    error_message: str | None = None,
) -> None:
    from sqlalchemy import text

    connection.execute(
        text(
            """
            UPDATE ingestion_runs
            SET
                finished_at = NOW(),
                status = :status,
                records_read = :records_read,
                records_inserted = :records_inserted,
                error_message = :error_message
            WHERE ingestion_run_id = :ingestion_run_id
            """
        ),
        {
            "ingestion_run_id": ingestion_run_id,
            "status": status,
            "records_read": records_read,
            "records_inserted": records_inserted,
            "error_message": error_message,
        },
    )


def read_jsonl_batches(path: Path, batch_size: int) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            batch.append(json.loads(line))
            if len(batch) >= batch_size:
                yield batch
                batch = []
    if batch:
        yield batch


def insert_raw_record_batch(
    connection,
    source_id: int,
    ingestion_run_id: str,
    records: list[dict[str, Any]],
) -> int:
    from sqlalchemy import text

    params = [
        {
            "ingestion_run_id": ingestion_run_id,
            "source_id": source_id,
            "source_record_key": record.get("source_record_key"),
            "source_record_hash": record["source_record_hash"],
            "payload": json.dumps(record["payload"], sort_keys=True),
            "observed_at": record.get("observed_at"),
        }
        for record in records
    ]
    result = connection.execute(
        text(
            """
            INSERT INTO raw_source_records (
                ingestion_run_id,
                source_id,
                source_record_key,
                source_record_hash,
                payload,
                observed_at
            )
            VALUES (
                :ingestion_run_id,
                :source_id,
                :source_record_key,
                :source_record_hash,
                CAST(:payload AS jsonb),
                CAST(:observed_at AS timestamptz)
            )
            ON CONFLICT (source_id, source_record_hash) DO NOTHING
            """
        ),
        params,
    )
    return int(result.rowcount or 0)


def load_raw_records_to_postgres(
    engine,
    raw_housing_jsonl: Path,
    source_query: dict[str, Any],
    source_url: str,
) -> LoadCounts:
    with engine.begin() as connection:
        source_id = ensure_source(connection, source_url=source_url)
        ingestion_run_id = start_ingestion_run(
            connection,
            source_id=source_id,
            source_query=source_query,
        )

    counts = LoadCounts(source_id=source_id, ingestion_run_id=ingestion_run_id)

    try:
        with engine.begin() as connection:
            for batch in read_jsonl_batches(raw_housing_jsonl, RAW_RECORD_BATCH_SIZE):
                counts.raw_records_read += len(batch)
                counts.raw_records_inserted += insert_raw_record_batch(
                    connection,
                    source_id=source_id,
                    ingestion_run_id=ingestion_run_id,
                    records=batch,
                )
            counts.raw_records_skipped = (
                counts.raw_records_read - counts.raw_records_inserted
            )
            finish_ingestion_run(
                connection,
                ingestion_run_id=ingestion_run_id,
                status="succeeded",
                records_read=counts.raw_records_read,
                records_inserted=counts.raw_records_inserted,
            )
    except Exception as exc:
        with engine.begin() as connection:
            finish_ingestion_run(
                connection,
                ingestion_run_id=ingestion_run_id,
                status="failed",
                records_read=counts.raw_records_read,
                records_inserted=counts.raw_records_inserted,
                error_message=str(exc),
            )
        raise

    return counts


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download the latest Analyze Boston property assessment CSV and extract East Boston housing records.",
    )
    parser.add_argument(
        "--source-csv",
        type=Path,
        help="Use an existing local assessment CSV instead of discovering/downloading the latest Analyze Boston resource.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for raw downloads and extracted files. Default: data",
    )
    parser.add_argument(
        "--package-url",
        default=CKAN_PACKAGE_URL,
        help="Analyze Boston CKAN package_show URL.",
    )
    parser.add_argument(
        "--zip-code",
        action="append",
        default=None,
        help="ZIP code to include. Repeat for multiple ZIP codes. Default: 02128",
    )
    parser.add_argument(
        "--city",
        action="append",
        default=None,
        help="City name to include. Repeat for multiple city names. Default: EAST BOSTON",
    )
    parser.add_argument(
        "--match-mode",
        choices=("any", "all"),
        default="any",
        help="Use any to match city OR ZIP, all to require both. Default: any",
    )
    parser.add_argument(
        "--label",
        default="east_boston",
        help="Output filename prefix. Default: east_boston",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download the latest source CSV even if it already exists locally.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Network timeout in seconds for CKAN metadata and CSV downloads. Default: 60",
    )
    parser.add_argument(
        "--exclude-mixed-use",
        action="store_true",
        help="Exclude mixed residential/commercial records from the housing core extract.",
    )
    parser.add_argument(
        "--include-parking",
        action="store_true",
        help="Include residential parking assessment records in the housing core extract.",
    )
    parser.add_argument(
        "--exclude-condo-main",
        action="store_true",
        help="Exclude CONDO MAIN records from the housing core extract.",
    )
    parser.add_argument(
        "--load-db",
        action="store_true",
        help="Load extracted raw housing records into PostgreSQL with SQLAlchemy.",
    )
    parser.add_argument(
        "--database-url",
        help=(
            "SQLAlchemy database URL. Defaults to DATABASE_URL or values from "
            "Database/.env."
        ),
    )
    parser.add_argument(
        "--db-env-file",
        type=Path,
        default=DEFAULT_DB_ENV_FILE,
        help="Path to Postgres env file. Default: Database/.env",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.zip_code = args.zip_code or list(DEFAULT_ZIP_CODES)
    args.city = args.city or list(DEFAULT_CITY_NAMES)

    resource: AssessmentResource | None = None
    source_csv: Path
    load_counts: LoadCounts | None = None

    try:
        if args.source_csv:
            source_csv = args.source_csv
            if not source_csv.exists():
                parser.error(f"--source-csv does not exist: {source_csv}")
        else:
            resource = discover_latest_resource(args.package_url, timeout=args.timeout)
            source_csv = download_resource(
                resource,
                raw_dir=args.output_root / "raw" / "analyze_boston",
                timeout=args.timeout,
                force=args.force_download,
            )

        fiscal_year = resource.fiscal_year if resource else parse_fiscal_year(source_csv.name)
        paths = output_paths(args.output_root, fiscal_year=fiscal_year, label=args.label)
        counts = extract_housing_records(
            source_csv=source_csv,
            paths=paths,
            resource=resource,
            zip_codes=args.zip_code,
            city_names=args.city,
            match_mode=args.match_mode,
            include_mixed_use=not args.exclude_mixed_use,
            include_parking=args.include_parking,
            include_condo_main=not args.exclude_condo_main,
        )
        write_manifest(
            paths["manifest"],
            source_csv=source_csv,
            resource=resource,
            counts=counts,
            paths=paths,
            args=args,
        )
        if args.load_db:
            source_url = (
                resource.url
                if resource
                else "https://data.boston.gov/dataset/property-assessment"
            )
            engine = create_db_engine(args.database_url, args.db_env_file)
            load_counts = load_raw_records_to_postgres(
                engine,
                raw_housing_jsonl=paths["raw_housing_jsonl"],
                source_url=source_url,
                source_query={
                    "source_csv": str(source_csv),
                    "source_resource": asdict(resource) if resource else None,
                    "zip_codes": args.zip_code,
                    "city_names": args.city,
                    "geography_match_mode": args.match_mode,
                    "include_mixed_use": not args.exclude_mixed_use,
                    "include_parking": args.include_parking,
                    "include_condo_main": not args.exclude_condo_main,
                    "core_housing_csv": str(paths["core_housing_csv"]),
                    "raw_housing_jsonl": str(paths["raw_housing_jsonl"]),
                },
            )
    except (RuntimeError, OSError, URLError) as exc:
        print(f"Analyze Boston ETL failed: {exc}", file=sys.stderr)
        return 1

    print(f"Read {counts.records_read:,} assessment records from {source_csv}.")
    print(f"Matched {counts.geography_records:,} East Boston geography records.")
    print(f"Wrote {counts.core_housing_records_written:,} housing core records.")
    print(f"Core CSV: {paths['core_housing_csv']}")
    print(f"Raw geography CSV: {paths['raw_geography_csv']}")
    print(f"Raw housing JSONL: {paths['raw_housing_jsonl']}")
    print(f"Manifest: {paths['manifest']}")
    if load_counts:
        print(f"Postgres source_id: {load_counts.source_id}")
        print(f"Postgres ingestion_run_id: {load_counts.ingestion_run_id}")
        print(f"Loaded {load_counts.raw_records_inserted:,} new raw_source_records.")
        print(f"Skipped {load_counts.raw_records_skipped:,} duplicate raw_source_records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
