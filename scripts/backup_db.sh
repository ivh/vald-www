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

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DB="${VALD_DB:-$REPO_DIR/db.sqlite3}"
BACKUP_DIR="${VALD_BACKUP_DIR:-$REPO_DIR/backups}"
RETENTION_DAYS="${VALD_BACKUP_RETENTION_DAYS:-90}"
# Never prune below this many snapshots, however old they are. Without it, an
# outage longer than the retention window leaves you with nothing at all.
KEEP_MIN="${VALD_BACKUP_KEEP_MIN:-14}"

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

# Prune: drop snapshots older than the retention window, but only those outside
# the newest KEEP_MIN.
pruned=0
protected="$(ls -1 "$BACKUP_DIR"/vald-db-*.sqlite3 2>/dev/null | sort -r | head -n "$KEEP_MIN")"
while IFS= read -r old; do
    [ -n "$old" ] || continue
    if ! grep -qxF "$old" <<<"$protected"; then
        rm -f "$old" && pruned=$((pruned + 1))
    fi
done < <(find "$BACKUP_DIR" -maxdepth 1 -name 'vald-db-*.sqlite3' -mtime +"$RETENTION_DAYS")

remaining="$(find "$BACKUP_DIR" -maxdepth 1 -name 'vald-db-*.sqlite3' | wc -l | tr -d ' ')"
echo "backup_db: pruned $pruned, $remaining snapshot(s) retained"
