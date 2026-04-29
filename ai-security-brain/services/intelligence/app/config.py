import os
from pathlib import Path

# Load .env file if present
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


class Settings:
    POSTGRES_URL: str = os.environ.get(
        "POSTGRES_URL", "postgres://asb:asb_dev@localhost:5432/asb"
    )
    CLICKHOUSE_URL: str = os.environ.get(
        "CLICKHOUSE_URL", "clickhouse://localhost:9000/default"
    )
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
    AWS_ACCESS_KEY_ID: str = os.environ.get("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    S3_BUCKET: str = os.environ.get("S3_BUCKET", "asb-data")
    PORT: int = int(os.environ.get("PORT", "8081"))
    CORE_DATA_DIR: str = os.environ.get("CORE_DATA_DIR", "")

    # Job schedule
    HOURLY_ENABLED: bool = os.environ.get("HOURLY_JOBS", "true").lower() == "true"
    DAILY_ENABLED: bool = os.environ.get("DAILY_JOBS", "true").lower() == "true"
    MONTHLY_ENABLED: bool = os.environ.get("MONTHLY_JOBS", "true").lower() == "true"

    # Analysis params
    DBSCAN_EPS_M: float = float(os.environ.get("DBSCAN_EPS", "3.0"))
    DBSCAN_MIN_SAMPLES: int = int(os.environ.get("DBSCAN_MIN_SAMPLES", "3"))
    HALLUCINATION_WINDOW_S: int = int(os.environ.get("HALLUCINATION_WINDOW", "5"))


settings = Settings()
