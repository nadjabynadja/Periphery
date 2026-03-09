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
    enrichment_tier3_min_priority: int = 1
    enrichment_geocoder: str = "nominatim"
    enrichment_geocode_rate_limit: float = 1.0
    enrichment_fuzzy_match_threshold: float = 0.88

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
