from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    exa_api_key: str = ""
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
    enrichment_spacy_model: str = "en_core_web_sm"
    enrichment_llm_model: str = "claude-sonnet-4-20250514"
    enrichment_llm_hourly_cap_usd: float = 5.0
    enrichment_llm_daily_cap_usd: float = 50.0
    enrichment_tier2_min_priority: int = 3
    enrichment_tier3_min_priority: int = 2
    enrichment_llm_timeout_seconds: float = 30.0
    enrichment_llm_max_tokens_per_request: int = 4000
    enrichment_geocode_cache_db: str = "./data/geocoding_cache.db"
    enrichment_geonames_db: str = "./data/geonames.db"
    enrichment_geospatial_seed_file: str = "./data/geospatial_seeds.json"
    enrichment_fuzzy_match_threshold: float = 0.88
    enrichment_photon_base_url: str = "http://localhost:2322"
    enrichment_llm_model_path: str = "models/llama-3.2-3b-instruct-q4_k_m.gguf"
    enrichment_llm_disambiguator_enabled: bool = True

    # LLM verification stage settings
    verification_enabled: bool = True
    verification_model: str = "claude-haiku-3-5-20241022"
    verification_exa_enabled: bool = True
    verification_exa_min_source_count: int = 3
    verification_batch_size: int = 50

    # Multi-space embedding settings
    embedding_index_dir: str = "./data/indices"
    embedding_chunk_size: int = 256  # tokens per chunk
    embedding_chunk_overlap: int = 64  # overlap tokens between chunks
    embedding_temporal_dim: int = 10
    embedding_geospatial_base_dim: int = 7  # before region vector
    embedding_region_count: int = 12  # number of fixed regions for one-hot
    embedding_rebuild_interval: int = 10000  # rebuild indices every N docs

    # Geotag embeddings database
    geotag_db_path: str = "./data/geotag_embeddings.db"

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
    critic_drift_mean_threshold: float = 0.15
    critic_drift_low_confidence_ratio: float = 0.5
    critic_drift_window_size: int = 5

    # RSS ingest settings
    rss_enabled: bool = True
    rss_feeds_config: str = ""  # path to feeds.yaml; empty = bundled default
    rss_fetch_full_articles: bool = True
    rss_queue_maxsize: int = 10_000

    # External data source settings
    sources_enabled: bool = True
    sources_config: str = ""  # path to sources.yaml; empty = use env vars

    # OpenSky Network
    opensky_enabled: bool = False
    opensky_poll_interval: int = 15
    opensky_username: str = ""
    opensky_password: str = ""
    opensky_bbox: str = ""  # "lamin,lomin,lamax,lomax" or empty for global

    # ADS-B Exchange (via Position-API)
    adsb_enabled: bool = False
    adsb_poll_interval: int = 30
    adsb_position_api_url: str = "http://localhost:3000"
    adsb_icao_watchlist: str = ""  # comma-separated ICAO hex codes

    # Maritime (via Position-API)
    maritime_enabled: bool = False
    maritime_poll_interval: int = 60
    maritime_position_api_url: str = "http://localhost:3000"
    maritime_mmsi_watchlist: str = ""  # comma-separated MMSI numbers
    maritime_watch_areas: str = ""  # comma-separated area codes (WMED,EMED,etc)

    # CelesTrak TLE
    celestrak_enabled: bool = False
    celestrak_poll_interval: int = 3600
    celestrak_groups: str = "stations,active"  # comma-separated satellite groups
    celestrak_norad_ids: str = ""  # comma-separated NORAD catalog IDs

    # OpenStreetMap Overpass
    osm_enabled: bool = False
    osm_poll_interval: int = 3600
    osm_bbox: str = ""  # "south,west,north,east"
    osm_feature_types: str = "military,aeroway,port,border_crossing,power_plant,embassy"
    osm_overpass_url: str = "https://overpass-api.de/api/interpreter"

    # Public CCTV
    cctv_enabled: bool = False
    cctv_poll_interval: int = 300
    cctv_dot_endpoints: str = ""  # comma-separated DOT 511 API URLs

    # Admin API key — required to access command and admin endpoints.
    # If unset, all admin/command endpoints return 403.
    # Set via ADMIN_API_KEY env var or in .env.
    admin_api_key: str = ""

    # Auth settings
    auth_enabled: bool = False
    auth_session_ttl_hours: int = 720  # 30 days
    auth_challenge_ttl_minutes: int = 5

    # CORS settings
    cors_origins: str = "http://localhost:5173,http://localhost:8000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
