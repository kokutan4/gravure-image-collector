from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .api import XApiError, XClient
from .collector import Collector
from .config import load_config
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gravure-collect",
        description="公式 X API を使って、指定アカウントの投稿画像をローカルに保存します",
    )
    parser.add_argument("-c", "--config", type=Path, default=Path("config.toml"),
                        help="設定ファイル(既定: config.toml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="ダウンロードせず、保存対象を表示するだけ(API の読み取りは行います)")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細ログを表示")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose or args.dry_run else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 2

    store = Store(config.db_path)
    try:
        client = XClient(config.bearer_token, min_interval=config.min_request_interval)
        results = Collector(config, client, store).run(dry_run=args.dry_run)
    except XApiError as e:
        print(f"X API エラー: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()

    failed = 0
    for username, s in results.items():
        print(f"@{username}: 新規 {s.saved} 件 / 取得済み {s.skipped_known} 件 / "
              f"重複 {s.skipped_duplicate} 件 / 失敗 {s.failed} 件(メディア付き投稿 {s.posts} 件)")
        failed += s.failed
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
