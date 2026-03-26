# delta-lint インテグレーション層 設計書

> **この文書は `architecture-commands.md`（コマンド責務）と並列の SSoT。**
> コマンド責務 → `architecture-commands.md`、レイヤー分割・CI 連携 → 本文書。
> 最終更新: 2026-03-27

---

## 1. 背景と目的

delta-lint は現在 Claude Code スキル（LLM 対話）専用。
企業利用では以下のトリガーに対応する必要がある:

| トリガー | タイミング | やること | 価値 |
|---|---|---|---|
| **PR open/sync** | PR 作成・push 時 | 変更ファイルの矛盾スキャン → PR コメント | ★★★ |
| **PR comment** | `/delta-scan` コメント | オンデマンドスキャン → 結果を返信 | ★★ |
| **Push to main** | マージ後 | ダッシュボード更新、findings ベースライン記録 | ★★ |
| **Scheduled (cron)** | 毎日/毎週 | フルスキャン、ストレステスト、負債トレンド | ★★ |
| **Release tag** | リリース前 | wide スキャン + セキュリティレンズ → ゲート | ★★★ |
| **Dependabot PR** | 依存更新時 | 更新パッケージに関連する矛盾チェック | ★ |
| **Pre-commit hook** | ローカル commit 前 | 変更ファイルだけ高速チェック | ★ |
| **Manual dispatch** | 手動 | 任意スコープで実行（初回導入・デバッグ） | ★ |

### 現状の問題

| 問題 | 詳細 |
|---|---|
| LLM 呼び出しが `claude -p` 前提 | CI 環境にはサブスク CLI がない。API キーが必要 |
| LLM バックエンド切替が 4 ファイルに重複実装 | detector.py, verifier.py, deep_verifier.py, fixgen.py が各自 `_cli()`, `_api()`, `_requests()` を持つ |
| 出力が人間向けテキストのみ | CI には JSON, SARIF, PR コメント用 Markdown, Check annotations が要る |
| スキル層とエンジンが密結合 | GitHub Action から呼ぶには CLI 経由しかない |

---

## 2. 3 層アーキテクチャ

```
┌──────────────────────────────────────────────────────┐
│  インテグレーション層（新規）                          │
│  action/         — GitHub Actions                     │
│  hooks/          — pre-commit, post-merge             │
│  bot/            — PR コメントボット                    │
├──────────────────────────────────────────────────────┤
│  インターフェース層（既存 + 拡張）                     │
│  cli/            — CLI（argparse、既存 scripts/）      │
│  skill/          — Claude Code スキル（SKILL.md 等）   │
├──────────────────────────────────────────────────────┤
│  エンジン層（既存を再編）                              │
│  core/           — 純粋 Python ライブラリ              │
│    llm.py        — LLM 抽象層（★ 最重要の新規コード）  │
│    scanner.py    — 検出 + 検証パイプライン             │
│    retrieval.py  — ファイル取得 + 依存解析             │
│    findings.py   — JSONL 管理                         │
│    scoring.py    — スコアリング                        │
│    output/       — フォーマッター群                    │
└──────────────────────────────────────────────────────┘
```

### 原則

- **エンジン層は IO を持たない。** LLM 呼び出しは `llm.py` 経由、ファイル出力は呼び出し元が決定。
- **インターフェース層は薄い。** CLI もスキルも Action も、エンジン層の関数を呼んで出力を整形するだけ。
- **スキル層は壊さない。** 既存の Claude Code スキルはそのまま動く。

---

## 3. エンジン層の詳細

### 3.1 llm.py — LLM 抽象層（最重要）

現状 4 ファイルに重複する LLM 呼び出しを 1 箇所に集約する。

```python
"""LLM backend abstraction — single source of truth for all LLM calls."""

from __future__ import annotations
from typing import Protocol

class LLMBackend(Protocol):
    """Any LLM backend must implement this."""
    def complete(self, system: str, user: str, model: str, timeout: int) -> str: ...

class ClaudeCLI(LLMBackend):
    """claude -p (subscription, $0). Default for local use."""
    ...

class AnthropicAPI(LLMBackend):
    """Anthropic API (API key required). Default for CI."""
    ...

class OpenAIAPI(LLMBackend):
    """OpenAI API. Future option."""
    ...

def get_backend(preference: str = "auto") -> LLMBackend:
    """
    "auto": CLI available → ClaudeCLI, else AnthropicAPI
    "cli":  ClaudeCLI (fail if unavailable)
    "api":  AnthropicAPI (require ANTHROPIC_API_KEY)
    """
    ...

def call_llm(system: str, user: str, *, model: str = DEFAULT_MODEL,
             backend: str = "auto", timeout: int = 600,
             retries: int = 0) -> str:
    """Convenience function. All detection/verification calls go through here.

    retries: 失敗時のリトライ回数（exponential backoff）。
             detector は retries=2 で呼ぶ。他はデフォルト 0。
    """
    return get_backend(backend).complete(system, user, model, timeout)
```

**現在の重複箇所（これらを `call_llm()` に置換）:**

| ファイル | 関数 | バックエンド | 行 |
|---|---|---|---|
| `detector.py` | `_detect_cli()`, `_detect_anthropic_sdk()`, `_detect_requests()` | CLI + SDK + HTTP | 421-477 |
| `verifier.py` | `_verify_cli()`, `_verify_anthropic_sdk()`, `_verify_requests()` | CLI + SDK + HTTP | 69-125 |
| `deep_verifier.py` | `_call_claude_cli()` | **CLI のみ**（API フォールバックなし） | 104-120 |
| `fixgen.py` | `_generate_fix_cli()`, `_generate_fix_api()` | CLI + SDK（HTTP 直叩きなし） | 130-150+ |

置換後、各ファイルは `from llm import call_llm` して自分のプロンプトを渡すだけになる。

**置換時の注意:**

| 呼び出し元 | 注意点 | 対応 |
|---|---|---|
| `detector.py` | `retries=2` の独自リトライ | `call_llm(retries=2)` で呼ぶ |
| `verifier.py` | バックエンド不在時に全 findings を通す graceful degradation | `call_llm()` の外（verifier 側の try/except）で維持 |
| `deep_verifier.py` | `ThreadPoolExecutor` で並列呼び出し | `call_llm()` はスレッドセーフにする。並列制御は呼び出し元に残す |
| `fixgen.py` | finding 単位でループ呼び出し | そのまま。`call_llm()` 側の変更不要 |

### 3.2 scanner.py — 検出パイプライン

既存の `cmd_scan.py` から「CLI 引数の処理」を除いた純粋なパイプライン:

```python
def scan(
    repo_path: str,
    *,
    scope: str = "diff",
    depth: str = "default",
    lens: str = "default",
    since: str = "3months",
    backend: str = "auto",
    model: str = DEFAULT_MODEL,
    on_finding: Callable[[Finding], None] | None = None,
) -> ScanResult:
    """Run a full scan pipeline. Returns structured result.

    on_finding: 検出のたびに呼ばれるコールバック。
      - ローカル CLI: findings.add_finding() を渡して途中保存（クラッシュ対策）
      - CI / Action: None（最後にまとめて処理）
    """
    # 1. File selection (retrieval)
    # 2. Detection (detector → call_llm)
    # 3. Verification (verifier → call_llm)
    # 4. Scoring & triage
    # 5. 各 finding に対して on_finding() を呼ぶ（あれば）
    # 6. Return ScanResult
```

**ScanResult** は dataclass:

```python
@dataclass
class ScanResult:
    findings: list[Finding]
    scan_metadata: dict          # scope, depth, lens, duration, etc.
    high_count: int
    patterns_found: list[str]
```

**on_finding コールバックの使い分け:**

```python
# ローカル CLI（cmd_scan.py）— 途中保存でクラッシュ耐性
result = scan(repo, on_finding=lambda f: add_finding(repo, f))

# GitHub Action（entrypoint.py）— メモリ上で完結、最後にまとめて出力
result = scan(repo, on_finding=None)
for f in result.findings:
    # PR コメント生成、SARIF 出力等
```

### 3.3 output/ — フォーマッター群

| モジュール | 消費者 | 形式 |
|---|---|---|
| `output/text.py` | ターミナル（既存 output.py を移動） | ANSI テキスト |
| `output/markdown.py` | PR コメント | GitHub Flavored Markdown |
| `output/sarif.py` | GitHub Code Scanning | SARIF 2.1.0 JSON |
| `output/json.py` | CI パイプライン、API 連携 | JSON |
| `output/annotations.py` | GitHub Check Run | Check Run API 形式 |
| `output/dashboard.py` | ブラウザ（既存テンプレート） | HTML |

各フォーマッターは `ScanResult` を受け取って文字列を返す。IO しない。

---

## 4. インテグレーション層の詳細

### 4.1 GitHub Action（PR スキャン）

```yaml
# action/action.yml
name: 'delta-lint scan'
description: 'Structural contradiction scanner for PRs'
inputs:
  scope:
    description: 'Scan scope (pr, diff, smart, wide)'
    default: 'pr'
  severity-threshold:
    description: 'Minimum severity to fail (high, medium, low)'
    default: 'high'
  anthropic-api-key:
    description: 'Anthropic API key'
    required: true
  model:
    description: 'Model to use'
    default: 'claude-sonnet-4-20250514'
  comment:
    description: 'Post PR comment with results'
    default: 'true'
runs:
  using: 'composite'
  steps:
    - run: python action/entrypoint.py
      env:
        ANTHROPIC_API_KEY: ${{ inputs.anthropic-api-key }}
        DELTA_SCOPE: ${{ inputs.scope }}
        DELTA_SEVERITY_THRESHOLD: ${{ inputs.severity-threshold }}
        DELTA_MODEL: ${{ inputs.model }}
        DELTA_COMMENT: ${{ inputs.comment }}
```

**entrypoint.py の処理フロー:**

```
1. GITHUB_EVENT_PATH から PR 情報取得
2. git diff origin/$BASE...HEAD で変更ファイル取得
3. scanner.scan(scope="pr", backend="api") 実行
4. ScanResult → output/markdown.py で PR コメント生成
5. gh pr comment で投稿
6. ScanResult → output/annotations.py で Check Run 作成
7. high_count > 0 なら exit 1（マージブロック）
```

### 4.2 利用者が書く workflow 例

```yaml
# .github/workflows/delta-scan.yml
name: delta-lint
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  scan:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write      # PR コメント投稿用
      checks: write             # Check annotations 用
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # git diff に必要
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - uses: your-org/delta-lint-action@v1
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          severity-threshold: high
```

### 4.3 PR コメントのフォーマット

```markdown
## delta-lint scan results

🔍 **3 findings** detected in this PR (1 high, 2 medium)

### 🔴 High: Cache invalidation assumes single-node deployment
- **Files**: `src/cache.py` ↔ `src/config.py`
- **Pattern**: implicit-assumption
- **Impact**: Config allows multi-node but cache uses in-memory dict

### 🟡 Medium: ...

---
<details><summary>Scan details</summary>

- Scope: pr (4 files changed)
- Model: claude-sonnet-4-20250514
- Duration: 12.3s
- [Full dashboard](link-to-artifact)
</details>
```

### 4.4 Scheduled フルスキャン

```yaml
on:
  schedule:
    - cron: '0 3 * * 1'   # 毎週月曜 3:00 UTC

jobs:
  full-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: your-org/delta-lint-action@v1
        with:
          scope: wide
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: delta-lint-dashboard
          path: .delta-lint/findings/dashboard.html
```

### 4.5 リリースゲート

```yaml
on:
  push:
    tags: ['v*']

jobs:
  release-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: your-org/delta-lint-action@v1
        with:
          scope: wide
          lens: security
          severity-threshold: medium   # medium 以上で block
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

---

## 5. ディレクトリ構成（移行後）

```
plugins/delta-lint/
├── scripts/                    ← 既存（CLI + エンジン同居）
│   ├── cli.py                     CLI エントリポイント
│   ├── cmd_init.py                init コマンド
│   ├── cmd_scan.py                scan コマンド（scanner.py を呼ぶ）
│   ├── llm.py                  ← NEW: LLM 抽象層
│   ├── scanner.py              ← NEW: 検出パイプライン（純粋関数）
│   ├── detector.py                検出（call_llm に置換）
│   ├── verifier.py                検証（call_llm に置換）
│   ├── deep_verifier.py           深層検証（call_llm に置換）
│   ├── fixgen.py                  修正生成（call_llm に置換）
│   ├── retrieval.py               ファイル取得
│   ├── findings.py                JSONL 管理
│   ├── scoring.py                 スコアリング
│   ├── info_theory.py             情報理論スコアリング
│   ├── output.py                  テキスト出力（既存）
│   ├── output_markdown.py      ← NEW: PR コメント用 Markdown
│   ├── output_sarif.py         ← NEW: SARIF 出力
│   ├── output_json.py          ← NEW: JSON 出力
│   └── ...
│
├── action/                     ← NEW: GitHub Action
│   ├── action.yml                 Action 定義
│   ├── entrypoint.py              薄いラッパー
│   └── templates/
│       └── pr_comment.md          PR コメントテンプレート
│
├── skills/                     ← 既存（変更なし）
│   ├── delta-scan/
│   │   ├── SKILL.md
│   │   └── references/
│   └── delta-review/
│       ├── SKILL.md
│       └── references/
│
└── docs/
    ├── architecture-commands.md   コマンド責務（SSoT）
    └── architecture-integration.md 本文書（SSoT）
```

### なぜ `core/` パッケージにしないか

`scripts/` 内のモジュールは相互に `from detector import detect` のように直接 import している。
`core/` サブパッケージに移動すると全ファイルの import パスが変わり、破壊的変更になる。

**方針: `scripts/` 内にフラットに `llm.py`, `scanner.py` 等を追加する。**
パッケージ分割は将来 PyPI 配布時に検討する。

---

## 6. Qodo 等との違い・共存

| | Qodo / CodeRabbit | delta-lint |
|---|---|---|
| **視点** | 変更行のコード品質 | モジュール間の構造矛盾 |
| **検出対象** | バグ、スタイル、セキュリティ | デグレ、暗黙の契約違反、仕様の食い違い |
| **スコープ** | diff のみ | diff + 依存先 + 履歴 |
| **比喩** | 木を見る | 森を見る |
| **競合** | しない | **補完関係** |

**共存の仕方:** Qodo は inline コメント（行レベル）、delta-lint は PR-level サマリー（ファイル間の関係）。両方を同じ PR で走らせる。

---

## 7. 段階的マイグレーション計画

### Phase 1: llm.py 抽出（最小工数・最大効果）

**やること:**
1. `scripts/llm.py` を新規作成（`call_llm()` + バックエンド実装）
   - `ClaudeCLI`: `subprocess.run(["claude", "-p"], ...)` — ローカル用（$0）
   - `AnthropicAPI`: `anthropic.Anthropic().messages.create()` — CI 用
   - `HTTPFallback`: `requests.post()` — SDK なし環境用
   - `retries` パラメータ（exponential backoff）
   - スレッドセーフ（deep_verifier の ThreadPoolExecutor 対応）
2. `detector.py`: `_detect_cli()`, `_detect_anthropic_sdk()`, `_detect_requests()` を `call_llm(retries=2)` に置換
3. `verifier.py`: `_verify_cli()`, `_verify_anthropic_sdk()`, `_verify_requests()` を `call_llm()` に置換。graceful degradation（バックエンド不在時に全 findings を通す）は verifier 側の try/except で維持
4. `deep_verifier.py`: `_call_claude_cli()` を `call_llm()` に置換。ThreadPoolExecutor はそのまま呼び出し元に残す
5. `fixgen.py`: `_generate_fix_cli()`, `_generate_fix_api()` を `call_llm()` に置換

**効果:** LLM バックエンド切替が 1 箇所に集約。CI 対応の土台完成。
**リスク:** 低。内部リファクタのみ、外部インターフェース不変。
**見積もり規模:** 約 200 行の新規コード + 4 ファイルから約 160 行の重複削除。

### Phase 2: scanner.py + output フォーマッター

**やること:**
1. `cmd_scan.py` から検出パイプラインの純粋ロジックを `scanner.py` に抽出
2. `ScanResult` dataclass 定義
3. `on_finding` コールバック対応（ローカル: 即時保存、CI: None）
4. `output_json.py` 作成（`--output json` フラグ対応）
5. `output_markdown.py` 作成（PR コメント用）
6. `cmd_scan.py` を `scanner.scan()` の薄いラッパーに書き換え

**効果:** CLI 以外から検出パイプラインを呼べるようになる。
**リスク:** 中。cmd_scan.py の責務分割が必要。現在の cmd_scan.py は検出→検証→findings記録→トリアージ→ダッシュボード再生成が一気通貫で、途中に findings 保存が挟まっている。`on_finding` コールバックで両立する。

### Phase 3: GitHub Action

**やること:**
1. `action/action.yml` + `action/entrypoint.py` 作成
2. PR コメント投稿ロジック（`gh pr comment`）
3. Check Run annotations 連携
4. severity threshold によるマージブロック

**効果:** PR ワークフローが完成。企業導入可能に。
**前提:** Phase 1, 2 が完了していること。

### Phase 4: 拡張トリガー

**やること:**
1. SARIF 出力（GitHub Code Scanning 連携）
2. Scheduled フルスキャン workflow
3. リリースゲート workflow
4. PR コメントボット（`/delta-scan` コマンド）

**効果:** フルの CI/CD 統合。

---

## 8. コスト管理

CI で API を使う場合のコスト:

| スキャン種類 | LLM 呼び出し回数 | 推定コスト/回 | 頻度 | 月額目安 |
|---|---|---|---|---|
| PR スキャン | 2（検出+検証） | ~$0.05 | 100 PR/月 | ~$5 |
| フルスキャン | 10-30（バッチ） | ~$0.50 | 週1回 | ~$2 |
| ストレステスト | 50-100 | ~$2.00 | 月1回 | ~$2 |
| リリースゲート | 10-30 | ~$0.50 | 月2回 | ~$1 |

**合計: 月 $10 程度（100 PR/月の中規模チーム）。**

コスト削減策:
- `--model haiku` で軽量モデルを使う（精度は下がる）
- キャッシュ: 同一ファイルペアの再スキャンを skip
- `--severity high` で high のみ検出（検証 LLM 呼び出しを減らす）

---

## 9. 矛盾チェックリスト

本設計書の変更時に確認すること:

- [ ] `llm.py` が全 LLM 呼び出しの唯一のエントリポイントか（detector/verifier/deep_verifier/fixgen に `subprocess.run(["claude"` が残っていないか）
- [ ] `call_llm()` がスレッドセーフか（deep_verifier の ThreadPoolExecutor から呼ばれる）
- [ ] `scanner.py` が直接 IO しないか（JSONL 書き込みは `on_finding` コールバック経由のみ）
- [ ] `cmd_scan.py` が `scanner.scan(on_finding=add_finding)` で途中保存しているか
- [ ] `action/entrypoint.py` が `scanner.scan(on_finding=None)` で呼んでいるか（CLI 経由でないか）
- [ ] 出力フォーマッターが `ScanResult` のみを入力としているか
- [ ] `architecture-commands.md` のコマンド責務と矛盾していないか
