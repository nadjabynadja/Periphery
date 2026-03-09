from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    embedding_model: str = "all-MiniLM-L6-v2"
    faiss_index_path: str = "data/faiss/index.bin"
    device: str = "cpu"
    crystallizer_interval: int = 300  # seconds
    crystallizer_db_path: str = "./data/periphery_documents.db"
    crystallizer_full_recluster_interval_docs: int = 100
    crystallizer_full_recluster_interval_seconds: int = 3600
    crystallizer_incremental_interval_seconds: int = 60
    crystallizer_min_cluster_size: int = 5
    crystallizer_min_samples: int = 3
    crystallizer_cluster_selection_epsilon: float = 0.0
    crystallizer_trajectory_min_snapshots: int = 5
    crystallizer_auto_label_with_llm: bool = True
    crystallizer_auto_label_budget_hourly_usd: float = 2.0

    # Enrichment pipeline settings
    enrichment_concurrency: int = 4
    enrichment_spacy_model: str = "en_core_web_trf"
    enrichment_llm_model: str = "claude-sonnet-4-20250514"
    enrichment_llm_hourly_cap_usd: float = 5.0
    enrichment_llm_daily_cap_usd: float = 50.0
    enrichment_tier2_min_priority: int = 3
    enrichment_tier3_min_priority: int = 2
    enrichment_llm_timeout_seconds: float = 30.0
    enrichment_llm_max_tokens_per_request: int = 4000
    enrichment_geocoder: str = "nominatim"
    enrichment_geocode_rate_limit: float = 1.0
    enrichment_geocode_cache_db: str = "./data/geocoding_cache.db"
    enrichment_geonames_db: str = "./data/geonames.db"
    enrichment_geospatial_seed_file: str = "./data/geospatial_seeds.json"
    enrichment_fuzzy_match_threshold: float = 0.88

    # Multi-space embedding settings
    embedding_index_dir: str = "./data/indices"
    embedding_chunk_size: int = 256  # tokens per chunk
    embedding_chunk_overlap: int = 64  # overlap tokens between chunks
    embedding_temporal_dim: int = 10
    embedding_geospatial_base_dim: int = 7  # before region vector
    embedding_region_count: int = 12  # number of fixed regions for one-hot
    embedding_rebuild_interval: int = 10000  # rebuild indices every N docs

    # Processing pipeline settings
    pipeline_db_path: str = "./data/periphery_documents.db"
    pipeline_enrichment_batch_size: int = 10
    pipeline_enrichment_poll_interval: float = 10.0
    pipeline_embedding_batch_size: int = 20
    pipeline_embedding_poll_interval: float = 15.0
    pipeline_crystallization_batch_size: int = 50
    pipeline_crystallization_poll_interval: float = 30.0
    pipeline_crystallization_min_batch: int = 1
    pipeline_stale_claim_timeout_seconds: float = 600.0
    pipeline_consumer_restart_delay: float = 5.0
    pipeline_max_retries: int = 3

    # Critic settings
    critic_checkpoint_dir: str = "./data/critic_checkpoints"
    critic_training_dir: str = "./data/critic_training"
    critic_max_checkpoints: int = 5
    critic_retraining_interval_runs: int = 10
    critic_retraining_interval_hours: float = 24.0
    critic_fine_tune_epochs: int = 20
    critic_perturbation_variants: int = 4
    critic_validation_split: float = 0.2
    critic_initial_training_epochs: int = 50
    critic_ensemble_weight_neural: float = 0.4
    critic_ensemble_weight_source_diversity: float = 0.15
    critic_ensemble_weight_temporal: float = 0.15
    critic_ensemble_weight_cross_space: float = 0.15
    critic_ensemble_weight_stability: float = 0.15

    # RSS ingest settings
    rss_enabled: bool = True
    rss_feeds_config: str = ""  # path to feeds.yaml; empty = bundled default
    rss_fetch_full_articles: bool = True
    rss_queue_maxsize: int = 10_000

    # CORS settings
    cors_origins: str = "http://localhost:5173,http://localhost:8000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
