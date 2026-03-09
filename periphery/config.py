from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    embedding_model: str = "all-MiniLM-L6-v2"
    faiss_index_path: str = "data/faiss/index.bin"
    device: str = "cpu"
    crystallizer_interval: int = 300  # seconds

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
    pipeline_crystallization_min_batch: int = 10
    pipeline_stale_claim_timeout_seconds: float = 600.0
    pipeline_consumer_restart_delay: float = 5.0
    pipeline_max_retries: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
