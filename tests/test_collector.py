from __future__ import annotations

from pathlib import Path

import pytest

from gravure_collector import USER_AGENT
from gravure_collector.api import XClient, best_media_url
from gravure_collector.collector import Collector
from gravure_collector.config import Config
from gravure_collector.store import Store


class FakeResponse:
    def __init__(self, status=200, json_body=None, content=b"", headers=None):
        self.status_code = status
        self._json = json_body or {}
        self.content = content
        self.headers = headers or {}
        self.text = str(json_body)

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        r = self.responses.pop(0)
        return r(url) if callable(r) else r


def timeline_page(tweets, media, newest_id=None, next_token=None):
    meta = {}
    if newest_id:
        meta["newest_id"] = newest_id
    if next_token:
        meta["next_token"] = next_token
    return FakeResponse(json_body={"data": tweets, "includes": {"media": media}, "meta": meta})


def make_config(tmp_path: Path, **kw) -> Config:
    return Config(accounts=["idol"], output_dir=tmp_path / "images", db_path=tmp_path / "db.sqlite3",
                  min_request_interval=0, min_download_interval=0, bearer_token="t", **kw)


def make_client(session):
    return XClient("t", min_interval=0, session=session, sleep=lambda s: None, now=lambda: 0)


TWEETS = [
    {"id": "200", "created_at": "2026-11-20T10:00:00.000Z", "attachments": {"media_keys": ["3_a", "3_b"]}},
    {"id": "199", "created_at": "2026-11-19T10:00:00.000Z", "attachments": {"media_keys": ["7_v"]}},
    {"id": "198", "created_at": "2026-11-18T10:00:00.000Z"},
]
MEDIA = [
    {"media_key": "3_a", "type": "photo", "url": "https://pbs.twimg.com/media/A.jpg"},
    {"media_key": "3_b", "type": "photo", "url": "https://pbs.twimg.com/media/B.jpg"},
    {"media_key": "7_v", "type": "video", "variants": [
        {"content_type": "video/mp4", "bit_rate": 100, "url": "https://video.twimg.com/low.mp4"},
        {"content_type": "video/mp4", "bit_rate": 900, "url": "https://video.twimg.com/high.mp4"},
        {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/x.m3u8"},
    ]},
]


def test_best_media_url():
    assert best_media_url(MEDIA[0]) == "https://pbs.twimg.com/media/A.jpg?name=orig"
    assert best_media_url(MEDIA[2]) == "https://video.twimg.com/high.mp4"


def test_collects_photos_and_dedupes_identical_images(tmp_path):
    api = FakeSession([
        FakeResponse(json_body={"data": {"id": "42"}}),
        timeline_page(TWEETS, MEDIA, newest_id="200"),
    ])
    # A と B は中身が同じ画像(再掲)
    http = FakeSession([FakeResponse(content=b"same"), FakeResponse(content=b"same")])
    store = Store(tmp_path / "db.sqlite3")
    stats = Collector(make_config(tmp_path), make_client(api), store, http=http).collect_account("idol")

    assert stats.saved == 1 and stats.skipped_duplicate == 1 and stats.failed == 0
    files = list((tmp_path / "images" / "idol").iterdir())
    assert [f.name for f in files] == ["20261120_200_1.jpg"]
    assert http.calls[0][0].endswith("A.jpg?name=orig")
    assert store.get_account("idol") == ("42", "200")
    params = api.calls[1][1]
    assert params["exclude"] == "retweets,replies" and "since_id" not in params


def test_second_run_uses_since_id_and_skips_known(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.save_account("idol", "42")
    store.set_since_id("idol", "200")
    store.add_media("3_a", "200", "idol", "x", "p")
    api = FakeSession([timeline_page([TWEETS[0]], MEDIA[:2], newest_id="201")])
    http = FakeSession([FakeResponse(content=b"new")])
    stats = Collector(make_config(tmp_path), make_client(api), store, http=http).collect_account("idol")

    assert api.calls[0][1]["since_id"] == "200"
    assert stats.skipped_known == 1 and stats.saved == 1
    assert store.get_account("idol") == ("42", "201")


def test_failed_download_keeps_since_id(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.save_account("idol", "42")
    api = FakeSession([timeline_page([TWEETS[0]], MEDIA[:2], newest_id="200")])
    http = FakeSession([FakeResponse(status=500), FakeResponse(content=b"ok")])
    stats = Collector(make_config(tmp_path), make_client(api), store, http=http).collect_account("idol")
    assert stats.failed == 1 and stats.saved == 1
    assert store.get_account("idol") == ("42", None)


def test_videos_included_when_enabled(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.save_account("idol", "42")
    api = FakeSession([timeline_page([TWEETS[1]], MEDIA, newest_id="199")])
    http = FakeSession([FakeResponse(content=b"video")])
    stats = Collector(make_config(tmp_path, include_videos=True), make_client(api), store,
                      http=http).collect_account("idol")
    assert stats.saved == 1
    assert (tmp_path / "images" / "idol" / "20261119_199_1.mp4").exists()


def test_rate_limit_waits_until_reset():
    slept = []
    api = FakeSession([
        FakeResponse(status=429, headers={"x-rate-limit-reset": "100"}),
        FakeResponse(json_body={"data": {"id": "42"}}),
    ])
    client = XClient("t", min_interval=0, session=api, sleep=slept.append, now=lambda: 10)
    assert client.get_user_id("idol") == "42"
    assert slept == [91.0]
    assert api.headers["User-Agent"] == USER_AGENT


def test_pagination_respects_limit():
    page1 = timeline_page([{"id": str(i), "attachments": {"media_keys": ["3_a"]}} for i in range(100, 95, -1)],
                          MEDIA[:1], newest_id="100", next_token="n1")
    page2 = timeline_page([{"id": str(i), "attachments": {"media_keys": ["3_a"]}} for i in range(95, 90, -1)],
                          MEDIA[:1], newest_id="95")
    api = FakeSession([page1, page2])
    posts, newest = make_client(api).fetch_media_posts(
        "42", since_id=None, limit=7, exclude_retweets=False, exclude_replies=False, include_videos=False)
    assert len(posts) == 7 and newest == "100"
    assert api.calls[1][1]["pagination_token"] == "n1"
    assert api.calls[1][1]["max_results"] == 5
    assert "exclude" not in api.calls[0][1]


def test_load_config_requires_token(tmp_path, monkeypatch):
    from gravure_collector.config import load_config
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    cfg = tmp_path / "c.toml"
    cfg.write_text('accounts = ["@idol"]\n')
    with pytest.raises(ValueError):
        load_config(cfg, env_path=tmp_path / "none")
    monkeypatch.setenv("X_BEARER_TOKEN", "abc")
    assert load_config(cfg, env_path=tmp_path / "none").accounts == ["idol"]
