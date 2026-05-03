#!/usr/bin/env bash
set -e

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_FILE="backups/real_estate_dev_${TIMESTAMP}.dump"

docker exec real_estate_postgres_dev pg_dump \
  -U real_estate_user \
  -d real_estate_dev \
  -Fc \
  -f /backups/$(basename "$BACKUP_FILE")

echo "Backup created: $BACKUP_FILE"