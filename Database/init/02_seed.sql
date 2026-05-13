-- Real Estate Data Pipeline - reference seed data.
--
-- Production-readiness goals:
--   * Idempotent: safe to run repeatedly during local rebuilds.
--   * Conservative: only load reference data that is stable and known.
--   * Relationally complete: parent rows are loaded before children, and
--     join tables are populated from canonical keys instead of hard-coded IDs.
--
-- PostgreSQL's Docker entrypoint only runs files in /docker-entrypoint-initdb.d
-- when the data directory is empty. Keeping this script idempotent still helps
-- when you rerun it manually against a development database.
BEGIN;

-- Load the 50 US states plus the District of Columbia.
--
-- `state_code` is the primary key in the schema, so use it as the conflict
-- target. If a row already exists, refresh the descriptive fields instead of
-- failing. This lets the seed file repair changed names/FIPS values without
-- deleting any rows that may already be referenced elsewhere.
INSERT INTO states (state_code, state_name, state_fips) VALUES
    ('AL', 'Alabama', '01'),
    ('AK', 'Alaska', '02'),
    ('AZ', 'Arizona', '04'),
    ('AR', 'Arkansas', '05'),
    ('CA', 'California', '06'),
    ('CO', 'Colorado', '08'),
    ('CT', 'Connecticut', '09'),
    ('DE', 'Delaware', '10'),
    ('DC', 'District of Columbia', '11'),
    ('FL', 'Florida', '12'),
    ('GA', 'Georgia', '13'),
    ('HI', 'Hawaii', '15'),
    ('ID', 'Idaho', '16'),
    ('IL', 'Illinois', '17'),
    ('IN', 'Indiana', '18'),
    ('IA', 'Iowa', '19'),
    ('KS', 'Kansas', '20'),
    ('KY', 'Kentucky', '21'),
    ('LA', 'Louisiana', '22'),
    ('ME', 'Maine', '23'),
    ('MD', 'Maryland', '24'),
    ('MA', 'Massachusetts', '25'),
    ('MI', 'Michigan', '26'),
    ('MN', 'Minnesota', '27'),
    ('MS', 'Mississippi', '28'),
    ('MO', 'Missouri', '29'),
    ('MT', 'Montana', '30'),
    ('NE', 'Nebraska', '31'),
    ('NV', 'Nevada', '32'),
    ('NH', 'New Hampshire', '33'),
    ('NJ', 'New Jersey', '34'),
    ('NM', 'New Mexico', '35'),
    ('NY', 'New York', '36'),
    ('NC', 'North Carolina', '37'),
    ('ND', 'North Dakota', '38'),
    ('OH', 'Ohio', '39'),
    ('OK', 'Oklahoma', '40'),
    ('OR', 'Oregon', '41'),
    ('PA', 'Pennsylvania', '42'),
    ('RI', 'Rhode Island', '44'),
    ('SC', 'South Carolina', '45'),
    ('SD', 'South Dakota', '46'),
    ('TN', 'Tennessee', '47'),
    ('TX', 'Texas', '48'),
    ('UT', 'Utah', '49'),
    ('VT', 'Vermont', '50'),
    ('VA', 'Virginia', '51'),
    ('WA', 'Washington', '53'),
    ('WV', 'West Virginia', '54'),
    ('WI', 'Wisconsin', '55'),
    ('WY', 'Wyoming', '56')
ON CONFLICT (state_code) DO UPDATE
SET
    state_name = EXCLUDED.state_name,
    state_fips = EXCLUDED.state_fips;

-- Load the source systems used or planned by this project.
--
-- These rows identify source systems; they do not imply that data has already
-- been ingested from each source. Keep source_name values stable because
-- ingestion tables reference them through source_id.
INSERT INTO sources (source_name, source_type, source_url, notes) VALUES
    ('analyze_boston', 'csv', 'https://data.boston.gov/', 'Publicly accessible Boston open data'),
    ('boston_property_lookup', 'api', 'https://property.boston.gov/', 'City of Boston property lookup records'),
    ('rentcast', 'api', 'https://www.rentcast.io/', 'RentCast property, listing, and rent estimate data'),
    ('mass_land_records', 'web', 'https://www.masslandrecords.com/', 'Massachusetts registry of deeds search records'),
    ('zillow', 'web', 'https://www.zillow.com/', 'Zillow search and property data')
ON CONFLICT (source_name) DO UPDATE
SET
    source_type = EXCLUDED.source_type,
    source_url = EXCLUDED.source_url,
    notes = EXCLUDED.notes;

-- Load Massachusetts counties.
--
-- County FIPS codes are stable Census identifiers. This loads all MA counties
-- so Boston-area expansion into nearby counties does not require a schema
-- rebuild. It intentionally does not load towns/cities or postal codes for
-- counties unless they are explicitly listed below.
INSERT INTO counties (state_code, county_name, county_fips) VALUES
    ('MA', 'Barnstable', '001'),
    ('MA', 'Berkshire', '003'),
    ('MA', 'Bristol', '005'),
    ('MA', 'Dukes', '007'),
    ('MA', 'Essex', '009'),
    ('MA', 'Franklin', '011'),
    ('MA', 'Hampden', '013'),
    ('MA', 'Hampshire', '015'),
    ('MA', 'Middlesex', '017'),
    ('MA', 'Nantucket', '019'),
    ('MA', 'Norfolk', '021'),
    ('MA', 'Plymouth', '023'),
    ('MA', 'Suffolk', '025'),
    ('MA', 'Worcester', '027')
ON CONFLICT (state_code, county_name) DO UPDATE
SET
    county_fips = EXCLUDED.county_fips;

-- Load Boston neighborhood/planning-district labels used by the postal-code
-- seed below.
--
-- These labels intentionally match the current source data vocabulary already
-- used by this project. Some are grouped labels such as Allston/Brighton and
-- Back Bay/Beacon Hill, so treat them as analysis districts rather than a
-- complete official neighborhood ontology.
INSERT INTO neighborhoods (city, state_code, neighborhood_name) VALUES
    ('Boston', 'MA', 'Allston/Brighton'),
    ('Boston', 'MA', 'Back Bay/Beacon Hill'),
    ('Boston', 'MA', 'Central'),
    ('Boston', 'MA', 'Charlestown'),
    ('Boston', 'MA', 'Dorchester'),
    ('Boston', 'MA', 'East Boston'),
    ('Boston', 'MA', 'Fenway/Kenmore'),
    ('Boston', 'MA', 'Hyde Park'),
    ('Boston', 'MA', 'Jamaica Plain'),
    ('Boston', 'MA', 'Mattapan'),
    ('Boston', 'MA', 'Roslindale'),
    ('Boston', 'MA', 'Roxbury'),
    ('Boston', 'MA', 'South Boston'),
    ('Boston', 'MA', 'South End'),
    ('Boston', 'MA', 'West Roxbury')
ON CONFLICT (city, state_code, neighborhood_name) DO UPDATE
SET
    neighborhood_name = EXCLUDED.neighborhood_name;

-- Load known Boston postal-code to primary-neighborhood labels.
--
-- The postal_codes.neighborhood column is a denormalized convenience label.
-- The normalized relationship is inserted into postal_code_neighborhoods below.
-- This seed deliberately avoids claiming to contain every Greater Boston postal
-- code. Add more rows only when you have an authoritative source for the
-- postal code, city, county, and neighborhood/district mapping.
WITH suffolk AS (
    SELECT county_id
    FROM counties
    WHERE state_code = 'MA'
      AND county_name = 'Suffolk'
      AND county_fips = '025'
),
postal_code_seed AS (
    SELECT *
    FROM (VALUES
        ('02134', 'MA', 'Boston', 'Allston/Brighton'),
        ('02135', 'MA', 'Boston', 'Allston/Brighton'),
        ('02163', 'MA', 'Boston', 'Allston/Brighton'),

        ('02108', 'MA', 'Boston', 'Back Bay/Beacon Hill'),
        ('02116', 'MA', 'Boston', 'Back Bay/Beacon Hill'),
        ('02117', 'MA', 'Boston', 'Back Bay/Beacon Hill'),
        ('02123', 'MA', 'Boston', 'Back Bay/Beacon Hill'),
        ('02133', 'MA', 'Boston', 'Back Bay/Beacon Hill'),
        ('02199', 'MA', 'Boston', 'Back Bay/Beacon Hill'),
        ('02216', 'MA', 'Boston', 'Back Bay/Beacon Hill'),
        ('02217', 'MA', 'Boston', 'Back Bay/Beacon Hill'),
        ('02295', 'MA', 'Boston', 'Back Bay/Beacon Hill'),

        ('02101', 'MA', 'Boston', 'Central'),
        ('02102', 'MA', 'Boston', 'Central'),
        ('02103', 'MA', 'Boston', 'Central'),
        ('02104', 'MA', 'Boston', 'Central'),
        ('02105', 'MA', 'Boston', 'Central'),
        ('02106', 'MA', 'Boston', 'Central'),
        ('02107', 'MA', 'Boston', 'Central'),
        ('02109', 'MA', 'Boston', 'Central'),
        ('02110', 'MA', 'Boston', 'Central'),
        ('02111', 'MA', 'Boston', 'Central'),
        ('02112', 'MA', 'Boston', 'Central'),
        ('02113', 'MA', 'Boston', 'Central'),
        ('02114', 'MA', 'Boston', 'Central'),
        ('02196', 'MA', 'Boston', 'Central'),
        ('02201', 'MA', 'Boston', 'Central'),
        ('02203', 'MA', 'Boston', 'Central'),

        ('02129', 'MA', 'Boston', 'Charlestown'),

        ('02122', 'MA', 'Boston', 'Dorchester'),
        ('02124', 'MA', 'Boston', 'Dorchester'),
        ('02125', 'MA', 'Boston', 'Dorchester'),

        ('02128', 'MA', 'Boston', 'East Boston'),
        ('02228', 'MA', 'Boston', 'East Boston'),

        ('02115', 'MA', 'Boston', 'Fenway/Kenmore'),

        ('02136', 'MA', 'Boston', 'Hyde Park'),

        ('02130', 'MA', 'Boston', 'Jamaica Plain'),

        ('02126', 'MA', 'Boston', 'Mattapan'),

        ('02131', 'MA', 'Boston', 'Roslindale'),

        ('02119', 'MA', 'Boston', 'Roxbury'),
        ('02120', 'MA', 'Boston', 'Roxbury'),
        ('02121', 'MA', 'Boston', 'Roxbury'),

        ('02127', 'MA', 'Boston', 'South Boston'),

        ('02118', 'MA', 'Boston', 'South End'),

        ('02132', 'MA', 'Boston', 'West Roxbury')
    ) AS v(postal_code, state_code, city, neighborhood)
)
INSERT INTO postal_codes (
    postal_code,
    state_code,
    city,
    neighborhood,
    county_id
)
SELECT
    pcs.postal_code,
    pcs.state_code,
    pcs.city,
    pcs.neighborhood,
    s.county_id
FROM postal_code_seed pcs
CROSS JOIN suffolk s
ON CONFLICT (postal_code) DO UPDATE
SET
    state_code = EXCLUDED.state_code,
    city = EXCLUDED.city,
    neighborhood = EXCLUDED.neighborhood,
    county_id = EXCLUDED.county_id;

-- Populate the many-to-many postal code/neighborhood bridge from natural keys.
--
-- This avoids hard-coded generated IDs and guarantees that the bridge agrees
-- with the postal_codes.neighborhood labels loaded above. If a future postal
-- code spans multiple neighborhoods, insert additional bridge rows and leave
-- exactly one row marked is_primary = true.
WITH postal_code_seed AS (
    SELECT *
    FROM (VALUES
        ('02134', 'Boston', 'MA', 'Allston/Brighton'),
        ('02135', 'Boston', 'MA', 'Allston/Brighton'),
        ('02163', 'Boston', 'MA', 'Allston/Brighton'),

        ('02108', 'Boston', 'MA', 'Back Bay/Beacon Hill'),
        ('02116', 'Boston', 'MA', 'Back Bay/Beacon Hill'),
        ('02117', 'Boston', 'MA', 'Back Bay/Beacon Hill'),
        ('02123', 'Boston', 'MA', 'Back Bay/Beacon Hill'),
        ('02133', 'Boston', 'MA', 'Back Bay/Beacon Hill'),
        ('02199', 'Boston', 'MA', 'Back Bay/Beacon Hill'),
        ('02216', 'Boston', 'MA', 'Back Bay/Beacon Hill'),
        ('02217', 'Boston', 'MA', 'Back Bay/Beacon Hill'),
        ('02295', 'Boston', 'MA', 'Back Bay/Beacon Hill'),

        ('02101', 'Boston', 'MA', 'Central'),
        ('02102', 'Boston', 'MA', 'Central'),
        ('02103', 'Boston', 'MA', 'Central'),
        ('02104', 'Boston', 'MA', 'Central'),
        ('02105', 'Boston', 'MA', 'Central'),
        ('02106', 'Boston', 'MA', 'Central'),
        ('02107', 'Boston', 'MA', 'Central'),
        ('02109', 'Boston', 'MA', 'Central'),
        ('02110', 'Boston', 'MA', 'Central'),
        ('02111', 'Boston', 'MA', 'Central'),
        ('02112', 'Boston', 'MA', 'Central'),
        ('02113', 'Boston', 'MA', 'Central'),
        ('02114', 'Boston', 'MA', 'Central'),
        ('02196', 'Boston', 'MA', 'Central'),
        ('02201', 'Boston', 'MA', 'Central'),
        ('02203', 'Boston', 'MA', 'Central'),

        ('02129', 'Boston', 'MA', 'Charlestown'),

        ('02122', 'Boston', 'MA', 'Dorchester'),
        ('02124', 'Boston', 'MA', 'Dorchester'),
        ('02125', 'Boston', 'MA', 'Dorchester'),

        ('02128', 'Boston', 'MA', 'East Boston'),
        ('02228', 'Boston', 'MA', 'East Boston'),

        ('02115', 'Boston', 'MA', 'Fenway/Kenmore'),

        ('02136', 'Boston', 'MA', 'Hyde Park'),

        ('02130', 'Boston', 'MA', 'Jamaica Plain'),

        ('02126', 'Boston', 'MA', 'Mattapan'),

        ('02131', 'Boston', 'MA', 'Roslindale'),

        ('02119', 'Boston', 'MA', 'Roxbury'),
        ('02120', 'Boston', 'MA', 'Roxbury'),
        ('02121', 'Boston', 'MA', 'Roxbury'),

        ('02127', 'Boston', 'MA', 'South Boston'),

        ('02118', 'Boston', 'MA', 'South End'),

        ('02132', 'Boston', 'MA', 'West Roxbury')
    ) AS v(postal_code, city, state_code, neighborhood_name)
)
INSERT INTO postal_code_neighborhoods (
    postal_code,
    neighborhood_id,
    is_primary
)
SELECT
    pcs.postal_code,
    n.neighborhood_id,
    TRUE
FROM postal_code_seed pcs
JOIN neighborhoods n
  ON n.city = pcs.city
 AND n.state_code = pcs.state_code
 AND n.neighborhood_name = pcs.neighborhood_name
ON CONFLICT (postal_code, neighborhood_id) DO UPDATE
SET
    is_primary = EXCLUDED.is_primary;

-- Fail fast if future edits accidentally introduce orphaned or mismatched seed
-- data. These checks are intentionally inside the transaction so a bad seed
-- file rolls back cleanly.
DO $$
DECLARE
    missing_bridge_count INTEGER;
    missing_primary_count INTEGER;
BEGIN
    SELECT COUNT(*)
    INTO missing_bridge_count
    FROM postal_codes pc
    LEFT JOIN postal_code_neighborhoods pcn
      ON pcn.postal_code = pc.postal_code
    WHERE pc.city = 'Boston'
      AND pc.state_code = 'MA'
      AND pcn.postal_code IS NULL;

    IF missing_bridge_count > 0 THEN
        RAISE EXCEPTION 'Seed validation failed: % Boston postal_codes rows have no postal_code_neighborhoods row',
            missing_bridge_count;
    END IF;

    SELECT COUNT(*)
    INTO missing_primary_count
    FROM postal_codes pc
    WHERE pc.city = 'Boston'
      AND pc.state_code = 'MA'
      AND NOT EXISTS (
          SELECT 1
          FROM postal_code_neighborhoods pcn
          JOIN neighborhoods n
            ON n.neighborhood_id = pcn.neighborhood_id
          WHERE pcn.postal_code = pc.postal_code
            AND pcn.is_primary
            AND n.city = pc.city
            AND n.state_code = pc.state_code
            AND n.neighborhood_name = pc.neighborhood
      );

    IF missing_primary_count > 0 THEN
        RAISE EXCEPTION 'Seed validation failed: % Boston postal_codes rows do not match their primary neighborhood bridge row',
            missing_primary_count;
    END IF;
END $$;

-- Load transaction type reference data.
INSERT INTO transaction_types (transaction_type_code, transaction_type_name, description)
VALUES
    ('sale', 'Sale', 'Property sale transaction'),
    ('deed_transfer', 'Deed Transfer', 'Recorded deed transfer'),
    ('listed_for_sale', 'Listed For Sale', 'Property listed for sale'),
    ('listed_for_rent', 'Listed For Rent', 'Property listed for rent'),
    ('rental_event', 'Rental Event', 'Rental market event')
ON CONFLICT (transaction_type_code) DO UPDATE
SET
    transaction_type_name = EXCLUDED.transaction_type_name,
    description = EXCLUDED.description;

-- Loads reference data for listing statuses.
--
--
INSERT INTO listing_statuses (status_code, status_name)
VALUES
    ('active', 'Active'),
    ('pending', 'Pending'),
    ('sold', 'Sold'),
    ('rented', 'Rented'),
    ('off_market', 'Off Market'),
    ('expired', 'Expired')
ON CONFLICT (status_code) DO UPDATE
SET
    status_name = EXCLUDED.status_name;

-- Loads reference data for property types.
--
-- These are intentionally broad categories. Source-specific assessor land-use
-- codes belong in land_use_types once you decide which official code list to
-- standardize on.
INSERT INTO property_types (property_type_code, property_type_name, property_category)
VALUES
    ('residential', 'Residential Property', 'residential'),
    ('condo', 'Condo', 'residential'),
    ('multifamily', 'Multifamily', 'residential'),
    ('three_family', 'Three-Family Dwelling', 'residential'),
    ('commercial', 'Commercial Property', 'commercial'),
    ('mixed_use', 'Mixed Use', 'mixed_use')
ON CONFLICT (property_type_code) DO UPDATE
SET
    property_type_name = EXCLUDED.property_type_name,
    property_category = EXCLUDED.property_category;

-- Intentionally not populated yet:
--   * land_use_types: should come from a chosen official assessor code list.
--   * feature_definitions: should come from fields accepted by normalization.
--   * ma_registry_districts: add only after choosing canonical registry names
--     and URLs.

COMMIT;
