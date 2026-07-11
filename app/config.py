from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_openai_api_key: str
    azure_openai_endpoint: str
    azure_openai_chat_deployment_name: str
    azure_openai_api_version: str = "2024-10-21"


@lru_cache
def get_settings() -> Settings:
    return Settings()
