"""Row cleaning and canonical field mapping for Boston assessment CSV data."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ETL.analyze_boston.config import (
    CORE_COLUMNS,
    HOUSING_LU_CODES,
    MIXED_USE_LUC_CODES,
    MIXED_USE_LU_CODES,
    NON_VALUE_MARKERS,
    RESIDENTIAL_LUC_PREFIXES,
    SOURCE_DATASET_URL,
    SOURCE_NAME,
)
from ETL.analyze_boston.source import AssessmentResource, parse_fiscal_year


@dataclass(frozen=True)
class AssessmentRecord:
    """Normalized property assessment row ready for database upserts."""

    raw_row: dict[str, str]
    source_name: str
    source_dataset_url: str
    source_resource_id: str | None
    source_resource_name: str | None
    source_resource_url: str | None
    source_resource_modified_at: str | None
    source_file: str
    extracted_at: str
    source_record_hash: str
    assessment_year: int | None
    boston_pid: str | None
    cm_id: str | None
    gis_id: str | None
    address_line1: str | None
    address_line2: str | None
    display_address: str | None
    city: str | None
    state_code: str
    postal_code: str | None
    land_use_code: str | None
    land_use_numeric_code: str | None
    land_use_description: str | None
    housing_category: str | None
    property_type_code: str
    building_type: str | None
    owner_occupied: bool | None
    owner_name: str | None
    mail_addressee: str | None
    mail_street_address: str | None
    mail_city: str | None
    mail_state: str | None
    mail_postal_code: str | None
    building_sequence: int | None
    building_count: int | None
    residential_units: int | None
    commercial_units: int | None
    rental_units: int | None
    land_area_sqft: int | None
    gross_area_sqft: int | None
    living_area_sqft: int | None
    year_built: int | None
    year_remodeled: int | None
    bedrooms: int | None
    full_bathrooms: int | None
    half_bathrooms: int | None
    kitchens: int | None
    total_rooms: int | None
    parking_spaces: int | None
    fireplaces: int | None
    stories: str | None
    land_value: str | None
    building_value: str | None
    special_feature_value: str | None
    total_assessed_value: str | None
    gross_tax: str | None

    @property
    def source_record_key(self) -> str | None:
        """Use the Boston PID as the stable source property key."""

        return self.boston_pid


def utc_now_iso() -> str:
    """Return a compact UTC timestamp used in output manifests and JSONL."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_header(header: str | None) -> str:
    """Trim source column names; this fixes fields like ' GROSS_TAX '."""

    return (header or "").strip()


def clean_text(value: str | None) -> str | None:
    """Normalize empty source markers to None while preserving source spelling."""

    cleaned = (value or "").strip()
    if cleaned.upper() in NON_VALUE_MARKERS:
        return None
    return cleaned


def clean_identifier(value: str | None) -> str | None:
    """Clean parcel-like identifiers without changing leading zeroes."""

    cleaned = clean_text(value)
    if not cleaned:
        return None
    return cleaned.rstrip("_")


def normalize_postal_code(value: str | None) -> str | None:
    """Extract a five-digit ZIP code from plain or ZIP+4 source values."""

    cleaned = clean_identifier(value)
    if not cleaned:
        return None
    match = re.search(r"\d{5}", cleaned)
    return match.group(0) if match else cleaned


def parse_integer(value: str | None) -> int | None:
    """Parse source integer-ish values, accepting commas and decimals."""

    cleaned = clean_text(value)
    if cleaned is None:
        return None
    numeric = cleaned.replace(",", "")
    try:
        return int(Decimal(numeric))
    except (InvalidOperation, ValueError):
        return None


def parse_decimal_string(value: str | None) -> str | None:
    """Parse money/decimal source values and return DB-friendly text."""

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


def normalize_bool(value: str | None) -> bool | None:
    """Convert common source yes/no markers to Python booleans."""

    cleaned = clean_text(value)
    if cleaned is None:
        return None
    normalized = cleaned.upper()
    if normalized in {"Y", "YES", "TRUE", "T", "1"}:
        return True
    if normalized in {"N", "NO", "FALSE", "F", "0"}:
        return False
    return None


def normalize_lu_code(value: str | None) -> str:
    """Normalize Boston's letter land-use code for classification."""

    return (clean_text(value) or "").upper()


def normalize_luc(value: str | None) -> str | None:
    """Normalize Boston's numeric LUC to a zero-padded string."""

    cleaned = clean_identifier(value) or ""
    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        return None
    return digits.zfill(3) if len(digits) < 3 else digits


def normalize_row(raw_row: dict[str, str]) -> dict[str, str]:
    """Normalize source headers and trim cell values for one CSV row."""

    return {normalize_header(key): (value or "").strip() for key, value in raw_row.items()}


def compact_json(row: dict[str, str]) -> str:
    """Serialize a row deterministically before hashing."""

    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_record_hash(row: dict[str, str]) -> str:
    """Hash the full raw row so changed assessor values create a new raw record."""

    return hashlib.sha256(compact_json(row).encode("utf-8")).hexdigest()


def csv_safe(value: object) -> str:
    """Return an empty string for None so extracted CSVs are easy to inspect."""

    if value is None:
        return ""
    return str(value)


def address_line1(row: dict[str, str]) -> str | None:
    """Build the street address line from Boston's split street fields."""

    parts = [
        clean_text(row.get("ST_NUM")),
        clean_text(row.get("ST_NUM2")),
        clean_text(row.get("ST_NAME")),
    ]
    return " ".join(part for part in parts if part) or None


def address_line2(row: dict[str, str]) -> str | None:
    """Normalize unit values into a second address line."""

    unit = clean_text(row.get("UNIT_NUM"))
    if not unit:
        return None
    if re.match(r"^(unit|apt|suite|ste|#)\b", unit, flags=re.IGNORECASE):
        return unit
    return f"Unit {unit}"


def display_address(row: dict[str, str]) -> str | None:
    """Build a human-friendly address used on property summary views."""

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


def mailing_address_hash(record: AssessmentRecord) -> str | None:
    """Hash owner mailing address fields when the row provides one."""

    if not record.mail_street_address:
        return None
    normalized = "|".join(
        (
            record.mail_street_address.upper(),
            (record.mail_city or "").upper(),
            (record.mail_state or "").upper(),
            record.mail_postal_code or "",
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def property_address_hash(record: AssessmentRecord) -> str | None:
    """Hash canonical property address fields for idempotent address upserts."""

    if not record.address_line1:
        return None
    normalized = "|".join(
        (
            record.address_line1.upper(),
            (record.address_line2 or "").upper(),
            (record.city or "").upper(),
            record.state_code.upper(),
            record.postal_code or "",
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def matches_geography(
    row: dict[str, str],
    zip_codes: set[str],
    city_names: set[str],
    match_mode: str,
) -> bool:
    """Return whether a row belongs to the configured geography scope."""

    row_zip = normalize_postal_code(row.get("ZIP_CODE") or row.get("ZIPCODE"))
    row_city = (clean_text(row.get("CITY")) or "").upper()
    zip_match = row_zip in zip_codes if zip_codes else True
    city_match = row_city in city_names if city_names else True

    if match_mode == "all":
        return zip_match and city_match
    return zip_match or city_match


def housing_category(row: dict[str, str], include_mixed_use: bool) -> str | None:
    """Classify source land use into the project's residential categories."""

    lu = normalize_lu_code(row.get("LU"))
    luc = normalize_luc(row.get("LUC")) or ""
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


def property_type_code_for_category(category: str | None) -> str:
    """Map normalized housing category to the broad property_types table."""

    if category in {"residential_condo", "residential_condo_main"}:
        return "condo"
    if category == "three_family":
        return "three_family"
    if category in {"two_family", "apartment", "subsidized_housing"}:
        return "multifamily"
    if category == "mixed_use_residential":
        return "mixed_use"
    return "residential"


def is_housing_record(
    row: dict[str, str],
    include_mixed_use: bool,
    include_parking: bool,
    include_condo_main: bool,
) -> bool:
    """Return whether a row should seed the canonical property universe."""

    description = (clean_text(row.get("LU_DESC")) or "").upper()
    if not include_parking and "PARKING" in description:
        return False
    if not include_condo_main and "CONDO MAIN" in description:
        return False
    return housing_category(row, include_mixed_use=include_mixed_use) is not None


def assessment_record_from_row(
    row: dict[str, str],
    resource: AssessmentResource | None,
    source_csv: Path,
    extracted_at: str,
    assessment_year_override: int | None = None,
) -> AssessmentRecord:
    """Convert a normalized CSV row into the database mapping object."""

    assessment_year = (
        assessment_year_override
        if assessment_year_override
        else resource.fiscal_year if resource else parse_fiscal_year(source_csv.name)
    )
    category = housing_category(row, include_mixed_use=True)
    return AssessmentRecord(
        raw_row=row,
        source_name=SOURCE_NAME,
        source_dataset_url=SOURCE_DATASET_URL,
        source_resource_id=resource.resource_id if resource else None,
        source_resource_name=resource.name if resource else source_csv.name,
        source_resource_url=resource.url if resource else None,
        source_resource_modified_at=resource.metadata_modified_at if resource else None,
        source_file=str(source_csv),
        extracted_at=extracted_at,
        source_record_hash=source_record_hash(row),
        assessment_year=assessment_year,
        boston_pid=clean_identifier(row.get("PID")),
        cm_id=clean_identifier(row.get("CM_ID")),
        gis_id=clean_identifier(row.get("GIS_ID")),
        address_line1=address_line1(row),
        address_line2=address_line2(row),
        display_address=display_address(row),
        city=clean_text(row.get("CITY")),
        state_code="MA",
        postal_code=normalize_postal_code(row.get("ZIP_CODE") or row.get("ZIPCODE")),
        land_use_code=clean_text(row.get("LU")),
        land_use_numeric_code=normalize_luc(row.get("LUC")),
        land_use_description=clean_text(row.get("LU_DESC")),
        housing_category=category,
        property_type_code=property_type_code_for_category(category),
        building_type=clean_text(row.get("BLDG_TYPE")),
        owner_occupied=normalize_bool(row.get("OWN_OCC")),
        owner_name=clean_text(row.get("OWNER")),
        mail_addressee=clean_text(row.get("MAIL_ADDRESSEE")),
        mail_street_address=clean_text(row.get("MAIL_STREET_ADDRESS")),
        mail_city=clean_text(row.get("MAIL_CITY")),
        mail_state=clean_text(row.get("MAIL_STATE")),
        mail_postal_code=normalize_postal_code(row.get("MAIL_ZIP_CODE") or row.get("MAIL_ZIPCODE")),
        building_sequence=parse_integer(row.get("BLDG_SEQ")),
        building_count=parse_integer(row.get("NUM_BLDGS")),
        residential_units=parse_integer(row.get("RES_UNITS")),
        commercial_units=parse_integer(row.get("COM_UNITS")),
        rental_units=parse_integer(row.get("RC_UNITS")),
        land_area_sqft=parse_integer(row.get("LAND_SF")),
        gross_area_sqft=parse_integer(row.get("GROSS_AREA")),
        living_area_sqft=parse_integer(row.get("LIVING_AREA")),
        year_built=parse_integer(row.get("YR_BUILT")),
        year_remodeled=parse_integer(row.get("YR_REMODEL")),
        bedrooms=parse_integer(row.get("BED_RMS")),
        full_bathrooms=parse_integer(row.get("FULL_BTH")),
        half_bathrooms=parse_integer(row.get("HLF_BTH")),
        kitchens=parse_integer(row.get("KITCHENS")),
        total_rooms=parse_integer(row.get("TT_RMS")),
        parking_spaces=parse_integer(row.get("NUM_PARKING")),
        fireplaces=parse_integer(row.get("FIREPLACES")),
        stories=parse_decimal_string(row.get("RES_FLOOR")),
        land_value=parse_decimal_string(row.get("LAND_VALUE")),
        building_value=parse_decimal_string(row.get("BLDG_VALUE")),
        special_feature_value=parse_decimal_string(row.get("SFYI_VALUE")),
        total_assessed_value=parse_decimal_string(row.get("TOTAL_VALUE")),
        gross_tax=parse_decimal_string(row.get("GROSS_TAX")),
    )


def core_csv_row(record: AssessmentRecord) -> dict[str, str]:
    """Flatten the normalized record into the human-readable core CSV format."""

    values = {
        "source_name": record.source_name,
        "source_dataset_url": record.source_dataset_url,
        "source_resource_id": record.source_resource_id,
        "source_resource_name": record.source_resource_name,
        "source_resource_url": record.source_resource_url,
        "source_resource_modified_at": record.source_resource_modified_at,
        "source_file": record.source_file,
        "extracted_at": record.extracted_at,
        "raw_source_record_hash": record.source_record_hash,
        "assessment_year": record.assessment_year,
        "boston_pid": record.boston_pid,
        "cm_id": record.cm_id,
        "gis_id": record.gis_id,
        "address_line1": record.address_line1,
        "address_line2": record.address_line2,
        "display_address": record.display_address,
        "city": record.city,
        "state_code": record.state_code,
        "postal_code": record.postal_code,
        "land_use_code": record.land_use_code,
        "land_use_numeric_code": record.land_use_numeric_code,
        "land_use_description": record.land_use_description,
        "housing_category": record.housing_category,
        "building_type": record.building_type,
        "owner_occupied": record.owner_occupied,
        "owner_name": record.owner_name,
        "mail_addressee": record.mail_addressee,
        "mail_street_address": record.mail_street_address,
        "mail_city": record.mail_city,
        "mail_state": record.mail_state,
        "mail_postal_code": record.mail_postal_code,
        "building_sequence": record.building_sequence,
        "building_count": record.building_count,
        "residential_units": record.residential_units,
        "commercial_units": record.commercial_units,
        "rental_units": record.rental_units,
        "land_area_sqft": record.land_area_sqft,
        "gross_area_sqft": record.gross_area_sqft,
        "living_area_sqft": record.living_area_sqft,
        "year_built": record.year_built,
        "year_remodeled": record.year_remodeled,
        "bedrooms": record.bedrooms,
        "full_bathrooms": record.full_bathrooms,
        "half_bathrooms": record.half_bathrooms,
        "kitchens": record.kitchens,
        "total_rooms": record.total_rooms,
        "parking_spaces": record.parking_spaces,
        "fireplaces": record.fireplaces,
        "land_value": record.land_value,
        "building_value": record.building_value,
        "special_feature_value": record.special_feature_value,
        "total_assessed_value": record.total_assessed_value,
        "gross_tax": record.gross_tax,
    }
    return {key: csv_safe(values.get(key)) for key in CORE_COLUMNS}
