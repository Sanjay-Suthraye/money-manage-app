#!/bin/bash
# Daily SQLite backup -> S3 (runs on the Lightsail server from cron).
# sqlite3 .backup takes a consistent snapshot even mid-write (WAL-safe).
set -euo pipefail

DB=/home/ubuntu/money_manager/data/money.db
DIR=/home/ubuntu/backups
BUCKET=s3://money-backups-707875361098
TS=$(date +%F)

mkdir -p "$DIR"
sqlite3 "$DB" ".backup '$DIR/money-$TS.db'"
gzip -f "$DIR/money-$TS.db"
aws s3 cp "$DIR/money-$TS.db.gz" "$BUCKET/daily/money-$TS.db.gz" --only-show-errors
find "$DIR" -name 'money-*.db.gz' -mtime +14 -delete  # keep 2 weeks locally
