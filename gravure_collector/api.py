"""公式 X API v2 の薄いクライアント。

- 認証は利用者自身の Bearer Token のみ
- リクエスト間隔を空け、429 では x-rate-limit-reset まで待つ
- User-Agent は偽装せず、このツール名をそのまま名乗る
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import requests

from . import USER_AGENT

log = logging.getLogger(__name__)

API_BASE = "https://api.x.com/2"
MAX_RETRIES = 5


class XApiError(RuntimeError):
    pass


@dataclass
class Media:
    media_key: str
    type: str  # photo / video / animated_gif
    url: str  # ダウンロードに使う URL


@dataclass
class Post:
    id: str
    created_at: str
    media: list[Media]


class Throttle:
    """呼び出し間に最小間隔を空ける。"""

    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self.interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


def best_media_url(media: dict) -> str | None:
    mtype = media.get("type")
    if mtype == "photo":
        url = media.get("url")
        if not url:
            return None
        # 公式 API が返す画像 URL に、元サイズ指定を付ける
        return url if "?" in url else f"{url}?name=orig"
    # video / animated_gif は mp4 の中で最もビットレートが高いものを選ぶ
    variants = [v for v in media.get("variants", []) if v.get("content_type") == "video/mp4"]
    if not variants:
        return None
    return max(variants, key=lambda v: v.get("bit_rate", 0))["url"]


class XClient:
    def __init__(self, bearer_token: str, min_interval: float = 1.5,
                 session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.time):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": USER_AGENT,
        })
        self._sleep = sleep
        self._now = now
        self.throttle = Throttle(min_interval, sleep=sleep)

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(1, MAX_RETRIES + 1):
            self.throttle.wait()
            resp = self.session.get(f"{API_BASE}{path}", params=params, timeout=30)

            if resp.status_code == 429:
                reset = resp.headers.get("x-rate-limit-reset")
                wait = max(1.0, float(reset) - self._now() + 1) if reset else 60.0 * attempt
                log.warning("レート制限に達しました。%.0f 秒待ってから再開します", wait)
                self._sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                wait = min(60.0, 2.0 ** attempt)
                log.warning("X API がエラー %s を返しました。%.0f 秒後に再試行します", resp.status_code, wait)
                self._sleep(wait)
                continue
            if resp.status_code != 200:
                raise XApiError(f"X API エラー {resp.status_code}: {resp.text[:300]}")

            remaining = resp.headers.get("x-rate-limit-remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) == 0:
                reset = resp.headers.get("x-rate-limit-reset")
                if reset:
                    wait = max(0.0, float(reset) - self._now() + 1)
                    log.info("このエンドポイントの残り回数が 0 です。%.0f 秒待ちます", wait)
                    self._sleep(wait)
            return resp.json()
        raise XApiError(f"{path} の取得に {MAX_RETRIES} 回失敗しました")

    def get_user_id(self, username: str) -> str:
        body = self._get(f"/users/by/username/{username}")
        if "data" not in body:
            raise XApiError(f"@{username} が見つかりません: {body.get('errors')}")
        return body["data"]["id"]

    def fetch_media_posts(self, user_id: str, *, since_id: str | None, limit: int,
                          exclude_retweets: bool, exclude_replies: bool,
                          include_videos: bool) -> tuple[list[Post], str | None]:
        """新しい順にメディア付き投稿を集める。戻り値の2つ目は今回見た最新の投稿 ID。"""
        exclude = [n for n, on in (("retweets", exclude_retweets), ("replies", exclude_replies)) if on]
        posts: list[Post] = []
        fetched = 0
        token: str | None = None
        newest_id: str | None = None
        while fetched < limit:
            params = {
                "max_results": max(5, min(100, limit - fetched)),
                "expansions": "attachments.media_keys",
                "media.fields": "media_key,type,url,variants",
                "tweet.fields": "created_at,attachments",
            }
            if exclude:
                params["exclude"] = ",".join(exclude)
            if since_id:
                params["since_id"] = since_id
            if token:
                params["pagination_token"] = token

            body = self._get(f"/users/{user_id}/tweets", params)
            meta = body.get("meta", {})
            if newest_id is None:
                newest_id = meta.get("newest_id")
            media_by_key = {m["media_key"]: m for m in body.get("includes", {}).get("media", [])}

            for tweet in body.get("data", []):
                if fetched >= limit:
                    break
                fetched += 1
                items = []
                for key in tweet.get("attachments", {}).get("media_keys", []):
                    m = media_by_key.get(key)
                    if not m or (m.get("type") != "photo" and not include_videos):
                        continue
                    url = best_media_url(m)
                    if url:
                        items.append(Media(media_key=key, type=m["type"], url=url))
                if items:
                    posts.append(Post(id=tweet["id"], created_at=tweet.get("created_at", ""), media=items))

            token = meta.get("next_token")
            if not token:
                break
        return posts, newest_id
