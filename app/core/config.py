from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "one-backend"
    app_env: str = "development"
    app_debug: bool = True
    api_prefix: str = "/api/v1"

    supabase_url: str = ""
    supabase_key: str = ""

    # Shared secret sent by proxy-server in X-Gateway-Token. Only requests carrying it
    # are trusted to name their actor and may create purchase orders.
    gateway_token: str = ""

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def gateway_configured(self) -> bool:
        # CHANGE_ME is the placeholder Terraform puts in SSM before the real value is set
        return bool(self.gateway_token) and self.gateway_token != "CHANGE_ME"


@lru_cache
def get_settings() -> Settings:
    return Settings()
