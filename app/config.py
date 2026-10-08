import os

# Try loading .env if present
_env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k not in os.environ:
                    os.environ[k] = v

class Settings:
    BROKER_NAME: str = os.getenv("BROKER_NAME", "BharatInvest")
    PROVIDER_CODE: str = os.getenv("PROVIDER_CODE", "b")
    DP_NAME: str = os.getenv("DP_NAME", "BharatInvest Securities")
    DP_ID: str = os.getenv("DP_ID", "IN300002")

    SECRET_KEY: str = os.getenv("SECRET_KEY", "bharatinvest-secret-key-change-in-production-2026")
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", os.getenv("SECRET_KEY", "bharatinvest-session-secret-change-in-production-2026"))
    ACCESS_TOKEN_EXPIRY_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRY_MINUTES", "60"))
    REFRESH_TOKEN_EXPIRY_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRY_DAYS", "7"))
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123")
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

    # TradeOne Integration & Shared Identity
    SHARED_IDENTITY_SALT: str = os.getenv("SHARED_IDENTITY_SALT", "tradeone-shared-salt-2026")
    INTERNAL_API_ENABLED: bool = os.getenv("INTERNAL_API_ENABLED", "true").lower() in ("true", "1")
    INTERNAL_API_KEY: str = os.getenv("INTERNAL_API_KEY", "tradeone-internal-secret-key-2026")
    TRADEONE_URL: str = os.getenv("TRADEONE_URL", "http://localhost:8080")

    # Database URL with PostgreSQL compatibility (e.g. Render/Neon/Supabase postgres:// -> postgresql://)
    _raw_db_url = os.getenv("DATABASE_URL", "sqlite:///./bharatinvest.db")
    if _raw_db_url.startswith("postgres://"):
        _raw_db_url = _raw_db_url.replace("postgres://", "postgresql://", 1)
    DATABASE_URL: str = _raw_db_url

    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "")

    # Starting Funds & Portfolio Defaults
    STARTING_FUNDS: float = float(os.getenv("STARTING_FUNDS", "1000000.0"))
    SEED_STARTER_PORTFOLIO: bool = os.getenv("SEED_STARTER_PORTFOLIO", "false").lower() in ("true", "1")

    # Allowed Emails
    _raw_allowed = os.getenv("ALLOWED_EMAILS", "")
    ALLOWED_EMAILS: list = [e.strip().lower() for e in _raw_allowed.split(",") if e.strip()]

settings = Settings()
