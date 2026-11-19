#!/bin/bash
#
# Snapshot the VALD sqlite database.
#
# Deliberately plain bash with no Django import: this has to keep working when
# the app itself does not (bad migration, missing secrets.txt, broken venv),
# which is exactly when you most want yesterday's snapshot to exist.
#
# The database runs in WAL mode, so copying db.sqlite3 with cp/rsync yields a
# file that silently omits everything still in the -wal. sqlite3 .backup uses
# the online backup API and produces a consistent snapshot of a live database.
#
# Snapshots are named with the date and the code revision that wrote them,
# because a dump is only restorable against a codebase at compatible migration
# state.
#
# On the server, start vald-backup.service rather than running this directly:
# the unit sets VALD_BACKUP_DIR to where the nightly snapshots go, while bare
# invocation defaults to backups/ inside the checkout - which is how the one
# snapshot you care about ends up somewhere you will not look for it.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DB="${VALD_DB:-$REPO_DIR/db.sqlite3}"
BACKUP_DIR="${VALD_BACKUP_DIR:-$REPO_DIR/backups}"
# Thinning: every snapshot younger than KEEP_DAILY days, then the newest one per
# ISO week up to KEEP_WEEKLY weeks back, then the newest one per month up to
# KEEP_MONTHLY months back. Anything older is deleted.
KEEP_DAILY="${VALD_BACKUP_KEEP_DAILY:-14}"
KEEP_WEEKLY="${VALD_BACKUP_KEEP_WEEKLY:-12}"
KEEP_MONTHLY="${VALD_BACKUP_KEEP_MONTHLY:-24}"
# Never prune below this many snapshots, however old they are. Without it, an
# outage longer than the whole schedule leaves you with nothing at all.
KEEP_MIN="${VALD_BACKUP_KEEP_MIN:-7}"

die() { echo "backup_db: $*" >&2; exit 1; }

[ -f "$DB" ] || die "database not found: $DB"

revision="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo nogit)"
if ! git -C "$REPO_DIR" diff --quiet HEAD 2>/dev/null; then
    revision="${revision}-dirty"
fi

stamp="$(date +%Y-%m-%dT%H%M%S)"
name="vald-db-${stamp}-${revision}.sqlite3"

mkdir -p "$BACKUP_DIR"
tmp="$BACKUP_DIR/.${name}.partial"
trap 'rm -f "$tmp"' EXIT

# .timeout covers the case where a job thread is mid-write; without it .backup
# gives up immediately with "database is locked".
sqlite3 "$DB" ".timeout 30000" ".backup '$tmp'" \
    || die "sqlite3 .backup failed for $DB"

# Verifying the copy, not the source: catches a snapshot truncated by a full
# disk, which is otherwise only discovered on the day you need to restore.
check="$(sqlite3 "$tmp" 'PRAGMA integrity_check;' 2>&1)" \
    || die "integrity check could not run on $tmp"
[ "$check" = "ok" ] || die "integrity check failed on new snapshot: $check"

mv "$tmp" "$BACKUP_DIR/$name"
trap - EXIT
ln -sfn "$name" "$BACKUP_DIR/latest.sqlite3"

size="$(du -h "$BACKUP_DIR/$name" | cut -f1)"
echo "backup_db: wrote $BACKUP_DIR/$name ($size)"

# Prune. Ages come from the timestamp in the filename, not mtime, so a copied
# or restored backup dir keeps its schedule. Walking newest-first means each
# week/month is represented by its latest snapshot.
now="$(date +%s)"
pruned=0
n=0
seen_weeks=" "
seen_months=" "
while IFS= read -r path; do
    [ -n "$path" ] || continue
    n=$((n + 1))
    f="${path##*/}"
    day="${f:8:10}"   # vald-db-YYYY-MM-DDTHHMMSS-<rev>.sqlite3
    if ! ts="$(date -d "$day" +%s 2>/dev/null)"; then
        echo "backup_db: cannot parse date in $f, leaving it alone" >&2
        continue
    fi
    age=$(( (now - ts) / 86400 ))
    week="$(date -d "$day" +%G-W%V)"
    month="${day:0:7}"

    keep=0
    if [ "$n" -le "$KEEP_MIN" ] || [ "$age" -lt "$KEEP_DAILY" ]; then
        keep=1
    elif [ "$age" -lt $((KEEP_WEEKLY * 7)) ]; then
        [[ "$seen_weeks" == *" $week "* ]] || keep=1
    elif [ "$age" -lt $((KEEP_MONTHLY * 31)) ]; then
        [[ "$seen_months" == *" $month "* ]] || keep=1
    fi
    # Daily-tier snapshots count as their week's/month's representative too,
    # otherwise each day ageing out of a tier would survive as one more extra.
    seen_weeks+="$week "
    seen_months+="$month "

    if [ "$keep" -eq 0 ]; then
        rm -f "$path" && pruned=$((pruned + 1))
    fi
done < <(find "$BACKUP_DIR" -maxdepth 1 -name 'vald-db-*.sqlite3' | sort -r)

remaining="$(find "$BACKUP_DIR" -maxdepth 1 -name 'vald-db-*.sqlite3' | wc -l | tr -d ' ')"
echo "backup_db: pruned $pruned, $remaining snapshot(s) retained"
