/*
Jay Martinez -- 5/28/2026

These queries are ment to validate the inital Analyze Boston CSV
ingestion.

The queries will provide insight to what data is missing/ misformatted.

The base line/ the source of truth that I am building the database from is
the boston_pid. The boston_pid should be unique for each property and only
one property should have the same pid.

I also want to validate that properties without unit numbers or street numbers
are correctly entered.
*/

-- Gets the PID for a given address line 1
-- This is useful because we consider PID to be bottom line t

SELECT 
  a.formatted_address,
  b.boston_pid
FROM addresses a
JOIN properties p
  ON p.canonical_address_id = a.address_id
JOIN boston_assessor_details b
  ON b.property_id = p.property_id
WHERE a.address_line1 = '258 Lexington ST'; -- < address line 1 (can be changed to search)


-- Gets all the properties with multiple pids accociated with it

SELECT 
  b.boston_pid,
  COUNT(*) AS duplicate_count
FROM boston_assessor_details b
GROUP BY b.boston_pid
HAVING COUNT(*) >= 2;

-- Get properties with missing unit numbers
