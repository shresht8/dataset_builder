"""Environment-driven settings. See .env.example for the full list."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    storage_endpoint_url: str
    storage_bucket: str
    storage_access_key: str
    storage_secret_key: str

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid,profile,email"

    pat_default_ttl_days: int = 90
    app_secret_key: str = "change-me"


settings = Settings()  # type: ignore[call-arg]
