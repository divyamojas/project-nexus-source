from __future__ import annotations

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_PUBLISHABLE_KEY: str = ""
    DATABASE_URL: str = ""
    JWT_SECRET: str = ""
    CORS_ORIGINS: str = "*"
    ENABLE_RAW_SQL: bool = False
    S3_SYNC_ENABLED: str = "false"
    S3_BUCKET_NAME: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    @property
    def cors_origins_list(self) -> List[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def s3_enabled(self) -> bool:
        return self.S3_SYNC_ENABLED.lower() == "true"


settings = Settings()
