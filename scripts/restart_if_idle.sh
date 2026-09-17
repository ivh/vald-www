#!/bin/bash
# Restart vald.service once no job is in flight.
#
# A restart kills the in-process JobQueue and its worker threads, so anything
# running dies with it. vald/startup.py re-runs what it stranded, but only up to
# VALD_STRANDED_RERUN_MAX - past that the user gets a failure mail instead.
# Waiting for idle avoids the question entirely.
set -u

DB=${VALD_DB:-/home/vald/vald-www.git/db.sqlite3}
WAIT=${WAIT:-600}      # seconds to wait for idle before giving up
INTERVAL=${INTERVAL:-5}
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

busy() {
    # The DB row is the authoritative "the queue owns this"; the Fortran
    # processes catch a job whose row was written by another host's mirror or
    # left behind by a crash.
    local n
    n=$(sqlite3 -bail -readonly "$DB" \
        ".timeout 5000" \
        "SELECT COUNT(*) FROM vald_request WHERE status IN ('pending','processing');") || return 2
    [ "$n" -gt 0 ] && { echo "$n request(s) pending/processing"; return 0; }
    if pgrep -u vald -f 'bin/(preselect5|presformat5|select5|hfs_pres|post_hfs_format5)\b' >/dev/null 2>&1; then
        echo "backend binaries still running"
        return 0
    fi
    return 1
}

deadline=$(( $(date +%s) + WAIT ))
while :; do
    reason=$(busy); rc=$?
    [ $rc -eq 2 ] && { echo "cannot read $DB" >&2; exit 2; }
    [ $rc -eq 1 ] && break
    if [ $FORCE -eq 1 ]; then
        echo "busy ($reason) - restarting anyway (--force)"
        break
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "still busy after ${WAIT}s ($reason) - not restarting" >&2
        exit 1
    fi
    echo "waiting: $reason"
    sleep "$INTERVAL"
done

echo "restarting vald.service"
exec ${RESTART_CMD:-sudo systemctl restart vald.service}
