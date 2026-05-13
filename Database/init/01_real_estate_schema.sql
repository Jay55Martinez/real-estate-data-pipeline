-- Real Estate Data Pipeline - Initial PostgreSQL Schema
-- Safe to run multiple times for most objects.

BEGIN;

-- ============================================================
-- Extensions
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;

-- NOTE:
-- PostGIS requires a PostGIS-enabled PostgreSQL image/package.
-- If this fails, either install PostGIS or remove geom columns/indexes.
CREATE EXTENSION IF NOT EXISTS postgis;

-- ============================================================
-- Helper trigger for updated_at columns
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Reference Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS sources (
    source_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    source_url TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS states (
    state_code CHAR(2) PRIMARY KEY,
    state_name TEXT NOT NULL,
    state_fips TEXT
);

CREATE TABLE IF NOT EXISTS counties (
    county_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    state_code CHAR(2) NOT NULL REFERENCES states(state_code),
    county_name TEXT NOT NULL,
    county_fips TEXT,
    CONSTRAINT counties_state_name_unique UNIQUE (state_code, county_name),
    CONSTRAINT counties_state_fips_unique UNIQUE (state_code, county_fips)
);

CREATE TABLE IF NOT EXISTS postal_codes (
    postal_code TEXT PRIMARY KEY,
    state_code CHAR(2) REFERENCES states(state_code),
    city TEXT,
    -- Denormalized primary neighborhood/planning-district label for convenient
    -- filtering. The normalized many-to-many relationship lives in
    -- postal_code_neighborhoods because postal codes and neighborhoods do not
    -- map one-to-one.
    neighborhood TEXT,
    county_id BIGINT REFERENCES counties(county_id)
);

CREATE INDEX IF NOT EXISTS idx_postal_codes_state_city ON postal_codes(state_code, city);
CREATE INDEX IF NOT EXISTS idx_postal_codes_county_id ON postal_codes(county_id);
CREATE INDEX IF NOT EXISTS idx_postal_codes_neighborhood ON postal_codes(neighborhood);

CREATE TABLE IF NOT EXISTS neighborhoods (
    neighborhood_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    city TEXT NOT NULL,
    state_code CHAR(2) NOT NULL REFERENCES states(state_code),
    neighborhood_name TEXT NOT NULL,
    CONSTRAINT neighborhoods_unique UNIQUE (city, state_code, neighborhood_name)
);

CREATE TABLE IF NOT EXISTS postal_code_neighborhoods (
    postal_code TEXT NOT NULL REFERENCES postal_codes(postal_code),
    neighborhood_id BIGINT NOT NULL REFERENCES neighborhoods(neighborhood_id),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (postal_code, neighborhood_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_postal_code_neighborhoods_one_primary
ON postal_code_neighborhoods(postal_code)
WHERE is_primary;

CREATE TABLE IF NOT EXISTS property_types (
    property_type_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_type_code TEXT UNIQUE,
    property_type_name TEXT NOT NULL,
    property_category TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS land_use_types (
    land_use_type_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    land_use_code TEXT NOT NULL UNIQUE,
    land_use_name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS feature_definitions (
    feature_definition_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feature_group TEXT,
    feature_name TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK (value_type IN ('text', 'integer', 'numeric', 'boolean', 'date', 'json')),
    unit TEXT,
    description TEXT,
    CONSTRAINT feature_definitions_unique UNIQUE (feature_group, feature_name)
);

CREATE TABLE IF NOT EXISTS transaction_types (
    transaction_type_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transaction_type_code TEXT NOT NULL UNIQUE,
    transaction_type_name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS listing_statuses (
    listing_status_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status_code TEXT NOT NULL UNIQUE,
    status_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_or_organizations (
    party_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    party_name TEXT NOT NULL,
    party_type TEXT CHECK (party_type IN ('person', 'organization', 'trust', 'government', 'unknown')),
    normalized_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_parties_normalized_name ON person_or_organizations(normalized_name);

-- ============================================================
-- Ingestion Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS ingestion_runs (
    ingestion_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'partial')),
    source_query JSONB,
    records_read INTEGER NOT NULL DEFAULT 0,
    records_inserted INTEGER NOT NULL DEFAULT 0,
    records_updated INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source_started ON ingestion_runs(source_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_status_started ON ingestion_runs(status, started_at DESC);

CREATE TABLE IF NOT EXISTS raw_source_records (
    raw_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_run_id UUID NOT NULL REFERENCES ingestion_runs(ingestion_run_id),
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    source_record_key TEXT,
    source_record_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    normalized_at TIMESTAMPTZ,
    normalization_status TEXT NOT NULL DEFAULT 'pending',
    normalization_error TEXT,
    CONSTRAINT raw_source_records_unique_hash UNIQUE (source_id, source_record_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_source_records_source_key ON raw_source_records(source_id, source_record_key);
CREATE INDEX IF NOT EXISTS idx_raw_source_records_status_observed ON raw_source_records(normalization_status, observed_at);
CREATE INDEX IF NOT EXISTS idx_raw_source_records_payload_gin ON raw_source_records USING GIN (payload);

-- ============================================================
-- Address and Property Core
-- ============================================================

CREATE TABLE IF NOT EXISTS addresses (
    address_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    address_line1 TEXT NOT NULL,
    address_line2 TEXT,
    city TEXT,
    state_code CHAR(2) REFERENCES states(state_code),
    postal_code TEXT REFERENCES postal_codes(postal_code),
    county_id BIGINT REFERENCES counties(county_id),
    formatted_address TEXT,
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    geom GEOGRAPHY(Point, 4326),
    address_hash TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_addresses_postal_code ON addresses(postal_code);
CREATE INDEX IF NOT EXISTS idx_addresses_city_state ON addresses(city, state_code);
CREATE INDEX IF NOT EXISTS idx_addresses_geom ON addresses USING GIST (geom);

DROP TRIGGER IF EXISTS trg_addresses_updated_at ON addresses;
CREATE TRIGGER trg_addresses_updated_at
BEFORE UPDATE ON addresses
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS properties (
    property_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_address_id BIGINT REFERENCES addresses(address_id),
    property_type_id BIGINT REFERENCES property_types(property_type_id),
    land_use_type_id BIGINT REFERENCES land_use_types(land_use_type_id),
    county_id BIGINT REFERENCES counties(county_id),
    state_code CHAR(2) REFERENCES states(state_code),
    postal_code TEXT REFERENCES postal_codes(postal_code),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    display_address TEXT,
    city TEXT,
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    geom GEOGRAPHY(Point, 4326),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_properties_postal_code ON properties(postal_code);
CREATE INDEX IF NOT EXISTS idx_properties_county_id ON properties(county_id);
CREATE INDEX IF NOT EXISTS idx_properties_property_type_id ON properties(property_type_id);
CREATE INDEX IF NOT EXISTS idx_properties_land_use_type_id ON properties(land_use_type_id);
CREATE INDEX IF NOT EXISTS idx_properties_display_address_trgm ON properties USING GIN (display_address gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_properties_geom ON properties USING GIST (geom);

DROP TRIGGER IF EXISTS trg_properties_updated_at ON properties;
CREATE TRIGGER trg_properties_updated_at
BEFORE UPDATE ON properties
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS property_source_ids (
    property_source_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    source_property_key TEXT NOT NULL,
    source_property_key_type TEXT NOT NULL,
    valid_from DATE,
    valid_to DATE,
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_source_ids_unique_key UNIQUE (source_id, source_property_key_type, source_property_key)
);

CREATE INDEX IF NOT EXISTS idx_property_source_ids_property_source ON property_source_ids(property_id, source_id);

CREATE TABLE IF NOT EXISTS property_physical_attributes (
    property_id BIGINT PRIMARY KEY REFERENCES properties(property_id) ON DELETE CASCADE,
    building_count INTEGER,
    residential_units INTEGER,
    commercial_units INTEGER,
    rental_units INTEGER,
    bedrooms INTEGER,
    full_bathrooms INTEGER,
    half_bathrooms INTEGER,
    bathrooms_total NUMERIC(4,1),
    kitchens INTEGER,
    total_rooms INTEGER,
    gross_area_sqft INTEGER,
    living_area_sqft INTEGER CHECK (living_area_sqft >= 0),
    land_area_sqft INTEGER CHECK (land_area_sqft >= 0),
    year_built INTEGER CHECK (year_built BETWEEN 1600 AND 2200),
    year_remodeled INTEGER,
    parking_spaces INTEGER,
    fireplaces INTEGER,
    stories NUMERIC(5,2),
    last_observed_at TIMESTAMPTZ,
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_property_physical_attributes_updated_at ON property_physical_attributes;
CREATE TRIGGER trg_property_physical_attributes_updated_at
BEFORE UPDATE ON property_physical_attributes
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS property_features (
    property_feature_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    feature_definition_id BIGINT NOT NULL REFERENCES feature_definitions(feature_definition_id),
    value_text TEXT,
    value_integer INTEGER,
    value_numeric NUMERIC(14,4),
    value_boolean BOOLEAN,
    value_date DATE,
    value_json JSONB,
    source_id BIGINT REFERENCES sources(source_id),
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_features_unique_observation UNIQUE (property_id, feature_definition_id, source_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_property_features_property_definition ON property_features(property_id, feature_definition_id);
CREATE INDEX IF NOT EXISTS idx_property_features_definition_value_text ON property_features(feature_definition_id, value_text);
CREATE INDEX IF NOT EXISTS idx_property_features_value_json ON property_features USING GIN (value_json);

CREATE TABLE IF NOT EXISTS property_owners (
    property_owner_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    party_id BIGINT NOT NULL REFERENCES person_or_organizations(party_id),
    mailing_address_id BIGINT REFERENCES addresses(address_id),
    ownership_role TEXT,
    ownership_percent NUMERIC(7,4),
    start_date DATE,
    end_date DATE,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    source_id BIGINT REFERENCES sources(source_id),
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_property_owners_property_current ON property_owners(property_id, is_current);
CREATE INDEX IF NOT EXISTS idx_property_owners_party_id ON property_owners(party_id);
CREATE INDEX IF NOT EXISTS idx_property_owners_mailing_address_id ON property_owners(mailing_address_id);

-- ============================================================
-- Assessments and Taxes
-- ============================================================

CREATE TABLE IF NOT EXISTS property_assessments (
    assessment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    assessment_year INTEGER NOT NULL CHECK (assessment_year BETWEEN 1600 AND 2200),
    assessment_date DATE,
    land_value NUMERIC(14,2),
    building_value NUMERIC(14,2),
    special_feature_value NUMERIC(14,2),
    total_assessed_value NUMERIC(14,2) NOT NULL,
    source_property_key TEXT,
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_assessments_unique_year UNIQUE (property_id, source_id, assessment_year)
);

CREATE INDEX IF NOT EXISTS idx_property_assessments_year ON property_assessments(assessment_year);
CREATE INDEX IF NOT EXISTS idx_property_assessments_property_year ON property_assessments(property_id, assessment_year DESC);
CREATE INDEX IF NOT EXISTS idx_property_assessments_total_value ON property_assessments(total_assessed_value);

CREATE TABLE IF NOT EXISTS property_taxes (
    tax_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    tax_year INTEGER NOT NULL,
    gross_tax_amount NUMERIC(14,2),
    net_tax_amount NUMERIC(14,2),
    total_billed_amount NUMERIC(14,2),
    residential_exemption_flag BOOLEAN,
    personal_exemption_flag BOOLEAN,
    residential_exemption_amount NUMERIC(14,2),
    personal_exemption_amount NUMERIC(14,2),
    community_preservation_amount NUMERIC(14,2),
    other_charges JSONB,
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_taxes_unique_year UNIQUE (property_id, source_id, tax_year)
);

CREATE INDEX IF NOT EXISTS idx_property_taxes_year ON property_taxes(tax_year);
CREATE INDEX IF NOT EXISTS idx_property_taxes_property_year ON property_taxes(property_id, tax_year DESC);

-- ============================================================
-- Transactions, Deeds, Listings, Rent
-- ============================================================

CREATE TABLE IF NOT EXISTS property_transactions (
    transaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    transaction_type_id BIGINT NOT NULL REFERENCES transaction_types(transaction_type_id),
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    transaction_date DATE,
    recorded_date DATE,
    amount NUMERIC(14,2),
    book_number TEXT,
    page_number TEXT,
    document_number TEXT,
    instrument_type TEXT,
    source_transaction_key TEXT,
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_transactions_unique_source_key UNIQUE (source_id, source_transaction_key)
);

CREATE INDEX IF NOT EXISTS idx_property_transactions_property_date ON property_transactions(property_id, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_property_transactions_date ON property_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_property_transactions_amount ON property_transactions(amount);
CREATE INDEX IF NOT EXISTS idx_property_transactions_book_page ON property_transactions(book_number, page_number);
CREATE INDEX IF NOT EXISTS idx_property_transactions_document ON property_transactions(document_number);

CREATE TABLE IF NOT EXISTS transaction_parties (
    transaction_party_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transaction_id BIGINT NOT NULL REFERENCES property_transactions(transaction_id) ON DELETE CASCADE,
    party_id BIGINT NOT NULL REFERENCES person_or_organizations(party_id),
    party_role TEXT NOT NULL,
    CONSTRAINT transaction_parties_unique_role UNIQUE (transaction_id, party_id, party_role)
);

CREATE TABLE IF NOT EXISTS listings (
    listing_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    listing_status_id BIGINT REFERENCES listing_statuses(listing_status_id),
    listing_type TEXT NOT NULL CHECK (listing_type IN ('sale', 'rent')),
    list_date DATE,
    removed_date DATE,
    price NUMERIC(14,2),
    rent_period TEXT,
    bedrooms INTEGER,
    bathrooms NUMERIC(4,1),
    living_area_sqft INTEGER,
    source_listing_key TEXT,
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT listings_unique_observation UNIQUE (source_id, source_listing_key, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_listings_property_observed ON listings(property_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_type_status_observed ON listings(listing_type, listing_status_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price);

CREATE TABLE IF NOT EXISTS listing_price_history (
    listing_price_history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id BIGINT NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    changed_at TIMESTAMPTZ NOT NULL,
    old_price NUMERIC(14,2),
    new_price NUMERIC(14,2),
    old_listing_status_id BIGINT REFERENCES listing_statuses(listing_status_id),
    new_listing_status_id BIGINT REFERENCES listing_statuses(listing_status_id),
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id)
);

CREATE INDEX IF NOT EXISTS idx_listing_price_history_listing_changed ON listing_price_history(listing_id, changed_at DESC);

CREATE TABLE IF NOT EXISTS rental_estimates (
    rental_estimate_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES sources(source_id),
    estimate_date DATE NOT NULL,
    estimated_rent NUMERIC(14,2),
    rent_low NUMERIC(14,2),
    rent_high NUMERIC(14,2),
    bedrooms INTEGER,
    bathrooms NUMERIC(4,1),
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT rental_estimates_unique_observation UNIQUE (property_id, source_id, estimate_date, bedrooms, bathrooms)
);

CREATE INDEX IF NOT EXISTS idx_rental_estimates_property_date ON rental_estimates(property_id, estimate_date DESC);
CREATE INDEX IF NOT EXISTS idx_rental_estimates_date ON rental_estimates(estimate_date);

-- ============================================================
-- Massachusetts / Boston Specific Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS ma_registry_districts (
    registry_district_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    district_name TEXT NOT NULL UNIQUE,
    county_id BIGINT REFERENCES counties(county_id),
    registry_url TEXT
);

CREATE TABLE IF NOT EXISTS ma_property_registry_refs (
    ma_property_registry_ref_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    registry_district_id BIGINT REFERENCES ma_registry_districts(registry_district_id),
    book_number TEXT,
    page_number TEXT,
    certificate_number TEXT,
    plan_number TEXT,
    source_id BIGINT REFERENCES sources(source_id),
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ma_registry_refs_district_book_page ON ma_property_registry_refs(registry_district_id, book_number, page_number);
CREATE INDEX IF NOT EXISTS idx_ma_registry_refs_property ON ma_property_registry_refs(property_id);

CREATE TABLE IF NOT EXISTS boston_assessor_details (
    property_id BIGINT PRIMARY KEY REFERENCES properties(property_id) ON DELETE CASCADE,
    boston_pid TEXT NOT NULL UNIQUE,
    cm_id TEXT,
    gis_id TEXT,
    building_sequence INTEGER,
    num_buildings INTEGER,
    luc TEXT,
    lu TEXT,
    lu_description TEXT,
    building_type TEXT,
    owner_occupied BOOLEAN,
    personal_exemption_flag BOOLEAN,
    residential_exemption_flag BOOLEAN,
    child_parcel_count INTEGER,
    master_parcel_id TEXT,
    image_url TEXT,
    source_id BIGINT REFERENCES sources(source_id),
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_boston_assessor_details_gis_id ON boston_assessor_details(gis_id);
CREATE INDEX IF NOT EXISTS idx_boston_assessor_details_master_parcel_id ON boston_assessor_details(master_parcel_id);
CREATE INDEX IF NOT EXISTS idx_boston_assessor_details_lu ON boston_assessor_details(lu);

DROP TRIGGER IF EXISTS trg_boston_assessor_details_updated_at ON boston_assessor_details;
CREATE TRIGGER trg_boston_assessor_details_updated_at
BEFORE UPDATE ON boston_assessor_details
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- Analytical Views
-- ============================================================

CREATE OR REPLACE VIEW latest_property_assessment AS
SELECT *
FROM (
    SELECT
        pa.*,
        ROW_NUMBER() OVER (
            PARTITION BY property_id
            ORDER BY assessment_year DESC, created_at DESC
        ) AS rn
    FROM property_assessments pa
) ranked
WHERE rn = 1;

CREATE OR REPLACE VIEW latest_property_sale AS
SELECT *
FROM (
    SELECT
        pt.*,
        tt.transaction_type_code,
        ROW_NUMBER() OVER (
            PARTITION BY pt.property_id
            ORDER BY pt.transaction_date DESC NULLS LAST, pt.recorded_date DESC NULLS LAST, pt.created_at DESC
        ) AS rn
    FROM property_transactions pt
    JOIN transaction_types tt ON tt.transaction_type_id = pt.transaction_type_id
    WHERE tt.transaction_type_code IN ('sale', 'deed_transfer')
) ranked
WHERE rn = 1;

CREATE OR REPLACE VIEW active_listings AS
SELECT
    l.*
FROM listings l
JOIN listing_statuses ls ON ls.listing_status_id = l.listing_status_id
WHERE ls.status_code = 'active';

CREATE OR REPLACE VIEW current_property_summary AS
SELECT
    p.property_id,
    p.display_address,
    p.city,
    p.state_code,
    p.postal_code,
    p.latitude,
    p.longitude,
    pt.property_type_name,
    lut.land_use_name,
    ppa.bedrooms,
    ppa.bathrooms_total,
    ppa.living_area_sqft,
    ppa.land_area_sqft,
    ppa.year_built,
    lpa.assessment_year AS latest_assessment_year,
    lpa.total_assessed_value AS latest_assessed_value,
    lps.transaction_date AS latest_sale_date,
    lps.amount AS latest_sale_amount
FROM properties p
LEFT JOIN property_types pt ON pt.property_type_id = p.property_type_id
LEFT JOIN land_use_types lut ON lut.land_use_type_id = p.land_use_type_id
LEFT JOIN property_physical_attributes ppa ON ppa.property_id = p.property_id
LEFT JOIN latest_property_assessment lpa ON lpa.property_id = p.property_id
LEFT JOIN latest_property_sale lps ON lps.property_id = p.property_id;

COMMIT;
