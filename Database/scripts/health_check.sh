#!/usr/bin/env bash
set -e

CONTAINER_NAME="real_estate_postgres_dev"
DB_USER="real_estate_user"
DB_NAME="real_estate_dev"

echo "=== Docker container status ==="
docker ps --filter "name=${CONTAINER_NAME}"

echo
echo "=== Postgres readiness ==="
docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME"

echo
echo "=== Database size ==="
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
"

echo
echo "=== Table count ==="
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "
SELECT COUNT(*) AS table_count
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE';
"

echo
echo "=== Largest tables ==="
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "
SELECT
  schemaname,
  relname AS table_name,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 10;
"

echo
echo "=== Active connections ==="
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "
SELECT
  state,
  COUNT(*) AS connection_count
FROM pg_stat_activity
GROUP BY state
ORDER BY connection_count DESC;
"

echo
echo "=== Server disk usage ==="
df -h

echo
echo "=== Server memory ==="
free -h

echo
echo "Health check complete."
