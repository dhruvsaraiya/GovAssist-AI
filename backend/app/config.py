"""Configuration management for backend.

Loads environment variables (from backend/.env if python-dotenv is used externally
or env exported in shell). We keep this lightweight using pydantic BaseSettings.
"""

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    backend_port: int = Field(8000, alias="BACKEND_PORT")
    backend_log_level: str = Field("info", alias="BACKEND_LOG_LEVEL")

    azure_openai_endpoint: Optional[HttpUrl] = Field(None, alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_deployment_name: str = Field("gpt-realtime", alias="AZURE_OPENAI_DEPLOYMENT_NAME")
    openai_api_version: str = Field("2025-08-28", alias="OPENAI_API_VERSION")

    class Config:
        case_sensitive = False
        # Use .env inside backend directory. Previously set to "backend/.env" which
        # fails when CWD is already backend (looked for backend/backend/.env).
        env_file = ".env"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()  # type: ignore
    # Lazy lightweight debug (only on first load)
    try:
        import logging
        logging.getLogger(__name__).info(
            "Loaded settings azure_endpoint=%s deployment=%s", s.azure_openai_endpoint, s.azure_openai_deployment_name
        )
    except Exception:
        pass
    return s
