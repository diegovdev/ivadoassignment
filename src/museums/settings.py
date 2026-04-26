from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Full connection string (takes precedence — used in dev/SQLite/preview)
    database_url: str | None = None

    # Individual parts injected by ECS (DB_PASSWORD comes from Secrets Manager)
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "museums"
    db_user: str = "postgres"
    db_password: str | None = None

    min_visitors: int = 2_000_000
    wikipedia_rest_api: str = "https://en.wikipedia.org/api/rest_v1"
    wikidata_sparql_url: str = "https://query.wikidata.org/sparql"

    @property
    def effective_database_url(self) -> str:
        """Resolve the SQLAlchemy database URL from available config."""
        if self.database_url:
            return self.database_url
        if self.db_host and self.db_password:
            return (
                f"postgresql://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        # Local dev fallback — SQLite
        return "sqlite:///./data/museums.db"


settings = Settings()
