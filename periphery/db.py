from contextlib import asynccontextmanager
import aiosqlite
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

@asynccontextmanager
async def get_connection(db_path: str | Path):
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=30000")  # 30 seconds
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()

async def ensure_database(db_path: str | Path) -> None:
    """Create the database and all tables if they don't exist.

    Consolidates schema from all components so the DB is fully initialized
    before any component tries to query it.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    async with get_connection(path) as db:
        # -- Documents (from rss_ingest/document_store.py) --
        await db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                source_feed TEXT NOT NULL,
                source_category TEXT,
                source_credibility_tier INTEGER,
                title TEXT,
                url TEXT,
                published TIMESTAMP,
                ingested TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                content TEXT,
                raw_html TEXT,
                summary TEXT,
                content_quality TEXT DEFAULT 'full',
                metadata JSON,
                processing_status TEXT DEFAULT 'pending',
                processing_error TEXT,
                enrichment_started_at TIMESTAMP,
                enrichment_completed_at TIMESTAMP,
                embedding_started_at TIMESTAMP,
                embedding_completed_at TIMESTAMP,
                crystallization_started_at TIMESTAMP,
                crystallization_completed_at TIMESTAMP,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ingested ON documents(ingested)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_source_feed ON documents(source_feed)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_processing_status ON documents(processing_status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_url ON documents(url)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_content_quality ON documents(content_quality)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS document_enrichments (
                document_id TEXT PRIMARY KEY REFERENCES documents(id),
                entities JSON,
                relationships JSON,
                temporal_context JSON,
                geospatial_data JSON,
                cross_references JSON,
                enrichment_metadata JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS document_embeddings (
                document_id TEXT PRIMARY KEY REFERENCES documents(id),
                semantic_embedding BLOB,
                semantic_chunks JSON,
                entity_embedding BLOB,
                relational_embedding BLOB,
                temporal_vector JSON,
                geospatial_vector JSON,
                embedding_model TEXT,
                embedding_dimensions INTEGER,
                completeness JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
            )
        """)

        # -- Crystallizer (from crystallizer/persistence.py) --
        await db.execute("""
            CREATE TABLE IF NOT EXISTS crystallizer_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                generated_at TIMESTAMP,
                snapshot_data JSON,
                corpus_size INTEGER,
                num_clusters INTEGER,
                num_anomalies INTEGER,
                processing_time_ms INTEGER
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_time ON crystallizer_snapshots(generated_at)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS clusters (
                cluster_id TEXT PRIMARY KEY,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                status TEXT,
                current_size INTEGER,
                cross_space_coherence FLOAT,
                label TEXT,
                key_entities JSON,
                metadata JSON
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS cluster_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id TEXT REFERENCES clusters(cluster_id),
                timestamp TIMESTAMP,
                size INTEGER,
                centroid JSON,
                coherence FLOAT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cluster_snap_time ON cluster_snapshots(cluster_id, timestamp)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS trajectories (
                trajectory_id TEXT PRIMARY KEY,
                cluster_id TEXT REFERENCES clusters(cluster_id),
                space TEXT,
                pattern TEXT,
                velocity FLOAT,
                confidence FLOAT,
                first_detected TIMESTAMP,
                last_updated TIMESTAMP,
                snapshots JSON
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS anomalies (
                anomaly_id TEXT PRIMARY KEY,
                document_id TEXT,
                anomaly_type TEXT,
                anomaly_score FLOAT,
                outlier_spaces JSON,
                source_credibility INTEGER,
                first_detected TIMESTAMP,
                resolved BOOLEAN DEFAULT FALSE,
                resolved_into_cluster TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_unresolved ON anomalies(resolved, anomaly_score DESC)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS relational_gradients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_cluster TEXT REFERENCES clusters(cluster_id),
                target_cluster TEXT REFERENCES clusters(cluster_id),
                gradient_score FLOAT,
                components JSON,
                first_detected TIMESTAMP,
                trend TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gradient_score ON relational_gradients(gradient_score DESC)")

        # -- Critic (from critic/persistence.py) --
        await db.execute("""
            CREATE TABLE IF NOT EXISTS critic_runs (
                run_id TEXT PRIMARY KEY,
                timestamp TIMESTAMP,
                model_version INTEGER,
                snapshot_id TEXT,
                structures_scored INTEGER,
                mean_confidence FLOAT,
                median_confidence FLOAT,
                low_confidence_count INTEGER,
                high_confidence_count INTEGER,
                scoring_time_ms INTEGER
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_critic_run_time ON critic_runs(timestamp)")

        # -- Query (from query/persistence.py) --
        await db.execute("""
            CREATE TABLE IF NOT EXISTS query_history (
                query_id TEXT PRIMARY KEY,
                query_text TEXT,
                parsed_intent JSON,
                execution_plan JSON,
                result_summary JSON,
                execution_stats JSON,
                analyst_feedback JSON,
                session_id TEXT,
                timestamp TIMESTAMP,
                response_time_ms INTEGER
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_query_time ON query_history(timestamp)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_query_session ON query_history(session_id)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS query_sessions (
                session_id TEXT PRIMARY KEY,
                state JSON,
                created_at TIMESTAMP,
                last_active TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS query_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id TEXT REFERENCES query_history(query_id),
                session_id TEXT,
                label TEXT,
                created_at TIMESTAMP,
                active BOOLEAN DEFAULT TRUE
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bookmark_session ON query_bookmarks(session_id)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS analyst_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                annotation_type TEXT,
                target_type TEXT,
                target_id TEXT,
                annotation_data JSON,
                session_id TEXT,
                created_at TIMESTAMP
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_annotation_target ON analyst_annotations(target_type, target_id)")

        await db.commit()
    logger.info("database_initialized path=%s", db_path)


async def get_persistent_connection(db_path: str | Path) -> aiosqlite.Connection:
    """For connections that stay open for the lifetime of a component."""
    db = await aiosqlite.connect(str(db_path))
    logger.info("db_connection_opened", journal_mode="WAL", busy_timeout=5000)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=30000")  # 30 seconds
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    return db