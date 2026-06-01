#!/usr/bin/env python3
"""
FloodClaim Pro — Automated SQLite Database Backup
Backs up floodclaim.db to /data/backups/ with timestamp.
Keeps last 7 daily backups. Run via cron.
"""
import os, shutil, sqlite3, datetime, glob

DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') or os.environ.get('RAILWAY_DATA_DIR') or os.environ.get('DATA_DIR') or '/data'
DB_PATH = os.path.join(DATA_DIR, 'floodclaim.db')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
KEEP_DAYS = 7

def run_backup():
    if not os.path.exists(DB_PATH):
        print(f"❌ DB not found at {DB_PATH}")
        return False

    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Create timestamped backup using SQLite's safe backup API
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f'floodclaim_{ts}.db'
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    try:
        # Use sqlite3 backup API (safe even if DB is in use)
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(backup_path)
        src.backup(dst)
        dst.close()
        src.close()

        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        print(f"✅ Backup saved: {backup_name} ({size_mb:.1f} MB)")
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        return False

    # Clean up old backups (keep last N days)
    try:
        pattern = os.path.join(BACKUP_DIR, 'floodclaim_*.db')
        backups = sorted(glob.glob(pattern))
        # Keep last KEEP_DAYS backups
        if len(backups) > KEEP_DAYS:
            for old in backups[:-KEEP_DAYS]:
                os.remove(old)
                print(f"🗑️  Removed old backup: {os.path.basename(old)}")
    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")

    return True

def verify_backup(backup_path):
    """Verify backup is a valid SQLite DB."""
    try:
        conn = sqlite3.connect(backup_path)
        conn.execute('SELECT COUNT(*) FROM sqlite_master')
        conn.close()
        return True
    except Exception:
        return False

if __name__ == '__main__':
    print(f"🔄 FloodClaim Pro DB Backup — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   Source: {DB_PATH}")
    print(f"   Dest:   {BACKUP_DIR}")
    success = run_backup()
    exit(0 if success else 1)
