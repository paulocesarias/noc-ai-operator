"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Database
    database_url: str = "postgresql+asyncpg://localhost:5432/noc_ai"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Claude AI
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    claude_max_tokens: int = 4096

    # Kubernetes
    kubeconfig_path: str | None = None
    k8s_namespace: str = "default"

    # AlertManager
    alertmanager_url: str = "http://localhost:9093"

    # Syslog
    syslog_port: int = 514

    # SNMP
    snmp_port: int = 162

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"


settings = Settings()
