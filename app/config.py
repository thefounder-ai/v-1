import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    app_name: str = os.getenv("APP_NAME", "SkillOrbit")
    supabase_url: str = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    qdrant_url: str = os.getenv("QDRANT_URL", "").rstrip("/")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "skillorbit_products")
    mesh_api_key: str = os.getenv("MESH_API_KEY", "")
    mesh_api_base_url: str = os.getenv("MESH_API_BASE_URL", "https://api.meshapi.ai/v1").rstrip("/")
    mesh_embedding_model: str = os.getenv("MESH_EMBEDDING_MODEL", "text-embedding-3-small")
    mesh_chat_model: str = os.getenv("MESH_CHAT_MODEL", "openai/gpt-4.1-mini")
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    resend_from_email: str = os.getenv("RESEND_FROM_EMAIL", "SkillOrbit <onboarding@resend.dev>")
    app_public_url: str = os.getenv("APP_PUBLIC_URL", "http://localhost:5000")
    cron_secret: str = os.getenv("CRON_SECRET", "")
    digest_interval_days: int = int(os.getenv("DIGEST_INTERVAL_DAYS", "7"))

    @property
    def supabase_service_role_key(self) -> str:
        return os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)

    @property
    def vector_configured(self) -> bool:
        return bool(self.qdrant_url and self.qdrant_api_key)

    @property
    def mesh_configured(self) -> bool:
        return bool(self.mesh_api_key)

    @property
    def resend_configured(self) -> bool:
        return bool(self.resend_api_key and self.resend_from_email)

    @property
    def digest_configured(self) -> bool:
        return self.resend_configured and bool(self.supabase_service_role_key)

    @property
    def cron_configured(self) -> bool:
        return bool(self.cron_secret)


settings = Settings()
