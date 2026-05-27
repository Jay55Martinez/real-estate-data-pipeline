"""PostgreSQL loading for normalized Analyze Boston assessment records."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from ETL.analyze_boston.config import RAW_RECORD_BATCH_SIZE, SOURCE_NAME
from ETL.analyze_boston.normalize import (
    AssessmentRecord,
    assessment_record_from_row,
    clean_text,
    mailing_address_hash,
    property_address_hash,
)
from ETL.analyze_boston.source import AssessmentResource


LOGGER = logging.getLogger(__name__)
LOAD_PROGRESS_INTERVAL = 1_000


FEATURE_COLUMNS: dict[str, tuple[str, str]] = {
    # source column: (feature group, feature name)
    "STRUCTURE_CLASS": ("assessor_building", "structure_class"),
    "ROOF_STRUCTURE": ("assessor_building", "roof_structure"),
    "ROOF_COVER": ("assessor_building", "roof_cover"),
    "INT_WALL": ("assessor_condition", "interior_wall"),
    "EXT_FNISHED": ("assessor_condition", "exterior_finish"),
    "INT_COND": ("assessor_condition", "interior_condition"),
    "EXT_COND": ("assessor_condition", "exterior_condition"),
    "OVERALL_COND": ("assessor_condition", "overall_condition"),
    "BDRM_COND": ("assessor_condition", "bedroom_condition"),
    "BTHRM_STYLE1": ("assessor_finish", "bathroom_style_1"),
    "BTHRM_STYLE2": ("assessor_finish", "bathroom_style_2"),
    "BTHRM_STYLE3": ("assessor_finish", "bathroom_style_3"),
    "KITCHEN_TYPE": ("assessor_finish", "kitchen_type"),
    "KITCHEN_STYLE1": ("assessor_finish", "kitchen_style_1"),
    "KITCHEN_STYLE2": ("assessor_finish", "kitchen_style_2"),
    "KITCHEN_STYLE3": ("assessor_finish", "kitchen_style_3"),
    "HEAT_TYPE": ("assessor_systems", "heat_type"),
    "HEAT_SYSTEM": ("assessor_systems", "heat_system"),
    "AC_TYPE": ("assessor_systems", "ac_type"),
    "CD_FLOOR": ("assessor_unit", "condo_floor"),
    "ORIENTATION": ("assessor_unit", "orientation"),
    "PROP_VIEW": ("assessor_unit", "property_view"),
    "CORNER_UNIT": ("assessor_unit", "corner_unit"),
}


@dataclass
class LoadCounts:
    """Database loading counters for the final CLI summary."""

    source_id: int
    ingestion_run_id: str
    raw_records_read: int = 0
    raw_records_inserted: int = 0
    raw_records_skipped: int = 0
    properties_inserted: int = 0
    properties_updated: int = 0
    assessments_upserted: int = 0
    taxes_upserted: int = 0
    owners_linked: int = 0
    features_inserted: int = 0


def read_jsonl_batches(
    path: Path,
    batch_size: int = RAW_RECORD_BATCH_SIZE,
) -> Iterable[list[dict[str, Any]]]:
    """Yield JSONL records in batches so large annual files stay memory-light."""

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


def ensure_source(connection, source_url: str) -> int:
    """Create or update the source row and return its generated ID."""

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
    """Open an ingestion run before any row-level loading starts."""

    result = connection.execute(
        text(
            """
            INSERT INTO ingestion_runs (source_id, status, source_query)
            VALUES (:source_id, 'running', CAST(:source_query AS jsonb))
            RETURNING ingestion_run_id
            """
        ),
        {"source_id": source_id, "source_query": json.dumps(source_query, sort_keys=True)},
    )
    return str(result.scalar_one())


def finish_ingestion_run(
    connection,
    ingestion_run_id: str,
    status: str,
    records_read: int,
    records_inserted: int,
    records_updated: int,
    error_message: str | None = None,
) -> None:
    """Mark an ingestion run complete or failed with final counters."""

    connection.execute(
        text(
            """
            UPDATE ingestion_runs
            SET
                finished_at = NOW(),
                status = :status,
                records_read = :records_read,
                records_inserted = :records_inserted,
                records_updated = :records_updated,
                error_message = :error_message
            WHERE ingestion_run_id = :ingestion_run_id
            """
        ),
        {
            "ingestion_run_id": ingestion_run_id,
            "status": status,
            "records_read": records_read,
            "records_inserted": records_inserted,
            "records_updated": records_updated,
            "error_message": error_message,
        },
    )


def ensure_reference_data(connection, record: AssessmentRecord) -> dict[str, int | None]:
    """Ensure lookup rows required by one property record exist."""

    connection.execute(
        text(
            """
            INSERT INTO states (state_code, state_name, state_fips)
            VALUES ('MA', 'Massachusetts', '25')
            ON CONFLICT (state_code) DO NOTHING
            """
        )
    )
    county_id = connection.execute(
        text(
            """
            INSERT INTO counties (state_code, county_name, county_fips)
            VALUES ('MA', 'Suffolk', '025')
            ON CONFLICT (state_code, county_name) DO UPDATE
            SET county_fips = EXCLUDED.county_fips
            RETURNING county_id
            """
        )
    ).scalar_one()

    if record.postal_code:
        connection.execute(
            text(
                """
                INSERT INTO postal_codes (postal_code, state_code, city, neighborhood, county_id)
                VALUES (:postal_code, 'MA', 'Boston', 'East Boston', :county_id)
                ON CONFLICT (postal_code) DO UPDATE
                SET
                    state_code = COALESCE(postal_codes.state_code, EXCLUDED.state_code),
                    city = COALESCE(postal_codes.city, EXCLUDED.city),
                    neighborhood = COALESCE(postal_codes.neighborhood, EXCLUDED.neighborhood),
                    county_id = COALESCE(postal_codes.county_id, EXCLUDED.county_id)
                """
            ),
            {"postal_code": record.postal_code, "county_id": county_id},
        )

    property_type_id = connection.execute(
        text(
            """
            INSERT INTO property_types (
                property_type_code,
                property_type_name,
                property_category
            )
            VALUES (:code, :name, :category)
            ON CONFLICT (property_type_code) DO UPDATE
            SET
                property_type_name = EXCLUDED.property_type_name,
                property_category = EXCLUDED.property_category
            RETURNING property_type_id
            """
        ),
        {
            "code": record.property_type_code,
            "name": record.property_type_code.replace("_", " ").title(),
            "category": (
                "mixed_use"
                if record.property_type_code == "mixed_use"
                else "residential"
            ),
        },
    ).scalar_one()

    land_use_type_id = None
    if record.land_use_numeric_code:
        land_use_type_id = connection.execute(
            text(
                """
                INSERT INTO land_use_types (land_use_code, land_use_name, description)
                VALUES (:code, :name, :description)
                ON CONFLICT (land_use_code) DO UPDATE
                SET
                    land_use_name = EXCLUDED.land_use_name,
                    description = EXCLUDED.description
                RETURNING land_use_type_id
                """
            ),
            {
                "code": record.land_use_numeric_code,
                "name": record.land_use_description or record.land_use_numeric_code,
                "description": f"Boston assessor LU={record.land_use_code or ''}".strip(),
            },
        ).scalar_one()

    return {
        "county_id": int(county_id),
        "property_type_id": int(property_type_id),
        "land_use_type_id": int(land_use_type_id) if land_use_type_id else None,
    }


def insert_raw_source_record(
    connection,
    source_id: int,
    ingestion_run_id: str,
    item: dict[str, Any],
) -> tuple[str, bool]:
    """Insert a raw row or return the existing raw_record_id for duplicates."""

    payload = json.dumps(item["payload"], sort_keys=True)
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
            RETURNING raw_record_id
            """
        ),
        {
            "ingestion_run_id": ingestion_run_id,
            "source_id": source_id,
            "source_record_key": item.get("source_record_key"),
            "source_record_hash": item["source_record_hash"],
            "payload": payload,
            "observed_at": item.get("observed_at"),
        },
    ).scalar()
    if result:
        return str(result), True

    existing = connection.execute(
        text(
            """
            SELECT raw_record_id
            FROM raw_source_records
            WHERE source_id = :source_id
              AND source_record_hash = :source_record_hash
            """
        ),
        {"source_id": source_id, "source_record_hash": item["source_record_hash"]},
    ).scalar_one()
    return str(existing), False


def upsert_address(
    connection,
    *,
    address_line1: str | None,
    address_line2: str | None,
    city: str | None,
    state_code: str | None,
    postal_code: str | None,
    county_id: int | None,
    formatted_address: str | None,
    address_hash: str | None,
) -> int | None:
    """Upsert an address and return its ID; missing line1 means no address."""

    if not address_line1 or not address_hash:
        return None
    ensure_address_reference_data(
        connection,
        state_code=state_code,
        postal_code=postal_code,
        city=city,
        county_id=county_id,
    )
    result = connection.execute(
        text(
            """
            INSERT INTO addresses (
                address_line1,
                address_line2,
                city,
                state_code,
                postal_code,
                county_id,
                formatted_address,
                address_hash
            )
            VALUES (
                :address_line1,
                :address_line2,
                :city,
                :state_code,
                :postal_code,
                :county_id,
                :formatted_address,
                :address_hash
            )
            ON CONFLICT (address_hash) DO UPDATE
            SET
                address_line1 = EXCLUDED.address_line1,
                address_line2 = EXCLUDED.address_line2,
                city = EXCLUDED.city,
                state_code = EXCLUDED.state_code,
                postal_code = EXCLUDED.postal_code,
                county_id = EXCLUDED.county_id,
                formatted_address = EXCLUDED.formatted_address
            RETURNING address_id
            """
        ),
        {
            "address_line1": address_line1,
            "address_line2": address_line2,
            "city": city,
            "state_code": state_code,
            "postal_code": postal_code,
            "county_id": county_id,
            "formatted_address": formatted_address,
            "address_hash": address_hash,
        },
    )
    return int(result.scalar_one())


def ensure_address_reference_data(
    connection,
    *,
    state_code: str | None,
    postal_code: str | None,
    city: str | None,
    county_id: int | None,
) -> None:
    """Ensure FK rows exist before inserting property or mailing addresses.

    The seed file covers Boston ZIP codes, but assessor mailing addresses can
    point to Revere, Salem, New Hampshire, and beyond. For those addresses we
    preserve the source city/state/ZIP with a minimal postal_codes row.
    """

    if state_code:
        connection.execute(
            text(
                """
                INSERT INTO states (state_code, state_name)
                VALUES (:state_code, :state_name)
                ON CONFLICT (state_code) DO NOTHING
                """
            ),
            {"state_code": state_code, "state_name": state_code},
        )
    if postal_code:
        connection.execute(
            text(
                """
                INSERT INTO postal_codes (postal_code, state_code, city, county_id)
                VALUES (:postal_code, :state_code, :city, :county_id)
                ON CONFLICT (postal_code) DO UPDATE
                SET
                    state_code = COALESCE(postal_codes.state_code, EXCLUDED.state_code),
                    city = COALESCE(postal_codes.city, EXCLUDED.city),
                    county_id = COALESCE(postal_codes.county_id, EXCLUDED.county_id)
                """
            ),
            {
                "postal_code": postal_code,
                "state_code": state_code,
                "city": city.title() if city else None,
                "county_id": county_id,
            },
        )


def find_property_id(connection, source_id: int, source_property_key: str | None) -> int | None:
    """Look up an existing property by the source's stable parcel key."""

    if not source_property_key:
        return None
    return connection.execute(
        text(
            """
            SELECT property_id
            FROM property_source_ids
            WHERE source_id = :source_id
              AND source_property_key_type = 'boston_pid'
              AND source_property_key = :source_property_key
            """
        ),
        {"source_id": source_id, "source_property_key": source_property_key},
    ).scalar()


def upsert_property(
    connection,
    record: AssessmentRecord,
    refs: dict[str, int | None],
    canonical_address_id: int | None,
) -> tuple[int, bool]:
    """Insert or update the canonical property row tied to a Boston PID."""

    existing_property_id = find_property_id(connection, int(refs["source_id"]), record.boston_pid)
    params = {
        "canonical_address_id": canonical_address_id,
        "property_type_id": refs["property_type_id"],
        "land_use_type_id": refs["land_use_type_id"],
        "county_id": refs["county_id"],
        "state_code": record.state_code,
        "postal_code": record.postal_code,
        "display_address": record.display_address,
        "city": record.city,
    }
    if existing_property_id:
        connection.execute(
            text(
                """
                UPDATE properties
                SET
                    canonical_address_id = :canonical_address_id,
                    property_type_id = :property_type_id,
                    land_use_type_id = :land_use_type_id,
                    county_id = :county_id,
                    state_code = :state_code,
                    postal_code = :postal_code,
                    display_address = :display_address,
                    city = :city,
                    is_active = TRUE
                WHERE property_id = :property_id
                """
            ),
            {**params, "property_id": existing_property_id},
        )
        return int(existing_property_id), False

    property_id = connection.execute(
        text(
            """
            INSERT INTO properties (
                canonical_address_id,
                property_type_id,
                land_use_type_id,
                county_id,
                state_code,
                postal_code,
                display_address,
                city,
                is_active
            )
            VALUES (
                :canonical_address_id,
                :property_type_id,
                :land_use_type_id,
                :county_id,
                :state_code,
                :postal_code,
                :display_address,
                :city,
                TRUE
            )
            RETURNING property_id
            """
        ),
        params,
    ).scalar_one()
    return int(property_id), True


def upsert_property_source_id(
    connection,
    property_id: int,
    source_id: int,
    record: AssessmentRecord,
    raw_record_id: str,
) -> None:
    """Maintain the source-to-canonical-property bridge."""

    if not record.boston_pid:
        return
    connection.execute(
        text(
            """
            INSERT INTO property_source_ids (
                property_id,
                source_id,
                source_property_key,
                source_property_key_type,
                raw_record_id
            )
            VALUES (
                :property_id,
                :source_id,
                :source_property_key,
                'boston_pid',
                :raw_record_id
            )
            ON CONFLICT (source_id, source_property_key_type, source_property_key)
            DO UPDATE
            SET
                property_id = EXCLUDED.property_id,
                raw_record_id = EXCLUDED.raw_record_id
            """
        ),
        {
            "property_id": property_id,
            "source_id": source_id,
            "source_property_key": record.boston_pid,
            "raw_record_id": raw_record_id,
        },
    )


def bathroom_total(record: AssessmentRecord) -> float | None:
    """Compute total bathrooms from full and half bath source fields."""

    if record.full_bathrooms is None and record.half_bathrooms is None:
        return None
    return float(record.full_bathrooms or 0) + float(record.half_bathrooms or 0) * 0.5


def upsert_physical_attributes(
    connection,
    property_id: int,
    record: AssessmentRecord,
    raw_record_id: str,
) -> None:
    """Upsert stable physical facts from the assessment row."""

    connection.execute(
        text(
            """
            INSERT INTO property_physical_attributes (
                property_id,
                building_count,
                residential_units,
                commercial_units,
                rental_units,
                bedrooms,
                full_bathrooms,
                half_bathrooms,
                bathrooms_total,
                kitchens,
                total_rooms,
                gross_area_sqft,
                living_area_sqft,
                land_area_sqft,
                year_built,
                year_remodeled,
                parking_spaces,
                fireplaces,
                stories,
                last_observed_at,
                raw_record_id
            )
            VALUES (
                :property_id,
                :building_count,
                :residential_units,
                :commercial_units,
                :rental_units,
                :bedrooms,
                :full_bathrooms,
                :half_bathrooms,
                :bathrooms_total,
                :kitchens,
                :total_rooms,
                :gross_area_sqft,
                :living_area_sqft,
                :land_area_sqft,
                :year_built,
                :year_remodeled,
                :parking_spaces,
                :fireplaces,
                CAST(:stories AS numeric),
                NOW(),
                :raw_record_id
            )
            ON CONFLICT (property_id) DO UPDATE
            SET
                building_count = EXCLUDED.building_count,
                residential_units = EXCLUDED.residential_units,
                commercial_units = EXCLUDED.commercial_units,
                rental_units = EXCLUDED.rental_units,
                bedrooms = EXCLUDED.bedrooms,
                full_bathrooms = EXCLUDED.full_bathrooms,
                half_bathrooms = EXCLUDED.half_bathrooms,
                bathrooms_total = EXCLUDED.bathrooms_total,
                kitchens = EXCLUDED.kitchens,
                total_rooms = EXCLUDED.total_rooms,
                gross_area_sqft = EXCLUDED.gross_area_sqft,
                living_area_sqft = EXCLUDED.living_area_sqft,
                land_area_sqft = EXCLUDED.land_area_sqft,
                year_built = EXCLUDED.year_built,
                year_remodeled = EXCLUDED.year_remodeled,
                parking_spaces = EXCLUDED.parking_spaces,
                fireplaces = EXCLUDED.fireplaces,
                stories = EXCLUDED.stories,
                last_observed_at = EXCLUDED.last_observed_at,
                raw_record_id = EXCLUDED.raw_record_id
            """
        ),
        {
            "property_id": property_id,
            "building_count": record.building_count,
            "residential_units": record.residential_units,
            "commercial_units": record.commercial_units,
            "rental_units": record.rental_units,
            "bedrooms": record.bedrooms,
            "full_bathrooms": record.full_bathrooms,
            "half_bathrooms": record.half_bathrooms,
            "bathrooms_total": bathroom_total(record),
            "kitchens": record.kitchens,
            "total_rooms": record.total_rooms,
            "gross_area_sqft": record.gross_area_sqft,
            "living_area_sqft": record.living_area_sqft,
            "land_area_sqft": record.land_area_sqft,
            "year_built": record.year_built,
            "year_remodeled": record.year_remodeled,
            "parking_spaces": record.parking_spaces,
            "fireplaces": record.fireplaces,
            "stories": record.stories,
            "raw_record_id": raw_record_id,
        },
    )


def upsert_assessment_and_tax(
    connection,
    property_id: int,
    source_id: int,
    record: AssessmentRecord,
    raw_record_id: str,
) -> tuple[bool, bool]:
    """Upsert assessment value and tax rows when the source has values."""

    assessment_written = False
    tax_written = False
    if record.assessment_year and record.total_assessed_value:
        connection.execute(
            text(
                """
                INSERT INTO property_assessments (
                    property_id,
                    source_id,
                    assessment_year,
                    land_value,
                    building_value,
                    special_feature_value,
                    total_assessed_value,
                    source_property_key,
                    raw_record_id
                )
                VALUES (
                    :property_id,
                    :source_id,
                    :assessment_year,
                    CAST(:land_value AS numeric),
                    CAST(:building_value AS numeric),
                    CAST(:special_feature_value AS numeric),
                    CAST(:total_assessed_value AS numeric),
                    :source_property_key,
                    :raw_record_id
                )
                ON CONFLICT (property_id, source_id, assessment_year) DO UPDATE
                SET
                    land_value = EXCLUDED.land_value,
                    building_value = EXCLUDED.building_value,
                    special_feature_value = EXCLUDED.special_feature_value,
                    total_assessed_value = EXCLUDED.total_assessed_value,
                    source_property_key = EXCLUDED.source_property_key,
                    raw_record_id = EXCLUDED.raw_record_id
                """
            ),
            {
                "property_id": property_id,
                "source_id": source_id,
                "assessment_year": record.assessment_year,
                "land_value": record.land_value,
                "building_value": record.building_value,
                "special_feature_value": record.special_feature_value,
                "total_assessed_value": record.total_assessed_value,
                "source_property_key": record.boston_pid,
                "raw_record_id": raw_record_id,
            },
        )
        assessment_written = True

    if record.assessment_year and record.gross_tax:
        connection.execute(
            text(
                """
                INSERT INTO property_taxes (
                    property_id,
                    source_id,
                    tax_year,
                    gross_tax_amount,
                    raw_record_id
                )
                VALUES (
                    :property_id,
                    :source_id,
                    :tax_year,
                    CAST(:gross_tax_amount AS numeric),
                    :raw_record_id
                )
                ON CONFLICT (property_id, source_id, tax_year) DO UPDATE
                SET
                    gross_tax_amount = EXCLUDED.gross_tax_amount,
                    raw_record_id = EXCLUDED.raw_record_id
                """
            ),
            {
                "property_id": property_id,
                "source_id": source_id,
                "tax_year": record.assessment_year,
                "gross_tax_amount": record.gross_tax,
                "raw_record_id": raw_record_id,
            },
        )
        tax_written = True

    return assessment_written, tax_written


def upsert_boston_details(
    connection,
    property_id: int,
    source_id: int,
    record: AssessmentRecord,
    raw_record_id: str,
) -> None:
    """Upsert Boston-specific assessor identifiers and land-use descriptors."""

    if not record.boston_pid:
        return
    connection.execute(
        text(
            """
            INSERT INTO boston_assessor_details (
                property_id,
                boston_pid,
                cm_id,
                gis_id,
                building_sequence,
                num_buildings,
                luc,
                lu,
                lu_description,
                building_type,
                owner_occupied,
                source_id,
                raw_record_id
            )
            VALUES (
                :property_id,
                :boston_pid,
                :cm_id,
                :gis_id,
                :building_sequence,
                :num_buildings,
                :luc,
                :lu,
                :lu_description,
                :building_type,
                :owner_occupied,
                :source_id,
                :raw_record_id
            )
            ON CONFLICT (property_id) DO UPDATE
            SET
                boston_pid = EXCLUDED.boston_pid,
                cm_id = EXCLUDED.cm_id,
                gis_id = EXCLUDED.gis_id,
                building_sequence = EXCLUDED.building_sequence,
                num_buildings = EXCLUDED.num_buildings,
                luc = EXCLUDED.luc,
                lu = EXCLUDED.lu,
                lu_description = EXCLUDED.lu_description,
                building_type = EXCLUDED.building_type,
                owner_occupied = EXCLUDED.owner_occupied,
                source_id = EXCLUDED.source_id,
                raw_record_id = EXCLUDED.raw_record_id
            """
        ),
        {
            "property_id": property_id,
            "boston_pid": record.boston_pid,
            "cm_id": record.cm_id,
            "gis_id": record.gis_id,
            "building_sequence": record.building_sequence,
            "num_buildings": record.building_count,
            "luc": record.land_use_numeric_code,
            "lu": record.land_use_code,
            "lu_description": record.land_use_description,
            "building_type": record.building_type,
            "owner_occupied": record.owner_occupied,
            "source_id": source_id,
            "raw_record_id": raw_record_id,
        },
    )


def normalize_party_name(name: str) -> str:
    """Create a simple search key for owner de-duplication."""

    return re.sub(r"\s+", " ", name.strip().upper())


def infer_party_type(name: str) -> str:
    """Infer person vs organization conservatively from common legal suffixes."""

    org_markers = {"LLC", "INC", "CORP", "TRUST", "REALTY", "BANK", "LP", "LLP"}
    words = set(re.sub(r"[^A-Z0-9 ]", " ", name.upper()).split())
    if "TRUST" in words:
        return "trust"
    if words & org_markers:
        return "organization"
    return "unknown"


def upsert_owner(
    connection,
    property_id: int,
    source_id: int,
    record: AssessmentRecord,
    mailing_address_id: int | None,
    raw_record_id: str,
) -> bool:
    """Upsert owner party and current ownership link for the property."""

    if not record.owner_name:
        return False

    normalized_name = normalize_party_name(record.owner_name)
    party_id = connection.execute(
        text(
            """
            SELECT party_id
            FROM person_or_organizations
            WHERE normalized_name = :normalized_name
            LIMIT 1
            """
        ),
        {"normalized_name": normalized_name},
    ).scalar()
    if not party_id:
        party_id = connection.execute(
            text(
                """
                INSERT INTO person_or_organizations (
                    party_name,
                    party_type,
                    normalized_name
                )
                VALUES (:party_name, :party_type, :normalized_name)
                RETURNING party_id
                """
            ),
            {
                "party_name": record.owner_name,
                "party_type": infer_party_type(record.owner_name),
                "normalized_name": normalized_name,
            },
        ).scalar_one()

    # End any previous current owner link for this property/source when the
    # owner changed, then insert the latest current owner if it is not present.
    connection.execute(
        text(
            """
            UPDATE property_owners
            SET is_current = FALSE
            WHERE property_id = :property_id
              AND source_id = :source_id
              AND party_id <> :party_id
              AND is_current = TRUE
            """
        ),
        {"property_id": property_id, "source_id": source_id, "party_id": party_id},
    )
    existing_link = connection.execute(
        text(
            """
            SELECT property_owner_id
            FROM property_owners
            WHERE property_id = :property_id
              AND party_id = :party_id
              AND source_id = :source_id
              AND is_current = TRUE
            LIMIT 1
            """
        ),
        {"property_id": property_id, "party_id": party_id, "source_id": source_id},
    ).scalar()
    if existing_link:
        connection.execute(
            text(
                """
                UPDATE property_owners
                SET
                    mailing_address_id = :mailing_address_id,
                    raw_record_id = :raw_record_id
                WHERE property_owner_id = :property_owner_id
                """
            ),
            {
                "property_owner_id": existing_link,
                "mailing_address_id": mailing_address_id,
                "raw_record_id": raw_record_id,
            },
        )
        return False

    connection.execute(
        text(
            """
            INSERT INTO property_owners (
                property_id,
                party_id,
                mailing_address_id,
                ownership_role,
                is_current,
                source_id,
                raw_record_id
            )
            VALUES (
                :property_id,
                :party_id,
                :mailing_address_id,
                'assessed_owner',
                TRUE,
                :source_id,
                :raw_record_id
            )
            """
        ),
        {
            "property_id": property_id,
            "party_id": party_id,
            "mailing_address_id": mailing_address_id,
            "source_id": source_id,
            "raw_record_id": raw_record_id,
        },
    )
    return True


def ensure_feature_definition(connection, feature_group: str, feature_name: str) -> int:
    """Create a feature definition for assessor-specific descriptive fields."""

    return int(
        connection.execute(
            text(
                """
                INSERT INTO feature_definitions (
                    feature_group,
                    feature_name,
                    value_type,
                    description
                )
                VALUES (:feature_group, :feature_name, 'text', :description)
                ON CONFLICT (feature_group, feature_name) DO UPDATE
                SET description = EXCLUDED.description
                RETURNING feature_definition_id
                """
            ),
            {
                "feature_group": feature_group,
                "feature_name": feature_name,
                "description": "Analyze Boston assessor descriptive attribute",
            },
        ).scalar_one()
    )


def insert_features(
    connection,
    property_id: int,
    source_id: int,
    record: AssessmentRecord,
    raw_record_id: str,
    observed_at: str | None,
) -> int:
    """Insert source-specific descriptive features that lack dedicated columns."""

    inserted = 0
    for column, (feature_group, feature_name) in FEATURE_COLUMNS.items():
        value = clean_text(record.raw_row.get(column))
        if value is None:
            continue
        feature_definition_id = ensure_feature_definition(connection, feature_group, feature_name)
        result = connection.execute(
            text(
                """
                INSERT INTO property_features (
                    property_id,
                    feature_definition_id,
                    value_text,
                    source_id,
                    raw_record_id,
                    observed_at
                )
                VALUES (
                    :property_id,
                    :feature_definition_id,
                    :value_text,
                    :source_id,
                    :raw_record_id,
                    CAST(:observed_at AS timestamptz)
                )
                ON CONFLICT (property_id, feature_definition_id, source_id, observed_at)
                DO UPDATE
                SET
                    value_text = EXCLUDED.value_text,
                    raw_record_id = EXCLUDED.raw_record_id
                """
            ),
            {
                "property_id": property_id,
                "feature_definition_id": feature_definition_id,
                "value_text": value,
                "source_id": source_id,
                "raw_record_id": raw_record_id,
                "observed_at": observed_at,
            },
        )
        inserted += int(result.rowcount or 0)
    return inserted


def load_record(
    connection,
    source_id: int,
    record: AssessmentRecord,
    raw_record_id: str,
    observed_at: str | None,
) -> dict[str, int]:
    """Load one normalized row into all canonical tables."""

    refs = ensure_reference_data(connection, record)
    refs["source_id"] = source_id

    canonical_address_id = upsert_address(
        connection,
        address_line1=record.address_line1,
        address_line2=record.address_line2,
        city=record.city,
        state_code=record.state_code,
        postal_code=record.postal_code,
        county_id=refs["county_id"],
        formatted_address=record.display_address,
        address_hash=property_address_hash(record),
    )
    property_id, inserted_property = upsert_property(
        connection,
        record=record,
        refs=refs,
        canonical_address_id=canonical_address_id,
    )
    upsert_property_source_id(connection, property_id, source_id, record, raw_record_id)
    upsert_physical_attributes(connection, property_id, record, raw_record_id)
    assessment_written, tax_written = upsert_assessment_and_tax(
        connection,
        property_id,
        source_id,
        record,
        raw_record_id,
    )
    upsert_boston_details(connection, property_id, source_id, record, raw_record_id)

    mailing_address_id = upsert_address(
        connection,
        address_line1=record.mail_street_address,
        address_line2=None,
        city=record.mail_city,
        state_code=record.mail_state,
        postal_code=record.mail_postal_code,
        county_id=None,
        formatted_address=", ".join(
            part
            for part in (
                record.mail_street_address,
                record.mail_city,
                record.mail_state,
                record.mail_postal_code,
            )
            if part
        )
        or None,
        address_hash=mailing_address_hash(record),
    )
    owner_linked = upsert_owner(
        connection,
        property_id,
        source_id,
        record,
        mailing_address_id,
        raw_record_id,
    )
    features_inserted = insert_features(
        connection,
        property_id,
        source_id,
        record,
        raw_record_id,
        observed_at,
    )
    return {
        "properties_inserted": int(inserted_property),
        "properties_updated": int(not inserted_property),
        "assessments_upserted": int(assessment_written),
        "taxes_upserted": int(tax_written),
        "owners_linked": int(owner_linked),
        "features_inserted": features_inserted,
    }


def load_assessments_to_postgres(
    engine,
    raw_housing_jsonl: Path,
    source_query: dict[str, Any],
    source_url: str,
    source_csv: Path,
    resource: AssessmentResource | None,
    assessment_year_override: int | None = None,
) -> LoadCounts:
    """Load raw JSONL and canonical tables inside one ingestion run."""

    LOGGER.info("Preparing PostgreSQL ingestion run...")
    with engine.begin() as connection:
        source_id = ensure_source(connection, source_url=source_url)
        ingestion_run_id = start_ingestion_run(
            connection,
            source_id=source_id,
            source_query=source_query,
        )

    counts = LoadCounts(source_id=source_id, ingestion_run_id=ingestion_run_id)
    LOGGER.info(
        "Started ingestion_run_id=%s for source_id=%s.",
        ingestion_run_id,
        source_id,
    )
    LOGGER.info("Loading raw and canonical records from %s...", raw_housing_jsonl)

    try:
        with engine.begin() as connection:
            for batch in read_jsonl_batches(raw_housing_jsonl):
                for item in batch:
                    counts.raw_records_read += 1
                    if counts.raw_records_read % LOAD_PROGRESS_INTERVAL == 0:
                        LOGGER.info(
                            "Loaded %s rows so far: %s new raw, %s properties "
                            "inserted, %s properties updated.",
                            f"{counts.raw_records_read:,}",
                            f"{counts.raw_records_inserted:,}",
                            f"{counts.properties_inserted:,}",
                            f"{counts.properties_updated:,}",
                        )
                    raw_record_id, inserted_raw = insert_raw_source_record(
                        connection,
                        source_id=source_id,
                        ingestion_run_id=ingestion_run_id,
                        item=item,
                    )
                    counts.raw_records_inserted += int(inserted_raw)

                    record = assessment_record_from_row(
                        item["payload"],
                        resource=resource,
                        source_csv=source_csv,
                        extracted_at=item.get("observed_at") or "",
                        assessment_year_override=(
                            assessment_year_override or item.get("assessment_year")
                        ),
                    )
                    row_counts = load_record(
                        connection,
                        source_id=source_id,
                        record=record,
                        raw_record_id=raw_record_id,
                        observed_at=item.get("observed_at"),
                    )
                    counts.properties_inserted += row_counts["properties_inserted"]
                    counts.properties_updated += row_counts["properties_updated"]
                    counts.assessments_upserted += row_counts["assessments_upserted"]
                    counts.taxes_upserted += row_counts["taxes_upserted"]
                    counts.owners_linked += row_counts["owners_linked"]
                    counts.features_inserted += row_counts["features_inserted"]

            counts.raw_records_skipped = counts.raw_records_read - counts.raw_records_inserted
            finish_ingestion_run(
                connection,
                ingestion_run_id=ingestion_run_id,
                status="succeeded",
                records_read=counts.raw_records_read,
                records_inserted=counts.raw_records_inserted,
                records_updated=counts.properties_updated,
            )
            LOGGER.info(
                "Database load complete: processed %s rows; inserted %s raw "
                "records; skipped %s duplicates.",
                f"{counts.raw_records_read:,}",
                f"{counts.raw_records_inserted:,}",
                f"{counts.raw_records_skipped:,}",
            )
    except Exception as exc:
        LOGGER.exception("Database load failed; marking ingestion run failed.")
        with engine.begin() as connection:
            finish_ingestion_run(
                connection,
                ingestion_run_id=ingestion_run_id,
                status="failed",
                records_read=counts.raw_records_read,
                records_inserted=counts.raw_records_inserted,
                records_updated=counts.properties_updated,
                error_message=str(exc),
            )
        raise

    return counts
