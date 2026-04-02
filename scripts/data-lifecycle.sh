#!/bin/bash
# data-lifecycle.sh — Manage data lifecycle between server and storage box
#
# Phases:
#   1. Prune: Strip raw_html from enriched docs in collection DBs (saves ~90% space)
#   2. Archive: Sync current DBs to storage box
#   3. Vacuum: Reclaim freed space in SQLite files
#   4. Backup: Push timestamped DB snapshots
#
# Usage: data-lifecycle.sh [all|prune|archive|vacuum|backup]

set -euo pipefail

REMOTE="u570575@u570575.your-storagebox.de"
SSH_OPTS="-i /root/.ssh/id_storagebox -p 23"
DATA_DIR="/root/Periphery/data"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

ACTION="${1:-all}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ─── Phase 1: Prune processed data from collection DBs ───
prune() {
    log "=== PRUNE: Stripping raw_html from enriched documents ==="

    for db_file in "$DATA_DIR/rss.db" "$DATA_DIR/gdelt.db" "$DATA_DIR/sanctions.db"; do
        [ -f "$db_file" ] || continue
        db_name=$(basename "$db_file")

        # Count enriched docs with raw_html still present
        count=$(sqlite3 "$db_file" "SELECT COUNT(*) FROM documents WHERE enrichment_status = 'enriched' AND raw_html IS NOT NULL AND LENGTH(raw_html) > 0;" 2>/dev/null || echo 0)

        if [ "$count" -gt 0 ]; then
            # Calculate space to be freed
            size=$(sqlite3 "$db_file" "SELECT COALESCE(SUM(LENGTH(raw_html)), 0) / 1024 / 1024 FROM documents WHERE enrichment_status = 'enriched' AND raw_html IS NOT NULL;" 2>/dev/null || echo 0)
            log "  $db_name: Stripping raw_html from $count enriched docs (~${size}MB)"

            sqlite3 "$db_file" "UPDATE documents SET raw_html = NULL WHERE enrichment_status = 'enriched' AND raw_html IS NOT NULL;"
        else
            log "  $db_name: Nothing to prune"
        fi
    done
}

# ─── Phase 2: Archive to storage box ───
archive() {
    log "=== ARCHIVE: Syncing data to storage box ==="

    RSYNC="rsync -az --progress -e \"ssh $SSH_OPTS\""

    # Sync collection DBs (raw ingest)
    for db_file in rss.db gdelt.db sanctions.db; do
        if [ -f "$DATA_DIR/$db_file" ]; then
            category="rss"
            [ "$db_file" = "gdelt.db" ] && category="rss"
            [ "$db_file" = "sanctions.db" ] && category="sanctions"
            log "  Syncing $db_file..."
            eval $RSYNC "$DATA_DIR/$db_file" "$REMOTE:raw-ingest/$category/$db_file"
        fi
    done

    # Sync analytical.db (processed)
    for f in analytical.db analytical.db-wal analytical.db-shm; do
        [ -f "$DATA_DIR/$f" ] && eval $RSYNC "$DATA_DIR/$f" "$REMOTE:processed/analytical/"
    done

    # Sync indices
    eval $RSYNC "$DATA_DIR/faiss/" "$REMOTE:processed/faiss/"
    eval $RSYNC "$DATA_DIR/indices/" "$REMOTE:processed/indices/"

    # Sync misc processed data
    for f in geocoding_cache.db geotag_embeddings.db; do
        [ -f "$DATA_DIR/$f" ] && eval $RSYNC "$DATA_DIR/$f" "$REMOTE:processed/"
    done

    log "  Archive complete"
}

# ─── Phase 3: Vacuum SQLite files ───
vacuum() {
    log "=== VACUUM: Reclaiming space in SQLite files ==="

    for db_file in "$DATA_DIR"/*.db; do
        [ -f "$db_file" ] || continue
        db_name=$(basename "$db_file")
        size_before=$(du -sh "$db_file" | cut -f1)

        log "  Vacuuming $db_name ($size_before)..."
        sqlite3 "$db_file" "VACUUM;" 2>/dev/null || log "    VACUUM failed for $db_name (may be locked)"

        size_after=$(du -sh "$db_file" | cut -f1)
        log "  $db_name: $size_before → $size_after"
    done
}

# ─── Phase 4: Backup snapshots ───
backup() {
    log "=== BACKUP: Creating timestamped DB snapshots ==="

    SNAP_DIR="/tmp/db-snapshots-$TIMESTAMP"
    mkdir -p "$SNAP_DIR"

    for db_file in "$DATA_DIR"/*.db; do
        [ -f "$db_file" ] || continue
        db_name=$(basename "$db_file")
        log "  Snapshotting $db_name..."
        sqlite3 "$db_file" ".backup '$SNAP_DIR/$db_name'" 2>/dev/null || cp "$db_file" "$SNAP_DIR/$db_name"
    done

    log "  Uploading snapshots..."
    eval rsync -az --progress -e "\"ssh $SSH_OPTS\"" "$SNAP_DIR/" "$REMOTE:backups/db-snapshots/$TIMESTAMP/"
    rm -rf "$SNAP_DIR"

    # Clean up old backups (keep last 14)
    log "  Pruning old backups (keeping last 14)..."
    ssh $SSH_OPTS "$REMOTE" "ls -1d backups/db-snapshots/*/ 2>/dev/null | sort | head -n -14 | while read d; do rm -rf \"\$d\"; done" 2>/dev/null || true

    log "  Backup $TIMESTAMP complete"
}

# ─── Run ───
case "$ACTION" in
    all)    prune; archive; vacuum; backup ;;
    prune)  prune ;;
    archive) archive ;;
    vacuum) vacuum ;;
    backup) backup ;;
    *)
        echo "Usage: $0 [all|prune|archive|vacuum|backup]"
        exit 1
        ;;
esac

log "=== Data lifecycle complete ==="
