# delta-lint GitHub App セットアップ

## 1. GitHub App 登録

### 1.1 App 作成

1. https://github.com/settings/apps/new にアクセス
2. 以下を入力:

| 項目 | 値 |
|---|---|
| GitHub App name | `delta-lint` |
| Homepage URL | リポジトリの URL |
| Webhook URL | `https://your-server.com/webhook`（後で変更可） |
| Webhook secret | ランダム文字列を生成して入力 |

### 1.2 Permissions

**Repository permissions:**

| Permission | Access | 用途 |
|---|---|---|
| Contents | Read | コード読み取り |
| Pull requests | Read & Write | PR コメント、レビュー |
| Checks | Read & Write | Check Run アノテーション |
| Issues | Read & Write | コメント投稿 |
| Metadata | Read | リポ情報（visibility 判定） |

**Subscribe to events:**

- [x] Pull request
- [x] Issue comment
- [x] Pull request review

### 1.3 Private key 生成

App 作成後、設定ページ下部の「Generate a private key」をクリック。
`.pem` ファイルがダウンロードされる。これをサーバーに配置。

### 1.4 App ID 確認

設定ページ上部の「App ID」をメモ。

---

## 2. ローカル開発

### 2.1 smee.io でトンネル

開発中は smee.io を使って GitHub webhook をローカルに転送:

```bash
# smee チャンネル作成
# https://smee.io/ にアクセスして URL を取得

# smee クライアント実行
npx smee-client --url https://smee.io/YOUR_CHANNEL --target http://localhost:3000/webhook
```

GitHub App の Webhook URL を `https://smee.io/YOUR_CHANNEL` に設定。

### 2.2 環境変数

```bash
cp .env.example .env
# .env を編集:
#   GITHUB_APP_ID=（1.4 で確認した ID）
#   GITHUB_PRIVATE_KEY_PATH=./private-key.pem
#   GITHUB_WEBHOOK_SECRET=（1.1 で設定した secret）
#   ANTHROPIC_API_KEY=sk-ant-...
```

### 2.3 サーバー起動

```bash
cd plugins/delta-lint/app
pip install -r requirements.txt
python webhook.py
# → http://localhost:3000 で起動
# → /health で疎通確認
```

### 2.4 動作確認

1. GitHub App を自分のテストリポにインストール
2. テスト PR を作成
3. smee 経由で webhook がローカルに届く
4. delta-lint がスキャンを実行
5. PR にインラインコメント + サマリが投稿される

---

## 3. デプロイ

### 3.1 Railway（推奨・最安）

```bash
# Railway CLI
railway login
railway init
railway up

# 環境変数を設定
railway variables set GITHUB_APP_ID=...
railway variables set GITHUB_PRIVATE_KEY="$(cat private-key.pem)"
railway variables set GITHUB_WEBHOOK_SECRET=...
railway variables set ANTHROPIC_API_KEY=...
```

月額: ~$5（Hobby プラン）

### 3.2 Fly.io

```bash
fly launch
fly secrets set GITHUB_APP_ID=...
fly secrets set GITHUB_PRIVATE_KEY="$(cat private-key.pem)"
fly secrets set GITHUB_WEBHOOK_SECRET=...
fly secrets set ANTHROPIC_API_KEY=...
fly deploy
```

### 3.3 Docker

```bash
cd plugins/delta-lint
docker build -f app/Dockerfile -t delta-lint-app .
docker run -p 3000:3000 --env-file app/.env delta-lint-app
```

---

## 4. Marketplace 公開

### 4.1 前提条件

- App がインストール可能で正常動作している
- Webhook URL が本番サーバーを指している
- プライバシーポリシー URL がある
- サポート URL がある

### 4.2 Listing 作成

GitHub App 設定ページ → 「Marketplace listing」タブ:

1. **カテゴリ**: Code quality
2. **説明**: Detect structural contradictions between files that tests miss
3. **Pricing plans**:
   - Free: Public repos unlimited, Private repos 5 scans/month
   - Pro ($X/month): Unlimited scans + δ_nats health barometer
   - Enterprise: Custom

### 4.3 審査

「Submit for review」→ GitHub チームが確認（通常数日）。
審査基準は「アプリが動くこと」「説明が正確であること」程度。

---

## 5. アーキテクチャ

```
GitHub ──webhook──→ FastAPI (webhook.py)
                        │
                        ├── get_installation_token()  ← JWT 認証
                        ├── clone_repo()              ← shallow clone
                        ├── scanner.scan()            ← 既存パイプライン再利用
                        ├── build_inline_comments()   ← finding → review comment
                        └── GitHubClient
                             ├── post_review()        ← PR インラインコメント
                             ├── post_comment()       ← サマリコメント
                             └── post_check_run()     ← Check アノテーション
```

### 既存コードとの関係

| コンポーネント | 再利用元 | 変更点 |
|---|---|---|
| scan パイプライン | `scanner.scan()` | そのまま |
| 出力フォーマット | `output_formats.py` | そのまま |
| GitHub API 呼び出し | `action/entrypoint.py` | `gh` CLI → `httpx` に置換 |
| PR コメント | `entrypoint.py` | marker ベースの upsert 同一ロジック |
| Check Run | `entrypoint.py` | バッチ分割ロジック同一 |
| インラインコメント | **新規** | `build_inline_comments()` |
| OSS 判定 | **新規** | `is_oss_repo()` |
| 設定読み込み | **新規** | `.delta-lint.yml` optional |
