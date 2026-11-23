# gravure-image-collector

X(旧 Twitter)で指定したアカウントの投稿画像を、**公式 X API v2** 経由で取得してローカルに保存する個人用ツールです。

## 方針(スクレイピングはしません)

- X のページを読み込む・ログインを自動操作する、といったスクレイピングは一切しません。データは公式 API からのみ取得します。
- 認証は自分で発行した Bearer Token のみ。User-Agent の偽装、プロキシの切り替え、Bot 検知の回避などは行いません。
- API リクエスト間に間隔を空け、レート制限(429)に当たったら `x-rate-limit-reset` の時刻まで待ってから再開します。
- 前回取得した位置(`since_id`)を覚えているので、2回目以降は新しい投稿だけを読みます。API の読み取り量も最小限になります。
- 取得した画像は個人での閲覧用です。再配布・転載はしないでください(著作権・肖像権があります)。

## 必要なもの

- Python 3.11 以上
- X Developer Portal(https://developer.x.com/)のアカウントと、投稿の読み取りができるプランの Bearer Token
  - 読み取りの上限や料金はプランによって異なり、変更されることもあります。使う前に Developer Portal で確認してください。
  - `max_posts_per_account` を小さめにしておくと読み取り量を抑えられます。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env                 # X_BEARER_TOKEN を書き込む
cp config.example.toml config.toml   # accounts に収集したいアカウントを書く(候補は docs/idols.md)
```

## 使い方

```bash
# まずはダウンロードせずに対象を確認
gravure-collect --dry-run

# 保存する
gravure-collect -v
```

画像は `images/<アカウント名>/<投稿日>_<投稿ID>_<番号>.jpg` に保存されます。

```
@example_account: 新規 12 件 / 取得済み 0 件 / 重複 1 件 / 失敗 0 件(メディア付き投稿 9 件)
```

## 重複の扱い

- 一度保存したメディアは `collector.sqlite3` に記録し、次回以降はダウンロードしません。
- 別の投稿で同じ画像が再掲されている場合は、ファイルの中身(SHA-256)で判定して保存しません。
- ダウンロードに失敗したものがあった場合は取得位置を進めないので、次回の実行で再取得します。

## 設定項目(config.toml)

| 項目 | 既定値 | 説明 |
| --- | --- | --- |
| `accounts` | なし | 収集するアカウント名(`@` なしで可) |
| `output_dir` | `images` | 保存先 |
| `db_path` | `collector.sqlite3` | 取得記録のデータベース |
| `max_posts_per_account` | 100 | 1回の実行で1アカウントあたり読む投稿数の上限 |
| `exclude_retweets` / `exclude_replies` | true | リポスト・リプライを除外 |
| `include_videos` | false | 動画・GIF も保存する |
| `min_request_interval` | 1.5 | API リクエスト間の最小間隔(秒) |
| `min_download_interval` | 0.5 | 画像ダウンロード間の最小間隔(秒) |

注意: 初回や久しぶりの実行で、前回以降の投稿が `max_posts_per_account` より多い場合、それより古い分は取得されません。

## 定期実行の例(cron、1日1回)

```cron
0 7 * * * cd /path/to/gravure-image-collector && .venv/bin/gravure-collect >> collect.log 2>&1
```

## 開発

```bash
pip install -e '.[dev]'
pytest
```
