from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    accounts: list[str]
    output_dir: Path = Path("images")
    db_path: Path = Path("collector.sqlite3")
    max_posts_per_account: int = 100
    exclude_retweets: bool = True
    exclude_replies: bool = True
    include_videos: bool = False
    min_request_interval: float = 1.5
    min_download_interval: float = 0.5
    bearer_token: str = field(default="", repr=False)


def _load_dotenv(path: Path) -> None:
    """依存を増やさないための最小限の .env 読み込み(既存の環境変数は上書きしない)。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: Path, env_path: Path = Path(".env")) -> Config:
    _load_dotenv(env_path)
    with path.open("rb") as f:
        raw = tomllib.load(f)

    accounts = [a.strip().lstrip("@") for a in raw.get("accounts", []) if a.strip()]
    if not accounts:
        raise ValueError("config の accounts に収集対象のアカウントを1つ以上書いてください")

    max_posts = int(raw.get("max_posts_per_account", 100))
    if max_posts < 1:
        raise ValueError("max_posts_per_account は 1 以上にしてください")

    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        raise ValueError("X_BEARER_TOKEN が設定されていません(.env か環境変数に設定してください)")

    return Config(
        accounts=accounts,
        output_dir=Path(raw.get("output_dir", "images")),
        db_path=Path(raw.get("db_path", "collector.sqlite3")),
        max_posts_per_account=max_posts,
        exclude_retweets=bool(raw.get("exclude_retweets", True)),
        exclude_replies=bool(raw.get("exclude_replies", True)),
        include_videos=bool(raw.get("include_videos", False)),
        min_request_interval=max(0.0, float(raw.get("min_request_interval", 1.5))),
        min_download_interval=max(0.0, float(raw.get("min_download_interval", 0.5))),
        bearer_token=token,
    )
