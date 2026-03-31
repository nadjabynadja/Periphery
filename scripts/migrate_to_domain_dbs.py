#!/usr/bin/env python3
"""Migrate from single periphery_documents.db to domain-specific databases.

Creates four databases:
  - rss.db         ← RSS articles (source_feed starts with http)
  - gdelt.db       ← GDELT documents (source_feed contains 'GDELT')
  - sanctions.db   ← OFAC + ICIJ documents
  - analytical.db  ← All enriched/embedded/crystallized docs + enrichments/embeddings

Usage:
    python scripts/migrate_to_domain_dbs.py --source ./data/periphery_documents.db --data-dir ./data/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def classify_document(source_feed: str, metadata_json: str | None) -> str:
    """Classify a document into a domain database based on source_feed."""
    sf = (source_feed or "").lower()
    meta = {}
    if metadata_json:
        try:
            meta = json.loads(metadata_json)
        except (json.JSONDecodeError, TypeError):
            pass

    source_type = meta.get("source_type", "")

    # Analytical sources (public records, business registrations, property)
    analytical_source_types = (
        "irs_exempt_orgs", "nc_sos_business", "nc_rod",
        "nc_voter", "fec_contributions", "nc_campaign_finance", "nc_parcels",
    )

    if "gdelt" in sf or source_type == "gdelt_doc":
        return "gdelt"
    elif "icij" in sf or "ofac" in sf or source_type in ("icij_offshore", "ofac_sanctions"):
        return "sanctions"
    elif source_type in analytical_source_types:
        return "analytical"
    else:
        return "rss"  # default: RSS articles and anything unclassified


def create_collection_db(db_path: str) -> None:
    """Create a collection database with the minimal schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            source_feed TEXT NOT NULL,
            source_category TEXT NOT NULL DEFAULT '',
            source_credibility_tier INTEGER DEFAULT 3,
            title TEXT,
            url TEXT,
            published TIMESTAMP,
            content TEXT,
            raw_html TEXT,
            summary TEXT,
            content_quality TEXT DEFAULT 'full',
            metadata JSON,
            classification TEXT DEFAULT 'PUBLIC',
            enrichment_status TEXT DEFAULT 'pending',
            enrichment_priority INTEGER DEFAULT 3,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            content_hash TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_enrichment_status ON documents(enrichment_status);
        CREATE INDEX IF NOT EXISTS idx_enrichment_priority ON documents(enrichment_priority);
        CREATE INDEX IF NOT EXISTS idx_content_hash ON documents(content_hash);
        CREATE INDEX IF NOT EXISTS idx_url ON documents(url);
        CREATE INDEX IF NOT EXISTS idx_ingested_at ON documents(ingested_at);
    """)
    conn.commit()
    conn.close()


def migrate(source_path: str, data_dir: str, dry_run: bool = False) -> None:
    """Run the migration."""
    source = Path(source_path)
    out_dir = Path(data_dir)

    if not source.exists():
        print(f"ERROR: Source database not found: {source}")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)

    rss_path = str(out_dir / "rss.db")
    gdelt_path = str(out_dir / "gdelt.db")
    sanctions_path = str(out_dir / "sanctions.db")
    analytical_path = str(out_dir / "analytical.db")

    print(f"Source: {source}")
    print(f"Output: rss={rss_path} gdelt={gdelt_path} sanctions={sanctions_path} analytical={analytical_path}")

    # Open source
    src = sqlite3.connect(str(source))
    src.row_factory = sqlite3.Row

    # Count documents
    total = src.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    print(f"Total documents in source: {total}")

    if dry_run:
        # Just classify and count
        counts = {"rss": 0, "gdelt": 0, "sanctions": 0}
        cursor = src.execute("SELECT source_feed, metadata FROM documents")
        for row in cursor:
            domain = classify_document(row["source_feed"], row["metadata"])
            counts[domain] += 1

        enriched = src.execute(
            "SELECT COUNT(*) FROM documents WHERE processing_status IN ('enriched','embedded','crystallized')"
        ).fetchone()[0]

        print(f"\nDry run classification:")
        for domain, count in counts.items():
            print(f"  {domain}: {count}")
        print(f"  analytical (enriched+): {enriched}")
        src.close()
        return

    # Create collection databases
    print("\nCreating collection databases...")
    for path in [rss_path, gdelt_path, sanctions_path]:
        create_collection_db(path)

    # Create analytical database using the full schema
    print("Creating analytical database...")
    # Use asyncio to call ensure_database
    async def _create_analytical():
        from periphery.db import ensure_database, close_pool
        await ensure_database(analytical_path)
        await close_pool()

    asyncio.run(_create_analytical())

    # Open destination databases
    rss_db = sqlite3.connect(rss_path)
    gdelt_db = sqlite3.connect(gdelt_path)
    sanctions_db = sqlite3.connect(sanctions_path)
    analytical_db = sqlite3.connect(analytical_path)

    db_map = {
        "rss": rss_db,
        "gdelt": gdelt_db,
        "sanctions": sanctions_db,
    }

    # Migrate documents to collection databases
    print("\nMigrating documents to collection databases...")
    counts = {"rss": 0, "gdelt": 0, "sanctions": 0, "analytical": 0}
    batch_size = 1000
    offset = 0

    while True:
        cursor = src.execute(
            """
            SELECT id, source_feed, source_category, source_credibility_tier,
                   title, url, published, ingested, content, raw_html, summary,
                   content_quality, metadata, processing_status, priority,
                   data_classification
            FROM documents
            ORDER BY ingested ASC
            LIMIT ? OFFSET ?
            """,
            (batch_size, offset),
        )
        rows = cursor.fetchall()
        if not rows:
            break

        for row in rows:
            domain = classify_document(row["source_feed"], row["metadata"])
            dest_db = db_map[domain]

            # Map old schema to collection schema
            processing_status = row["processing_status"] or "pending"
            enrichment_status = "enriched" if processing_status in ("enriched", "embedded", "crystallized") else "pending"

            try:
                dest_db.execute(
                    """
                    INSERT OR IGNORE INTO documents
                        (id, source_feed, source_category, source_credibility_tier,
                         title, url, published, content, raw_html, summary,
                         content_quality, metadata, classification,
                         enrichment_status, enrichment_priority, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["source_feed"],
                        row["source_category"] or "",
                        row["source_credibility_tier"],
                        row["title"],
                        row["url"],
                        row["published"],
                        row["content"],
                        row["raw_html"],
                        row["summary"],
                        row["content_quality"],
                        row["metadata"],
                        row["data_classification"] or "PUBLIC",
                        enrichment_status,
                        row["priority"] or 3,
                        row["ingested"],
                    ),
                )
                counts[domain] += 1
            except Exception as e:
                print(f"  WARN: Failed to insert {row['id']} into {domain}: {e}")

        offset += batch_size
        if offset % 10000 == 0:
            print(f"  Processed {offset}/{total} documents...")
            for db in db_map.values():
                db.commit()

    for db in db_map.values():
        db.commit()

    print(f"\nCollection DB counts: rss={counts['rss']} gdelt={counts['gdelt']} sanctions={counts['sanctions']}")

    # Migrate enriched documents to analytical.db
    print("\nMigrating enriched documents to analytical.db...")
    enriched_cursor = src.execute(
        """
        SELECT * FROM documents
        WHERE processing_status IN ('enriched', 'embedded', 'crystallized')
        """
    )

    # Get column names from source
    src_cols = [desc[0] for desc in enriched_cursor.description]

    for row in enriched_cursor:
        row_dict = dict(zip(src_cols, row))
        try:
            analytical_db.execute(
                """
                INSERT OR IGNORE INTO documents
                    (id, source_feed, source_category, source_credibility_tier,
                     title, url, published, ingested, content, raw_html, summary,
                     content_quality, metadata, processing_status, processing_error,
                     enrichment_started_at, enrichment_completed_at,
                     embedding_started_at, embedding_completed_at,
                     crystallization_started_at, crystallization_completed_at,
                     retry_count, max_retries, priority, data_classification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_dict.get("id"),
                    row_dict.get("source_feed"),
                    row_dict.get("source_category"),
                    row_dict.get("source_credibility_tier"),
                    row_dict.get("title"),
                    row_dict.get("url"),
                    row_dict.get("published"),
                    row_dict.get("ingested"),
                    row_dict.get("content"),
                    row_dict.get("raw_html"),
                    row_dict.get("summary"),
                    row_dict.get("content_quality"),
                    row_dict.get("metadata"),
                    row_dict.get("processing_status"),
                    row_dict.get("processing_error"),
                    row_dict.get("enrichment_started_at"),
                    row_dict.get("enrichment_completed_at"),
                    row_dict.get("embedding_started_at"),
                    row_dict.get("embedding_completed_at"),
                    row_dict.get("crystallization_started_at"),
                    row_dict.get("crystallization_completed_at"),
                    row_dict.get("retry_count", 0),
                    row_dict.get("max_retries", 3),
                    row_dict.get("priority", 3),
                    row_dict.get("data_classification", "PUBLIC"),
                ),
            )
            counts["analytical"] += 1
        except Exception as e:
            print(f"  WARN: Failed to insert {row_dict.get('id')} into analytical: {e}")

    analytical_db.commit()
    print(f"  Migrated {counts['analytical']} enriched documents to analytical.db")

    # Migrate enrichments
    print("\nMigrating document_enrichments...")
    try:
        enrich_cursor = src.execute("SELECT * FROM document_enrichments")
        enrich_cols = [desc[0] for desc in enrich_cursor.description]
        enrich_count = 0
        for row in enrich_cursor:
            row_dict = dict(zip(enrich_cols, row))
            try:
                analytical_db.execute(
                    """
                    INSERT OR IGNORE INTO document_enrichments
                        (document_id, entities, relationships, temporal_context,
                         geospatial_data, cross_references, enrichment_metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_dict.get("document_id"),
                        row_dict.get("entities"),
                        row_dict.get("relationships"),
                        row_dict.get("temporal_context"),
                        row_dict.get("geospatial_data"),
                        row_dict.get("cross_references"),
                        row_dict.get("enrichment_metadata"),
                        row_dict.get("created_at"),
                    ),
                )
                enrich_count += 1
            except Exception as e:
                pass
        analytical_db.commit()
        print(f"  Migrated {enrich_count} enrichment records")
    except Exception as e:
        print(f"  WARN: Could not migrate enrichments: {e}")

    # Migrate embeddings
    print("Migrating document_embeddings...")
    try:
        embed_cursor = src.execute("SELECT * FROM document_embeddings")
        embed_cols = [desc[0] for desc in embed_cursor.description]
        embed_count = 0
        for row in embed_cursor:
            row_dict = dict(zip(embed_cols, row))
            try:
                analytical_db.execute(
                    """
                    INSERT OR IGNORE INTO document_embeddings
                        (document_id, semantic_embedding, semantic_chunks,
                         entity_embedding, relational_embedding,
                         temporal_vector, geospatial_vector,
                         embedding_model, embedding_dimensions,
                         completeness, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_dict.get("document_id"),
                        row_dict.get("semantic_embedding"),
                        row_dict.get("semantic_chunks"),
                        row_dict.get("entity_embedding"),
                        row_dict.get("relational_embedding"),
                        row_dict.get("temporal_vector"),
                        row_dict.get("geospatial_vector"),
                        row_dict.get("embedding_model"),
                        row_dict.get("embedding_dimensions"),
                        row_dict.get("completeness"),
                        row_dict.get("created_at"),
                        row_dict.get("updated_at"),
                    ),
                )
                embed_count += 1
            except Exception as e:
                pass
        analytical_db.commit()
        print(f"  Migrated {embed_count} embedding records")
    except Exception as e:
        print(f"  WARN: Could not migrate embeddings: {e}")

    # Migrate other analytical tables
    analytical_tables = [
        "crystallizer_snapshots", "clusters", "cluster_snapshots",
        "trajectories", "anomalies", "relational_gradients",
        "critic_runs", "critic_scores", "critic_confidence_history",
        "query_history", "query_sessions", "query_bookmarks", "analyst_annotations",
        "canonical_entities", "entity_aliases",
        "spatial_observations",
        "organizations", "users", "auth_sessions", "auth_challenges", "approved_emails",
        "user_entity_annotations", "user_entity_groups", "user_saved_views",
    ]

    for table_name in analytical_tables:
        try:
            cursor = src.execute(f"SELECT * FROM {table_name}")
            cols = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            if rows:
                placeholders = ",".join("?" for _ in cols)
                col_list = ",".join(cols)
                count = 0
                for row in rows:
                    try:
                        analytical_db.execute(
                            f"INSERT OR IGNORE INTO {table_name} ({col_list}) VALUES ({placeholders})",
                            row,
                        )
                        count += 1
                    except Exception:
                        pass
                analytical_db.commit()
                if count > 0:
                    print(f"  Migrated {count} rows from {table_name}")
        except Exception:
            pass  # Table might not exist in source

    # Close all
    src.close()
    rss_db.close()
    gdelt_db.close()
    sanctions_db.close()
    analytical_db.close()

    print("\n✅ Migration complete!")
    print(f"   rss.db: {counts['rss']} documents")
    print(f"   gdelt.db: {counts['gdelt']} documents")
    print(f"   sanctions.db: {counts['sanctions']} documents")
    print(f"   analytical.db: {counts['analytical']} enriched documents + all analytical tables")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate from single DB to domain-specific databases"
    )
    parser.add_argument(
        "--source",
        default="./data/periphery_documents.db",
        help="Path to source database",
    )
    parser.add_argument(
        "--data-dir",
        default="./data/",
        help="Output directory for new databases",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Just classify documents without migrating",
    )
    args = parser.parse_args()
    migrate(args.source, args.data_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
