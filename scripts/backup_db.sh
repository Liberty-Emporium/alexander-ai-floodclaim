#!/bin/bash
# FloodClaim Pro — SQLite backup script (called by cron)
# Backs up database and uploads directory

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

# Find the DB
DB_PATH=""
for p in "/data/floodclaim.db" "$APP_DIR/data/floodclaim.db" "/tmp/floodclaim.db"; do
    [ -f "$p" ] && DB_PATH="$p" && break
done

if [ -z "$DB_PATH" ]; then
    echo "[$(date)] ❌ DB not found"
    exit 1
fi

BACKUP_DIR="/data/backups"
mkdir -p "$BACKUP_DIR"

TS=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/floodclaim_${TS}.db"

# SQLite backup (safe while running)
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

if [ -f "$BACKUP_FILE" ]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "[$(date)] ✅ Backup saved: floodclaim_${TS}.db ($SIZE)"
else
    echo "[$(date)] ❌ Backup failed"
    exit 1
fi

# Also tar the uploads directory
UPLOAD_DIR="$(dirname "$DB_PATH")/uploads"
if [ -d "$UPLOAD_DIR" ]; then
    TAR_FILE="$BACKUP_DIR/uploads_${TS}.tar.gz"
    tar czf "$TAR_FILE" -C "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")" 2>/dev/null
    UTIL_SIZE=$(du -h "$TAR_FILE" 2>/dev/null | cut -f1)
    echo "[$(date)] ✅ Uploads archived: uploads_${TS}.db ($UTIL_SIZE)"
fi

# Keep only last 7 backups
ls -t "$BACKUP_DIR"/floodclaim_*.db 2>/dev/null | tail -n +8 | xargs rm -f
ls -t "$BACKUP_DIR"/uploads_*.tar.gz 2>/dev/null | tail -n +8 | xargs rm -f

echo "[$(date)] ✅ Backup complete"
