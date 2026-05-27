"""Shared Analyze Boston property assessment ingestion settings."""

from __future__ import annotations

from pathlib import Path


CKAN_PACKAGE_URL = (
    "https://data.boston.gov/api/3/action/package_show?id=property-assessment"
)
SOURCE_NAME = "analyze_boston_assessment"
SOURCE_DATASET_URL = "https://data.boston.gov/dataset/property-assessment"
DEFAULT_ZIP_CODES = ("02128",)
DEFAULT_CITY_NAMES = ("EAST BOSTON",)
DEFAULT_OUTPUT_ROOT = Path("data")
DEFAULT_DB_ENV_FILE = Path("Database/.env")
RAW_RECORD_BATCH_SIZE = 1_000
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

# Boston assessor LU/LUC values that represent the residential universe this
# pipeline should build first. Mixed use can be included because many Boston
# parcels combine ground-floor commercial space with housing above.
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
