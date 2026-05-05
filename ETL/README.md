# ETL

This directory is the pipeline entry point for source ingestion code. The first
source is Analyze Boston property assessment data, which defines the initial
East Boston property universe before later enrichment from Boston Property
Lookup, Mass Land Records, RentCast, Zillow, and other sources.

## Analyze Boston Assessment Extract

Run against the newest CSV published in the Analyze Boston CKAN catalog:

```bash
python3 -m ETL.analyze_boston.property_assessment
```

Run against the checked-in development CSV:

```bash
python3 -m ETL.analyze_boston.property_assessment \
  --source-csv "Analyze Boston/fy2026-property-assessment-data_12_23_2025.csv"
```

Extract and load raw housing records into PostgreSQL:

```bash
.venv/bin/python -m ETL.analyze_boston.property_assessment \
  --source-csv "Analyze Boston/fy2026-property-assessment-data_12_23_2025.csv" \
  --load-db
```

Database settings are read from `DATABASE_URL` first, then `Database/.env`.
The load currently writes the landing tables only: `sources`, `ingestion_runs`,
and `raw_source_records`. The next pipeline stage should normalize those raw
records into `properties`, `property_source_ids`, `property_assessments`,
`property_taxes`, `property_physical_attributes`, and
`boston_assessor_details`.

Default geography is East Boston:

- ZIP code: `02128`
- City: `EAST BOSTON`
- Match mode: city OR ZIP, so minor source inconsistencies are retained

Outputs are written under `data/extracted/analyze_boston/`:

- `east_boston_property_assessment_raw_fyYYYY.csv`: source rows for the target geography
- `east_boston_housing_raw_fyYYYY.jsonl`: raw housing rows shaped for `raw_source_records`
- `east_boston_housing_core_fyYYYY.csv`: high-level normalized fields for the core database tables
- `east_boston_manifest_fyYYYY.json`: source metadata, run config, counts, and output paths

The script is intentionally download/extract only. Loading into PostgreSQL
should be the next stage, using the schema's `sources`, `ingestion_runs`,
`raw_source_records`, `properties`, `property_source_ids`,
`property_physical_attributes`, `property_assessments`, `property_taxes`, and
`boston_assessor_details` tables.

Future orchestration can wrap this module as the first startup task in Prefect:

1. Discover and cache the latest annual Analyze Boston CSV.
2. Extract scoped geography/housing records.
3. Load raw JSONL into `raw_source_records`.
4. Upsert high-level core CSV into normalized tables.
5. Trigger downstream enrichments only for changed/new source record hashes.
