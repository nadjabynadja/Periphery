from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    embedding_model: str = "all-MiniLM-L6-v2"
    faiss_index_path: str = "data/faiss/index.bin"
    device: str = "cpu"
    crystallizer_interval: int = 300  # seconds

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
