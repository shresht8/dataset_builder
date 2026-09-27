"""Environment-driven settings. See .env.example for the full list."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Defaults match infra/docker-compose.yml so local dev boots without a .env.
    database_url: str = "postgresql+psycopg://groundline:groundline@localhost:5432/groundline"

    storage_endpoint_url: str = "http://localhost:9000"
    storage_bucket: str = "groundline"
    storage_access_key: str = "minioadmin"
    storage_secret_key: str = "minioadmin"

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid,profile,email"
    # GL-3-11: Entra SSO alongside dev login. Empty discovery URL -> derived
    # from entra_tenant_id. role_mapping is JSON: group object ID -> role,
    # plus "default" (design §5, PRD open decision 6).
    auth_sso_enabled: bool = False
    oidc_discovery_url: str = ""
    role_mapping: str = '{"default": "viewer"}'

    pat_default_ttl_days: int = 90
    # Signs session cookies (GL-1-9). The default is for local dev only.
    app_secret_key: str = "change-me"

    # GL-1-8: if set, create/promote this admin at API startup (idempotent).
    bootstrap_admin_email: str = ""

    # GL-1-9: deployment profile + passwordless dev login. Dev login is only
    # legal when app_env == "dev"; create_app() refuses to start otherwise.
    app_env: str = "dev"
    auth_dev_login: bool = True


settings = Settings()  # type: ignore[call-arg]
