#!/usr/bin/env bash
set -e

if [ -z "$1" ]; then
  echo "Usage: ./scripts/restore.sh backups/<backup_file>.dump"
  exit 1
fi

BACKUP_FILE="$1"
BASENAME=$(basename "$BACKUP_FILE")

docker exec real_estate_postgres_dev dropdb \
  -U real_estate_user \
  --if-exists real_estate_dev

docker exec real_estate_postgres_dev createdb \
  -U real_estate_user \
  real_estate_dev

docker exec real_estate_postgres_dev pg_restore \
  -U real_estate_user \
  -d real_estate_dev \
  /backups/"$BASENAME"

echo "Database restored from: $BACKUP_FILE"
