#!/bin/bash
# OWL Message Bus Poller v1.0
# Pulls echo-v1-brain repo and checks for new messages from Bull
# Runs every minute via cron

SHARED="/home/lol/Desktop/openclaw/shared"
INBOX="$SHARED/inbox/owl-from-bull"
POLL_LOG="$SHARED/data/message_bus_poll.log"
OUTDIR="$SHARED/outbox/owl-to-bull"
LOCKFILE="/tmp/owl_msgbus.lock"

# Prevent overlapping runs
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE" 2>/dev/null)
    if kill -0 "$PID" 2>/dev/null; then
        echo "$(date +%Y-%m-%dT%H:%M:%S) [POLLED] Previous run still active, skipping" >> "$POLL_LOG"
        exit 0
    fi
fi
echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

cd "$SHARED/echo-v1-brain" 2>/dev/null || {
    echo "$(date +%Y-%m-%dT%H:%M:%S) [ERROR] echo-v1-brain repo not found at $SHARED/echo-v1-brain" >> "$POLL_LOG"
    exit 1
}

# Pull latest from GitHub
git pull --rebase origin main >> "$POLL_LOG" 2>&1

# Check for new inbox messages
NEW_MSGS=$(ls "$INBOX"/*.md 2>/dev/null | wc -l)

if [ "$NEW_MSGS" -gt 0 ]; then
    echo "$(date +%Y-%m-%dT%H:%M:%S) [MSG] $NEW_MSGS new message(s) from Bull" >> "$POLL_LOG"
    # Copy messages to local processed folder
    mkdir -p "$SHARED/inbox/processed"
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    cp "$INBOX"/*.md "$SHARED/inbox/processed/${TIMESTAMP}_" 2>/dev/null
    # Log message names
    for f in "$INBOX"/*.do; do
        echo "$(date +%Y-%m-%dT%H:%M:%S) [CONTENTS] $f" >> "$(cat "$POLL_LOG")" 2>/dev/null
    done
else
    echo "$(date +%Y-%m-%dT%H:%M:%S) [IDLE] No new messages" >> "$POLL_LOG"
fi

exit 0
