#!/bin/bash
# storagebox-sync.sh — Sync data between Periphery server and Hetzner storage box
# Usage: storagebox-sync.sh [push|pull] [component]
# Components: all, raw, processed, backups

set -euo pipefail

REMOTE="u570575@u570575.your-storagebox.de"
SSH_OPTS="-i /root/.ssh/id_storagebox -p 23"
RSYNC_OPTS="-avz --progress -e \"ssh $SSH_OPTS\""
DATA_DIR="/root/Periphery/data"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

ACTION="${1:-push}"
COMPONENT="${2:-all}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

push_raw() {
    log "Pushing raw ingest data..."
    eval rsync $RSYNC_OPTS "$DATA_DIR/rss.db" "$REMOTE:raw-ingest/rss/"
    eval rsync $RSYNC_OPTS "$DATA_DIR/periphery_documents.db" "$REMOTE:raw-ingest/documents/"
    eval rsync $RSYNC_OPTS "$DATA_DIR/sanctions.db" "$DATA_DIR/sanctions.db-wal" "$DATA_DIR/sanctions.db-shm" "$REMOTE:raw-ingest/sanctions/" 2>/dev/null || true
    eval rsync $RSYNC_OPTS "$DATA_DIR/icij_offshore_full.zip" "$REMOTE:raw-ingest/icij/" 2>/dev/null || true
}

push_processed() {
    log "Pushing processed/crystallized data..."
    eval rsync $RSYNC_OPTS "$DATA_DIR/analytical.db" "$DATA_DIR/analytical.db-wal" "$DATA_DIR/analytical.db-shm" "$REMOTE:processed/analytical/" 2>/dev/null || true
    eval rsync $RSYNC_OPTS "$DATA_DIR/faiss/" "$REMOTE:processed/faiss/"
    eval rsync $RSYNC_OPTS "$DATA_DIR/indices/" "$REMOTE:processed/indices/"
    eval rsync $RSYNC_OPTS "$DATA_DIR/geocoding_cache.db" "$REMOTE:processed/" 2>/dev/null || true
    eval rsync $RSYNC_OPTS "$DATA_DIR/critic_training/" "$REMOTE:processed/critic/training/" 2>/dev/null || true
    eval rsync $RSYNC_OPTS "$DATA_DIR/critic_checkpoints/" "$REMOTE:processed/critic/checkpoints/" 2>/dev/null || true
}

push_backups() {
    log "Creating and pushing DB snapshots..."
    SNAP_DIR="/tmp/db-snapshots-$TIMESTAMP"
    mkdir -p "$SNAP_DIR"
    
    # Use sqlite3 .backup for consistent snapshots
    for db in "$DATA_DIR"/*.db; do
        [ -f "$db" ] || continue
        dbname=$(basename "$db")
        log "  Snapshotting $dbname..."
        sqlite3 "$db" ".backup '$SNAP_DIR/$dbname'" 2>/dev/null || cp "$db" "$SNAP_DIR/$dbname"
    done
    
    eval rsync $RSYNC_OPTS "$SNAP_DIR/" "$REMOTE:backups/db-snapshots/$TIMESTAMP/"
    rm -rf "$SNAP_DIR"
    log "Backup snapshot $TIMESTAMP pushed."
}

pull_raw() {
    log "Pulling raw ingest data from storage box..."
    eval rsync $RSYNC_OPTS "$REMOTE:raw-ingest/rss/rss.db" "$DATA_DIR/"
    eval rsync $RSYNC_OPTS "$REMOTE:raw-ingest/documents/periphery_documents.db" "$DATA_DIR/"
    eval rsync $RSYNC_OPTS "$REMOTE:raw-ingest/sanctions/" "$DATA_DIR/" 2>/dev/null || true
}

pull_processed() {
    log "Pulling processed data from storage box..."
    eval rsync $RSYNC_OPTS "$REMOTE:processed/analytical/" "$DATA_DIR/" 2>/dev/null || true
    eval rsync $RSYNC_OPTS "$REMOTE:processed/faiss/" "$DATA_DIR/faiss/"
    eval rsync $RSYNC_OPTS "$REMOTE:processed/indices/" "$DATA_DIR/indices/"
}

case "$ACTION" in
    push)
        case "$COMPONENT" in
            all) push_raw; push_processed; push_backups ;;
            raw) push_raw ;;
            processed) push_processed ;;
            backups) push_backups ;;
            *) echo "Unknown component: $COMPONENT"; exit 1 ;;
        esac
        ;;
    pull)
        case "$COMPONENT" in
            all) pull_raw; pull_processed ;;
            raw) pull_raw ;;
            processed) pull_processed ;;
            *) echo "Unknown component: $COMPONENT"; exit 1 ;;
        esac
        ;;
    *)
        echo "Usage: $0 [push|pull] [all|raw|processed|backups]"
        exit 1
        ;;
esac

log "Done."
