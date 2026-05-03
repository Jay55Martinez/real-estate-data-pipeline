Schema Design Insights
======================

Design goal
-----------
The schema is designed for a real estate data pipeline that collects data from
multiple sources, including Analyze Boston CSV assessment data, Boston Property
Lookup JSON responses, RentCast API data, and Mass Land Records deed searches.
The main goal is to preserve raw source data while building a clean PostgreSQL
model that supports later analysis.


Key decisions
=============

1. Keep raw source payloads
---------------------------
I added sources, ingestion_runs, and raw_source_records.

Reason:
Source formats will change, scrapers will improve, and normalization logic will
need to be rerun. Keeping raw JSON/CSV rows in PostgreSQL lets you reprocess old
data without scraping or downloading it again. It also gives you an audit trail
when two sources disagree.


2. Use surrogate primary keys for core entities
-----------------------------------------------
The properties table uses property_id as a generated bigint primary key instead
of using Boston PID, RentCast ID, or another external identifier.

Reason:
No source-specific ID is universal. Boston PID is useful for Boston assessor
data, but it will not identify RentCast-only properties, non-Boston properties,
or unit-level records in every source. Surrogate keys keep the core model stable.


3. Put source-specific IDs in property_source_ids
-------------------------------------------------
I added a mapping table for Boston PID, GIS_ID, RentCast IDs, registry
identifiers, and future source keys.

Reason:
This allows multiple identifiers to point to one canonical property. It also
makes deduplication easier because a property can be matched by parcel ID,
address, coordinates, or a source-specific key without overloading the
properties table.


4. Separate canonical data from Boston/Massachusetts-specific data
------------------------------------------------------------------
I added boston_assessor_details, ma_registry_districts, and
ma_property_registry_refs.

Reason:
Boston fields such as PID, LUC, LU, owner occupancy, and master parcel ID are
important, but they are not universal real estate concepts. Keeping them in
extension tables prevents the generic property table from turning into a wide
table full of nulls as more locations and sources are added.


5. Move is_listed out of properties
-----------------------------------
The original outline included is_listed on the property table. I replaced this
with listings and listing_statuses.

Reason:
Listing status is time-based. A property can be listed, delisted, listed for
rent, then later sold. Storing a single boolean on properties would lose history
and create conflicting updates between sources.


6. Store last_sale_price as a derived value
-------------------------------------------
The original transactions outline included last_sale_price. I modeled sales and
deed transfers in property_transactions instead.

Reason:
last_sale_price should be calculated from the latest sale transaction. Storing
it separately increases the chance that it becomes stale or disagrees with the
transaction history.


7. Use numeric for money and coordinates, not double
----------------------------------------------------
Money fields are numeric(14,2). Latitude and longitude are numeric(10,7), with
an optional PostGIS geography point.

Reason:
Floating point values can introduce rounding errors. Real estate analysis often
compares values, taxes, rents, and prices, so exact decimal storage is safer.
PostGIS is recommended for distance, containment, and neighborhood/spatial
queries when you are ready for it.


8. Split addresses into their own table
---------------------------------------
I added addresses and linked properties and mailing addresses to it.

Reason:
Properties have situs addresses, owners have mailing addresses, and source data
often repeats address fields. A shared address table improves consistency and
supports future geocoding, address matching, and deduplication.


9. Make owners and transaction parties generic
----------------------------------------------
I added person_or_organizations, property_owners, and transaction_parties.

Reason:
Real estate parties can be individuals, trusts, LLCs, estates, and government
entities. A generic party table handles all of those without separate owner,
buyer, and seller tables that would duplicate names and create matching issues.


10. Keep common property facts typed, but allow flexible features
-----------------------------------------------------------------
I added property_physical_attributes for common fields and property_features for
less standardized source attributes.

Reason:
Fields such as bedrooms, bathrooms, year built, living area, and lot size are
core analysis fields and should be typed columns. Fields such as roof cover,
kitchen style, view, condition, and utility details vary by source, so they fit
better in a typed feature table.


11. Model assessments and taxes separately
------------------------------------------
I separated property_assessments from property_taxes.

Reason:
Assessed value and tax bill data are related but not the same. Boston source
samples include assessed value history, tax amounts, exemptions, and billed
amounts. Keeping these separate makes yearly value trends and tax analysis much
cleaner.


12. Use many-to-many zip code to neighborhood mapping
-----------------------------------------------------
I replaced the single zip_code_to_neighborhood table with postal_codes,
neighborhoods, and postal_code_neighborhoods.

Reason:
One zip code can contain multiple neighborhoods, and one neighborhood can span
multiple zip codes. A many-to-many table avoids incorrect assumptions and works
better for city-level analysis.


13. Add constraints and indexes early
-------------------------------------
The outline includes unique constraints, foreign keys, check constraints, and
indexes for common lookup patterns.

Reason:
The database should help protect data quality. Constraints catch duplicate source
IDs, invalid statuses, impossible years, and broken relationships. Indexes are
included for expected queries such as lookup by parcel ID, property history,
latest assessment, active listings, and spatial search.


14. Plan for scale without overcomplicating day one
---------------------------------------------------
The outline recommends partitioning only after large tables grow substantially.

Reason:
Partitioning raw records, listings, transactions, and assessments can help at
scale, but it adds operational complexity. Starting with good keys and indexes is
better for early development. Partition when row counts and query plans justify
it.


15. Add analytical views later
------------------------------
I listed current_property_summary, latest_property_assessment,
latest_property_sale, and active_listings as recommended views.

Reason:
The normalized schema is good for correctness and history. Views make analysis
easy by presenting current, one-row-per-property summaries without duplicating
data.


Implementation notes
====================

Recommended first build order:
1. Create reference tables: states, counties, postal_codes, sources.
2. Create ingestion tables: ingestion_runs and raw_source_records.
3. Create addresses, properties, and property_source_ids.
4. Normalize Analyze Boston assessment rows into properties, source IDs,
   boston_assessor_details, property_physical_attributes, property_assessments,
   property_taxes, owners, and mailing addresses.
5. Normalize Boston Property Lookup JSON into assessments, taxes, features, and
   latest transaction data.
6. Normalize RentCast into addresses, source IDs, listings, rental estimates,
   and source feature fields.
7. Normalize Mass Land Records into property_transactions and
   transaction_parties.

Important follow-up work:
- Build deterministic address normalization before matching across sources.
- Store source_record_hash during ingestion to prevent duplicate raw records.
- Decide whether condominium units should be separate properties or child
  properties linked to a master parcel.
- Add migration tooling such as Alembic before creating production tables.
- Add dbt or SQL views once normalized loading is stable.
