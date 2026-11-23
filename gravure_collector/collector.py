from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import USER_AGENT
from .api import Media, Post, Throttle, XApiError, XClient
from .config import Config
from .store import Store

log = logging.getLogger(__name__)

EXT_BY_TYPE = {"photo": ".jpg", "video": ".mp4", "animated_gif": ".mp4"}


@dataclass
class Stats:
    posts: int = 0
    saved: int = 0
    skipped_known: int = 0
    skipped_duplicate: int = 0
    failed: int = 0


def _extension(media: Media) -> str:
    suffix = Path(urlparse(media.url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"} else EXT_BY_TYPE.get(media.type, ".bin")


class Collector:
    def __init__(self, config: Config, client: XClient, store: Store,
                 http: requests.Session | None = None):
        self.config = config
        self.client = client
        self.store = store
        self.http = http or requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT})
        self.download_throttle = Throttle(config.min_download_interval)

    def run(self, dry_run: bool = False) -> dict[str, Stats]:
        results = {}
        for username in self.config.accounts:
            log.info("@%s を確認しています", username)
            try:
                results[username] = self.collect_account(username, dry_run=dry_run)
            except XApiError as e:
                # 1アカウントの失敗で全体を止めない
                log.error("@%s の取得に失敗しました: %s", username, e)
                results[username] = Stats(failed=1)
        return results

    def collect_account(self, username: str, dry_run: bool = False) -> Stats:
        stats = Stats()
        known = self.store.get_account(username)
        if known:
            user_id, since_id = known
        else:
            user_id, since_id = self.client.get_user_id(username), None
            self.store.save_account(username, user_id)

        posts, newest_id = self.client.fetch_media_posts(
            user_id,
            since_id=since_id,
            limit=self.config.max_posts_per_account,
            exclude_retweets=self.config.exclude_retweets,
            exclude_replies=self.config.exclude_replies,
            include_videos=self.config.include_videos,
        )
        stats.posts = len(posts)

        for post in posts:
            for index, media in enumerate(post.media, start=1):
                self._save_media(username, post, index, media, stats, dry_run)

        # 失敗が無いときだけ「ここまで取得済み」を進める(失敗分は次回やり直せる)
        if newest_id and not dry_run and stats.failed == 0:
            self.store.set_since_id(username, newest_id)
        return stats

    def _save_media(self, username: str, post: Post, index: int, media: Media,
                    stats: Stats, dry_run: bool) -> None:
        if self.store.has_media(media.media_key):
            stats.skipped_known += 1
            return
        if dry_run:
            log.info("[dry-run] %s %s", post.id, media.url)
            stats.saved += 1
            return

        self.download_throttle.wait()
        try:
            resp = self.http.get(media.url, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("ダウンロード失敗 %s: %s", media.url, e)
            stats.failed += 1
            return

        data = resp.content
        digest = hashlib.sha256(data).hexdigest()
        existing = self.store.find_by_hash(digest)
        if existing:
            # 同じ画像が別の投稿で再掲されていた場合は保存しない
            self.store.add_media(media.media_key, post.id, username, digest, None)
            stats.skipped_duplicate += 1
            return

        folder = self.config.output_dir / username
        folder.mkdir(parents=True, exist_ok=True)
        date = post.created_at[:10].replace("-", "") or "unknown"
        path = folder / f"{date}_{post.id}_{index}{_extension(media)}"
        path.write_bytes(data)
        self.store.add_media(media.media_key, post.id, username, digest, str(path))
        stats.saved += 1
        log.info("保存: %s", path)
