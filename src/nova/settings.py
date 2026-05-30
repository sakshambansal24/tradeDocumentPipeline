from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MODEL_COSTS = {
    "gpt-4o": {"input_per_1m": 2.50, "output_per_1m": 10.00},
    "gpt-4.1-mini": {"input_per_1m": 0.40, "output_per_1m": 1.60},
    "gemini-1.5-flash": {"input_per_1m": 0.35, "output_per_1m": 1.05},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "nova-doc-pipeline"
    environment: str = "local"
    database_url: str = "sqlite:///./nova.db"

    openai_api_key: str = Field(default="", repr=False)
    google_api_key: str = Field(default="", repr=False)

    langfuse_public_key: str = Field(default="", repr=False)
    langfuse_secret_key: str = Field(default="", repr=False)
    langfuse_host: str = "https://cloud.langfuse.com"

    primary_vision_model: str = "gpt-4o"
    fallback_vision_model: str = "gemini-1.5-flash"
    non_vision_model: str = "gpt-4.1-mini"


class RuntimeLimits(BaseSettings):
    model_config = ConfigDict(extra="forbid")

    max_extraction_retries: int = Field(default=2, ge=0)
    max_document_pages: int = Field(default=20, ge=1)
    max_cost_usd_per_run: float = Field(default=0.75, ge=0.0)


def get_settings() -> Settings:
    return Settings()
