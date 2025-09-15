"""Configuration management for backend.

Loads environment variables (from backend/.env if python-dotenv is used externally
or env exported in shell). We keep this lightweight using pydantic BaseSettings.
"""

from pydantic import Field
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    backend_port: int = Field(8000, alias="BACKEND_PORT")
    backend_log_level: str = Field("info", alias="BACKEND_LOG_LEVEL")

    # Accept raw string so that wss:// scheme (not valid for HttpUrl) can be supplied
    azure_openai_endpoint: Optional[str] = Field(None, alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_deployment_name: str = Field("gpt-realtime", alias="AZURE_OPENAI_DEPLOYMENT_NAME")
    # Updated default API version aligned with latest realtime preview
    openai_api_version: str = Field("2025-04-01-preview", alias="OPENAI_API_VERSION")
    # Optional API key support (either var accepted). If both present, azure_openai_key preferred.
    azure_openai_key: Optional[str] = Field(None, alias="AZURE_OPENAI_KEY")
    azure_openai_api_key: Optional[str] = Field(None, alias="AZURE_OPENAI_API_KEY")

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
