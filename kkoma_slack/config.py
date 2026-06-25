from dataclasses import dataclass
import os
from pathlib import Path


def _path_from_env(name: str, default: Path, base: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


ROOT_DIR = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    data_dir: Path = _path_from_env("KKOMA_DATA_DIR", ROOT_DIR / "data", ROOT_DIR)
    state_db_path: Path = _path_from_env("KKOMA_STATE_DB", ROOT_DIR / "data" / "game_state.db", ROOT_DIR)
    port: int = int(os.environ.get("PORT", os.environ.get("KKOMA_PORT", "3339")))
    engine_mode: str = os.environ.get("KKOMA_ENGINE_MODE", "self_hosted")
    remote_base_url: str = os.environ.get("KKOMA_REMOTE_BASE_URL", "https://semantle-ko.newsjel.ly")
    sema_remote_base_url: str = os.environ.get("KKOMA_SEMA_REMOTE_BASE_URL", "https://legacy.semantle.com")
    enable_sema: bool = os.environ.get("KKOMA_ENABLE_SEMA", "1") != "0"
    en_data_dir: Path = _path_from_env("KKOMA_EN_DATA_DIR", ROOT_DIR / "data" / "en", ROOT_DIR)
    slack_signing_secret: str = os.environ.get("SLACK_SIGNING_SECRET", "")
    public_responses: bool = os.environ.get("KKOMA_PUBLIC_RESPONSES", "1") != "0"
    allow_score_only: bool = os.environ.get("KKOMA_ALLOW_SCORE_ONLY", "0") == "1"
    allow_unsigned: bool = os.environ.get("KKOMA_ALLOW_UNSIGNED", "0") == "1"


settings = Settings()
