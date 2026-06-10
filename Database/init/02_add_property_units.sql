-- Real Estate Data Pipeline - Add market-discovered property units
-- Run this after 01_real_estate_schema.sql on an existing database.

BEGIN;

-- ============================================================
-- Property Units
-- ============================================================
-- Represents market-discovered rentable/listable units that belong to an
-- official assessor property. These units may not have their own Boston PID.
CREATE TABLE IF NOT EXISTS property_units (
    unit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    source_id BIGINT REFERENCES sources(source_id),
    source_unit_key TEXT,
    unit_label TEXT,
    unit_normalized TEXT NOT NULL,
    unit_type TEXT CHECK (unit_type IN ('apartment', 'condo', 'room', 'building', 'unknown')),
    bedrooms INTEGER,
    bathrooms NUMERIC(4,1),
    living_area_sqft INTEGER CHECK (living_area_sqft >= 0),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence_score NUMERIC(4,3) CHECK (confidence_score BETWEEN 0 AND 1),
    status TEXT NOT NULL DEFAULT 'unverified' CHECK (status IN ('unverified', 'verified', 'active', 'inactive', 'conflicting')),
    raw_record_id UUID REFERENCES raw_source_records(raw_record_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT property_units_unique_unit UNIQUE (property_id, unit_normalized),
    CONSTRAINT property_units_unique_source_key UNIQUE (source_id, source_unit_key)
);

CREATE INDEX IF NOT EXISTS idx_property_units_property_id ON property_units(property_id);
CREATE INDEX IF NOT EXISTS idx_property_units_source_key ON property_units(source_id, source_unit_key);
CREATE INDEX IF NOT EXISTS idx_property_units_status_seen ON property_units(status, last_seen_at DESC);

DROP TRIGGER IF EXISTS trg_property_units_updated_at ON property_units;
CREATE TRIGGER trg_property_units_updated_at
BEFORE UPDATE ON property_units
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- Connect market data to optional units
-- ============================================================
ALTER TABLE listings
ADD COLUMN IF NOT EXISTS unit_id BIGINT REFERENCES property_units(unit_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_listings_unit_observed ON listings(unit_id, observed_at DESC);

ALTER TABLE rental_estimates
ADD COLUMN IF NOT EXISTS unit_id BIGINT REFERENCES property_units(unit_id) ON DELETE SET NULL;

-- Replace the property-only uniqueness rule with separate rules for property-level
-- estimates and unit-level estimates. This avoids duplicate unit estimates while
-- still allowing multiple units under the same parent property.
ALTER TABLE rental_estimates
DROP CONSTRAINT IF EXISTS rental_estimates_unique_observation;

CREATE UNIQUE INDEX IF NOT EXISTS rental_estimates_unique_property_observation
ON rental_estimates(property_id, source_id, estimate_date, bedrooms, bathrooms)
WHERE unit_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS rental_estimates_unique_unit_observation
ON rental_estimates(unit_id, source_id, estimate_date, bedrooms, bathrooms)
WHERE unit_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rental_estimates_unit_date ON rental_estimates(unit_id, estimate_date DESC);

COMMIT;
