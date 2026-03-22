# DeltaLint

**構造矛盾検出器** — コードモジュール間の暗黙の前提が破れている箇所を LLM で検出します。

スタイルやシンプルなバグではなく、**設計レベルの不整合**（あるモジュールの前提が別のモジュールの振る舞いと矛盾している箇所）を見つけます。

## 1. システム概要

### 核心テーゼ

DeltaLint は **コードモジュール間の構造矛盾（structural contradiction）** を LLM で検出するツール。従来の linter がスタイルや型を見るのに対し、**モジュール間の暗黙の契約違反** を見つける。

「ファイル A の前提がファイル B の実装と矛盾している箇所」— 開発者が合理的にスコープを絞った結果、気づかずに残る設計レベルの不整合を発見する。

### 設計を支配するデータ

63リポジトリ（17K〜133K stars）で検証。

- **検出率**: 62/63（98.4%）で構造矛盾を検出
- **真陽性率**: 報告101件中92件が真陽性（91%）
- **マージ率**: 提出29 PR中28件がマージ（96.6%）
- **非対称コスト**: 偽陰性（見逃し）のコストは偽陽性（誤報）の約29倍

この非対称コスト構造が、2段階検証アーキテクチャの根拠。

## セットアップ

### 必須

- **Python 3.11+**
- **git** — diff ベースのスキャンに使用

### 自動セットアップ（手動インストール不要）

DeltaLint は初回スキャン時に不足している依存を**自動で検出・インストール**します。
手動でのセットアップは不要です。何もインストールされていない状態からでも、DeltaLint が自分で環境を整えます。

| 依存 | 自動解決方法 | 用途 |
|------|-------------|------|
| **claude CLI** | `npm install -g @anthropic-ai/claude-code` | LLM バックエンド（$0） |
| **gh CLI** | `brew install gh` / `conda install gh` → 自動で `gh auth login --web` | Issue/PR 自動作成 |
| **anthropic SDK** | `pip install anthropic` | API バックエンド（フォールバック） |
| **PyYAML** | `pip install pyyaml` | 設定ファイル読み込み |

いずれかのインストールが失敗しても、代替手段に自動フォールバックして動作を継続します：

```
claude CLI がない → Anthropic API にフォールバック
gh CLI がない    → Issue/PR 作成をスキップ（スキャン結果は正常に返す）
API キーもない   → dry-run モードで動作
```

## 使い方

### Claude Code から（推奨）

```
> delta-scan                              # 変更ファイルのデグレチェック
> delta-scan --scope smart                # git 履歴ベースでファイル選択
> delta-scan --depth deep                 # 依存チェーンを辿る深層スキャン
> delta-scan --lens security              # セキュリティ特化
> delta-scan --scope all --lens stress    # 全ファイル × ストレステスト
> delta-view                              # ダッシュボードをブラウザで表示
> delta init                              # リポジトリの初期化（構造分析）
```

### CLI から直接

```bash
cd plugins/delta-lint/scripts

# 変更ファイルをスキャン（デフォルト: diff × 直接依存 × 構造矛盾検査）
python cli.py scan --repo /path/to/repo

# 3軸モデルで細かく指定
python cli.py scan --scope smart --depth deep --lens security

# プロファイルを使う（プリセット設定を一括適用）
python cli.py scan -p deep             # 徹底スキャン
python cli.py scan -p light            # CI向け高速チェック
python cli.py scan -p security         # セキュリティ特化

# 特定ファイルをスキャン
python cli.py scan --files src/handler.ts src/router.ts

# 全重要度を表示 + 日本語出力
python cli.py scan --severity low --lang ja

# 意味検索を有効化（暗黙の仮定を抽出して関連ファイルを拡張）
python cli.py scan --semantic

# ドキュメントを仕様契約として検査（コード × ドキュメント矛盾検出）
python cli.py scan --docs README.md ARCHITECTURE.md

# ウォッチモード（ファイル変更を監視して自動再スキャン）
python cli.py scan --watch

# 検出 + 自動修正コード生成
python cli.py scan --autofix

# API バックエンドを使用
python cli.py scan --backend api
```

## 2. データフロー

### メインパイプライン

```
Git diff → ファイル選択
    │
    ▼
[Phase 0] retrieval.py — コンテキスト構築
    │  import 解析 → 1-hop 依存取得
    │  信頼度による階層化: direct(0.95) / re-export(0.85) / type(0.50)
    │  [optional] --semantic: LLM で暗黙仮定を抽出 → grep で関連ファイル発見
    │  [optional] --docs: ドキュメントを仕様契約として追加
    │
    ▼
[Phase 1] detector.py — LLM 検出（高 recall）
    │  prompts/detect.md をシステムプロンプトとして送信
    │  10パターン（①-⑩）に基づく検出
    │  方針: 「30%の確信度でも報告。全部報告し、後で絞る」
    │
    ▼
[Phase 2] verifier.py — LLM 検証（高 precision）
    │  prompts/verify.md で各 finding を再検証
    │  本番で到達可能か？意図的な分岐か？を判定
    │  --no-verify でスキップ可能
    │
    ▼
output.py — フィルタリング
    │  severity フィルタ + suppress.yml マッチ
    │  → shown / filtered / suppressed / expired に分類
    │
    ▼
findings.py — 永続化
    │  JSONL append-only イベントログに追記
    │  同一IDの後発エントリが最新状態
    │
    ▼
[optional] fixgen.py + debt_loop.py — 自動修正
       branch 作成 → fix 生成 → regression check → commit → PR
```

### ステージ別の入出力

| ステージ | モジュール | 入力 | 出力 |
|---------|-----------|------|------|
| ファイル選択 | cmd_scan | Git diff / --since / --scope | `list[str]`（相対パス） |
| コンテキスト構築 | retrieval | ファイルリスト + repo_path | ModuleContext（target/dep/doc + コンテンツ） |
| LLM検出 | detector | ModuleContext + system prompt | `list[dict]`（raw findings） |
| LLM検証 | verifier | raw findings + ModuleContext | confirmed / rejected の2リスト |
| フィルタリング | output + suppress | confirmed + suppress + severity閾値 | FilterResult |
| 永続化 | findings | shown findings | `.delta-lint/findings/*.jsonl` |
| スコアリング | scoring + info_theory | findings + git churn + fan_out | roi_score, info_score 付与 |
| 修正ループ | debt_loop | 優先度ソート済み findings | GitHub PR |

### バッチ実行モデル

コンテキストが40,000文字を超える場合、自分自身をサブプロセスとして再帰呼び出ししてバッチ分割。`ThreadPoolExecutor` で並列実行し、結果は共有 JSONL で集約。プロセス分離により LLM タイムアウトが1バッチに閉じる。

## 3. 主要な設計原則

### 3.1 非対称コスト最適化

- **Phase 1 (detector)**: 「30%の確信でも報告せよ」— recall 最大化
- **Phase 2 (verifier)**: 「疑わしければ reject」— precision 回復
- **エスカレーションプロトコル**: 0件で終わりそうなとき3段階エスカレーション（sibling拡大 → 横断契約チェック → 最低確信候補の報告）

### 3.2 ゼロコスト LLM 呼び出し

`claude -p`（サブスク CLI、$0）がデフォルト。API は明示フォールバック。ADR-003 に「$100以上を無駄にした実績」が教訓として記録。

### 3.3 Append-Only イベントログ

JSONL の append-only 設計で3つの要求を同時に満たす:

1. **LLM 非決定性への耐性**（一度見つけたものは消えない）
2. **ストック型蓄積**（結果がブレても蓄積される）
3. **Git フレンドリー**（conflict しにくい）

### 3.4 構造ベース ID

`SHA256(repo:sorted(file_a, file_b):pattern)` で ID 生成。LLM が同じ矛盾を別の表現で報告しても同一IDになる。ロジック変更禁止。

### 3.5 4層設定マージ

```
CLI flags > profile > config.json > defaults
```

## 4. 検出パターン（10種）

### 構造矛盾（①-⑥）— category="contradiction"、2箇所必須

| # | パターン名 | 検出対象 |
|---|-----------|---------|
| ① | Asymmetric Defaults | 入出力パスで同じ値の扱いが異なる（null vs undefined、型強制等） |
| ② | Semantic Mismatch | 共有名（status, type, code）がモジュール間で異なる意味を持つ |
| ③ | External Spec Divergence | RFC・言語仕様・ドキュメント記載とコードが乖離 |
| ④ | Guard Non-Propagation | 一方のパスにはバリデーションがあるが並行パスにはない |
| ⑤ | Paired-Setting Override | 独立に見える2つの設定が暗黙に干渉 |
| ⑥ | Lifecycle Ordering | 特定パスで実行順序の前提が崩れる |

### 技術的負債（⑦-⑩）— category="debt"

| # | パターン名 | 検出対象 |
|---|-----------|---------|
| ⑦ | Dead Code / Unreachable Path | 未使用のエクスポート、永久OFFのフラグ等（1箇所可） |
| ⑧ | Duplication Drift | コピペコードの片方だけ更新済み |
| ⑨ | Interface Mismatch | 呼び出し側と定義側で引数の数・順序・意味が不一致 |
| ⑩ | Missing Abstraction | 同一ロジックが3箇所以上に散在（1箇所可） |

### メカニズム分類（なぜ矛盾が残るか）

- **copy_divergence (~60%)**: A→B コピー時の不完全な適応
- **one_sided_evolution (~25%)**: A を改善したが B はスコープ外で放置
- **independent_collision (~15%)**: A と B が独立に書かれ、暗黙の契約に気づかない

## 5. 3軸スキャンモデル

**scope（広さ）× depth（深さ）× lens（質）= 3×2×3 = 18通り**

| 軸 | 選択肢 | 説明 |
|----|--------|------|
| **scope** | `diff`（デフォルト） | 変更ファイル + 1-hop 依存 |
| | `smart` | git 履歴ベースのファイル選択（diff 不要） |
| | `all` | 全ソースファイル（バッチ処理） |
| **depth** | デフォルト | 直接の依存のみ |
| | `deep` | 依存チェーンを3ホップまで辿る（各ホップで信頼度 ×0.85 減衰） |
| **lens** | `default` | 構造矛盾検査（10パターン） |
| | `stress` | 仮想改修ストレステスト（地雷マップ生成） |
| | `security` | セキュリティ特化（認証・権限・入力検証） |

### 3つのスキャンパス

| パス | エントリ | パイプライン |
|------|---------|-------------|
| 通常スキャン (cmd_scan) | diff → 1-hop依存 → LLM検出 → LLM検証 | 日常の変更チェック |
| 深層スキャン (cmd_scan_deep) | 正規表現抽出 → 契約グラフ → LLM検証 | WordPress等フック系の構造解析 |
| ストレステスト (cmd_scan_full) | 構造分析 → 仮想改修生成 → N回スキャン → ヒートマップ | 地雷マップ作成 |

## 6. モジュール別アーキテクチャ

### 6.1 retrieval.py（1651行）— コンテキスト構築

**責務**: git diff → import 解析 → 依存取得 → LLM に渡すコンテキスト構築

**3+1 Tier 依存解決**:

| Tier | 信頼度 | 対象 |
|------|--------|------|
| Tier 1 | 0.95 | 同ディレクトリの明示的 import |
| Sibling | 0.90 | 学習済みの兄弟ペア（sibling_map.yml） |
| Tier 2 | 0.85 | 相対 import（ディレクトリ横断） |
| Tier 3 | 0.50 | プロジェクトスコープの名前マッチ |

MIN_CONFIDENCE(0.50) 未満は除外。

- **スマート切り詰め**: head cut ではなく、構造的アウトライン（import + 関数シグネチャ + 先頭/末尾）を保持。module-level context で **Recall 45% → 89%**（実験データ）
- **14言語の import 抽出**: JS/TS, Python, Go, Rust, PHP, Ruby, Java, Kotlin, C#, C/C++, Swift
- **マルチホップ**: `--depth deep` で max_hops=3。各ホップで信頼度 ×0.85 減衰。予算超過時はコンテキスト上限を2倍に自動拡張
- **コンテキスト上限**: MAX_CONTEXT_CHARS=40K, MAX_FILE_CHARS=15K, MAX_DEPS_PER_FILE=5

**データ構造**:

```
ModuleContext:
  target_files: list[FileContext]   # 変更ファイル（CHANGED）
  dep_files: list[FileContext]      # 依存ファイル（DEPENDENCY, confidence=N%）
  doc_files: list[FileContext]      # ドキュメント（specification contract）
```

### 6.2 semantic.py（313行）— 意味拡張

import 解析では見つからない暗黙の依存を LLM + grep で発見。

1. diff を LLM に渡し、暗黙の仮定（implicit assumptions）を抽出
2. 各仮定に付随する grep パターンでコードベースを検索
3. 発見したファイルを ModuleContext に追加（MAX_SEMANTIC_DEPS=8 件上限）

`--semantic` フラグで有効化。

### 6.3 detector.py（525行）— Phase 1: LLM 検出

**3段バックエンドフォールバック**: `claude -p`(CLI) → Anthropic SDK → 生HTTP

**プロンプト設計（prompts/detect.md）**:

- 10パターン定義 + "Scope-Blind Constraint Check" 戦略
- エスカレーションプロトコル（0件時の3段階エスカレーション）
- 経験的事前分布の埋め込み（98.4%のリポで発見される旨）
- チームは `.delta-lint/detect.md` で完全上書き可能
- `policy.prompt_append` で追加指示をプロンプト末尾に注入

**パース耐性（4段階）**:
1. markdown コードブロックから JSON 抽出
2. 直接 JSON parse
3. `[...]` ブラケット範囲抽出
4. raw text を `parse_error: true` で返す

**リトライ**: 最大2回、指数バックオフ（1s, 2s）

### 6.4 verifier.py（299行）— Phase 2: LLM 検証

**5つの検証基準**（すべて満たす場合のみ CONFIRMED）:

1. 両箇所のコードが実在する
2. クロスモジュール衝突である
3. 本番環境で到達可能
4. 意図的な設計ではない
5. 記述が正確

**出力**: verdict(confirmed/rejected), confidence(0-1), certainty(definite/probable/uncertain), reproducibility

全 findings を1回の LLM 呼び出しでバッチ検証（コスト効率優先）。confidence < 0.7 で reject。バックエンド不可時は全件パススルー（graceful degradation）。

### 6.5 findings.py（1896行）— JSONL 管理 + ダッシュボード

**設計思想**: LLM は非決定的 → append-only で蓄積（ストック型）

**STATUS_META（Single Source of Truth）**:

| ステータス | 意味 | closed | debt_weight |
|-----------|------|--------|-------------|
| found | 未トリアージ | No | 1.0 |
| suspicious | 要調査 | No | 0.9 |
| confirmed | 確定バグ | No | 1.0 |
| submitted | PR提出済み | No | 0.8 |
| merged | 修正済み | Yes | 0.0 |
| rejected | 却下 | Yes | 0.5 |
| wontfix | 対応不要 | Yes | 0.0 |
| duplicate | 重複 | Yes | 0.0 |
| false_positive | 偽陽性 | Yes | 0.0 |

**重複排除（3層）**:

1. **完全一致**: ファイル×パターン×タイトルからハッシュID → 同一IDスキップ
2. **言い換え**: タイトルの trigram 類似度 55%以上 → 同一判定
3. **別パターン同一実体**: コード中エンティティ抽出、60%以上重複 → 統合

**ダッシュボード**: Python `string.Template`（Jinja2 ではない）。テンプレート解決は profile > repo-local > built-in。

### 6.6 scoring.py（458行）+ info_theory.py（302行）— スコアリング

4軸の独立したスコアで優先度を多角的に評価。

#### A. 負債スコア（debt_score）— 0〜1000

```
debt_score = severity_weight × pattern_weight × status_multiplier × 1000
```

「この種のバグがどれくらい深刻か」の静的評価。

#### B. 解消価値（ROI / context_score）— 0〜数千

```
roi = severity × churn_weight × fan_out_weight / fix_cost × ROI_SCALE(100)
```

- **churn_weight**: git log からの月次変更頻度（月3回以上で max, 0.5〜10.0）
- **fan_out_weight**: git grep からの被参照ファイル数（5ファイル以上で max, 1.0〜10.0）
- **fix_cost**: パターン別修正工数（④ガード追加=1.0, ⑩共通化=5.0）
- churn/fan_out は `log₂(1+x)` で外れ値を抑制
- **放置コスト加速**: `age_multiplier = 1 + log₂(1 + days/30) × churn_ratio`
- **不確実ディスカウント**: certainty=uncertain は roi_score × 0.3

#### C. 情報量スコア（info_score）— 0〜数千

```
info_score = (1/√n) × log₂(1+m) × INFO_SCALE(100)
```

- n = 同パターンの finding 数（初出ほど高い）
- m = 同ファイルのオープン finding 数（ホットスポットほど高い）
- 現状はヒューリスティック。将来的に自己情報量 `-log₂ P(finding exists)` の実装を検討

#### D. Chao1 カバレッジ推定

生態学の種の豊かさ推定を応用。`scan_history.jsonl` の singleton/doubleton から未発見 finding 数を推定。95% CI は対数正規近似。

**3層マージ**: `defaults(scoring.py) ← config.json ← profile policy`。指定キーだけ上書き。

### 6.7 suppress.py（305行）— サプレス管理

LLM 出力に依存しないハッシュ設計:

- **finding_hash**: ソート済みファイル + 行番号5行バケット丸めから生成。パターンIDやLLMテキストは使わない
- **code_hash**: ±10行のコードハッシュ → コード変更時に自動失効
- **SuppressEntry**: 理由(why)、理由タイプ(domain/technical/preference)、承認者の記録

### 6.8 fixgen.py（262行）+ debt_loop.py（735行）— 自動修正

**fixgen.py**: LLM にソースコード + 矛盾情報を渡し、最小限の修正パッチ（old_code → new_code）を生成。

**debt_loop.py**: 負債解消の自動ループ

- findings を優先度順（info_score + roi_score + severity_bonus）にソート
- **1 finding = 1 branch = 1 PR** で処理
- fix 生成 → ローカル適用 → デグレチェック（`delta-scan --scope pr`）→ commit → push → PR
- 新たな矛盾が生まれたらブロック
- push 権限なしで `gh repo fork` を自動実行
- auto-stash: 未コミット変更の退避と復元

### 6.9 stress_test.py — ストレステスト + 地雷マップ

1. **Step 0**: 構造分析（LLM でリポジトリアーキテクチャを理解）
2. **Step 0.5**: 既存バグスキャン（ホットスポットクラスタの現在の矛盾検出）
3. **Step 1**: 仮想改修生成（LLM で「こう変更したら？」のシナリオ作成）
4. **Step 2**: 各仮想改修をスキャン → per-file ヒートマップとして集約

### 6.10 補助モジュール

| モジュール | 責務 |
|-----------|------|
| cli_utils.py (771行) | 環境チェック、config/profile 読込、適応的時間窓、ベースライン管理 |
| cmd_init.py (570行) | リポジトリ初期化（構造分析 + sibling_map 生成） |
| cache.py (106行) | SHA256(files+content) でスキャン結果キャッシュ。同一コンテキストはLLMスキップ |
| git_enrichment.py | git churn(6ヶ月) / fan_out 計算。スキャン時に finding へ埋め込み |
| sibling.py (433行) | finding / git共変更から兄弟ペアを学習 → 次回のコンテキスト構築に反映 |
| persona_translator.py (230行) | engineer/pm/qa 向けの出力変換（LLM翻訳 + テンプレートフォールバック） |
| contract_graph.py (420行) | WordPress等フック経由の暗黙依存検出（実験的） |
| surface_extractor.py (594行) | 正規表現ベースのhook/actionパターン抽出 |

## 7. データモデル

### Finding（JSONL 1行）

```json
{
    "id": "dl-a1b2c3d4",
    "repo": "my-app",
    "file": "src/handler.ts",
    "file_b": "src/validator.ts",
    "severity": "high",
    "pattern": "①",
    "title": "...",
    "description": "...",
    "status": "found",
    "category": "contradiction",
    "taxonomies": {"certainty": "definite", "reproducibility": "always"},
    "mechanism": "one_sided_evolution",
    "churn_6m": 15,
    "fan_out": 8,
    "contradiction": "...",
    "impact": "...",
    "internal_evidence": "..."
}
```

### ステータスライフサイクル

```
found → confirmed → submitted → merged (debt=0)
                              → rejected (debt=0.5)
      → false_positive (debt=0)
      → wontfix (debt=0)
      → duplicate (debt=0)
```

### ストレージレイアウト

```
.delta-lint/
├── config.json              # リポ固有設定（スコアリング重み含む）
├── suppress.yml             # サプレス済み findings（理由+期限付き）
├── sibling_map.yml          # 暗黙契約ペアの学習結果
├── scan_history.jsonl       # スキャン時系列（Chao1推定用）
├── cache/                   # context_hash ベースのキャッシュ
├── findings/
│   ├── {repo-name}.jsonl    # append-only イベントログ
│   ├── dashboard.html       # 生成済みダッシュボード
│   └── _index.md            # サマリー
├── profiles/                # カスタムプロファイル
└── landmine_map.json        # リスクヒートマップ
```

## 8. 設定システム

### 優先順位チェーン

```
CLI flags > profile (.delta-lint/profiles/<name>.yml) > config.json > defaults (scoring.py)
```

### Profile の config vs policy

- **config**: 「何を使うか」（モデル、閾値、容量制限）→ argparse フラグに対応
- **policy**: 「どう動くか」（プロンプト追加指示、無効パターン、スコアリング重み）→ 検出ロジック制御

```yaml
name: my-profile
config:
  severity: medium
  model: claude-sonnet-4-20250514
  max_context_chars: 80000
policy:
  prompt_append: "追加指示"
  disabled_patterns: ["⑦", "⑩"]
  scoring_weights: { ... }
```

### ビルトインプロファイル

| 名前 | 用途 | severity | semantic | 無効パターン |
|------|------|----------|----------|-------------|
| `deep` | 徹底スキャン（見逃しゼロ） | low | ON | なし |
| `light` | CI / PR レビュー向け高速チェック | high | OFF | ⑦⑧⑨⑩ |
| `security` | セキュリティ構造矛盾の重点検出 | low | OFF | ⑦⑩ |

### config.json（基本設定）

リポジトリルートに `.delta-lint/config.json` を配置することで、デフォルト動作をカスタマイズできます。
全フィールド省略可。**CLI フラグが常に config より優先**されます。

```json
{
  "lang": "ja",
  "backend": "cli",
  "severity": "medium",
  "model": "claude-sonnet-4-20250514",
  "verbose": false,
  "semantic": false,
  "persona": "engineer",
  "autofix": false,
  "scoring": {
    "severity_weight": { "high": 1.0, "medium": 0.6, "low": 0.3 },
    "pattern_weight": { "①": 1.0, "④": 1.0, "⑦": 0.3 },
    "status_multiplier": { "found": 1.0, "merged": 0.0 },
    "fix_cost": { "④": 0.8, "⑩": 2.0 }
  }
}
```

## 9. 耐障害性設計

| 壊れるもの | 対処 |
|-----------|------|
| LLM API（タイムアウト、レート制限） | 指数バックオフリトライ(1s→2s)。CLI → SDK → HTTP の3層フォールバック |
| LLM 出力フォーマット（JSON が markdown で囲まれる等） | 4段階パーサーで順次試行。部分結果でも抽出 |
| git 履歴（shallow clone で1件のみ） | ファイルサイズから変更頻度を推定するフォールバック |
| JSONL の途中行の破損 | 壊れた行スキップ。append-only 設計で部分破損に耐える |
| 巨大リポジトリ（36,000ファイル） | 10件ごとに途中保存、制限時間40分で中間結果返却 |
| git なし（zip 展開コード等） | ファイルシステム走査にフォールバック。churn なしでも静的スコアで動作 |
| Phase 2 バックエンド不可 | 全件パススルー（graceful degradation） |

### LLM 非決定性への多層対処

| 対策 | 実装箇所 |
|------|---------|
| 構造ベース ID（LLMテキスト非依存） | findings.py `generate_id()` |
| Semantic dedup（trigram + entity overlap） | findings.py |
| 行番号の丸め（5行バケット） | suppress.py |
| temperature=0 | detector.py, verifier.py |
| 多段パースフォールバック | detector.py `_parse_response()` |

## CLI コマンド一覧

### scan

```bash
python cli.py scan [OPTIONS]
```

| フラグ | デフォルト | 説明 |
|--------|-----------|------|
| `--repo` | `.` | 対象リポジトリ |
| `--scope` | `diff` | 広さ: `diff` / `smart` / `all` |
| `--depth` | 直接依存 | 深さ: 指定なし / `deep` |
| `--lens` | `default` | 質: `default` / `stress` / `security` |
| `--profile` / `-p` | なし | スキャンプロファイル |
| `--files` | (git diff) | スキャン対象ファイルを直接指定 |
| `--docs` | なし | ドキュメントを仕様契約として含む |
| `--diff-target` | `HEAD` | 差分比較先の git ref |
| `--severity` | `high` | 表示する最小重要度 |
| `--format` | `markdown` | 出力形式（`markdown` / `json`） |
| `--lang` | `en` | 出力言語（`en` / `ja`） |
| `--for` | config.json 優先 | ペルソナ（`engineer` / `pm` / `qa`） |
| `--backend` | `cli` | LLM バックエンド（`cli` / `api`） |
| `--model` | `claude-sonnet-4-20250514` | LLM モデル |
| `--semantic` | off | 意味検索を有効化 |
| `--watch` | off | ファイル変更監視モード |
| `--watch-interval` | `3.0` | ウォッチモードのポーリング間隔（秒） |
| `--autofix` | off | 修正コード自動生成 |
| `--no-verify` | off | Phase 2 検証をスキップ |
| `--no-cache` | off | キャッシュを使わず常に LLM 呼び出し |
| `--no-learn` | off | sibling_map 自動更新をスキップ |
| `--deep-workers` | `4` | 深層スキャンの並列ワーカー数 |
| `--baseline` | なし | ベースラインとの差分のみ報告 |
| `--baseline-save` | off | 現在の結果をベースラインとして保存 |
| `--diff-only` | off | diff 内ファイルに関連する finding のみ表示 |
| `--dry-run` | off | LLM を呼ばずコンテキストのみ表示 |

### findings

```bash
python cli.py findings <subcommand> [OPTIONS]
```

| サブコマンド | 説明 |
|-------------|------|
| `add` | finding を手動記録 |
| `list` | finding 一覧（`--status`, `--type`, `--repo-name`, `--format json` でフィルタ可） |
| `update <id> <status>` | ステータス更新（例: `update abc123 merged`） |
| `search <query>` | キーワード検索 |
| `stats` | サマリー統計（件数、severity 別、debt_score 合計） |
| `enrich` | git churn / fan-out データで findings をエンリッチ |
| `verify-top` | 優先度上位 1/3 の findings を LLM で再検証 |
| `index` | `_index.md` を再生成 |
| `dashboard` | HTML ダッシュボードを生成してブラウザで表示 |

### suppress

```bash
python cli.py suppress [OPTIONS]
```

| フラグ | 説明 |
|--------|------|
| `<N>` | 直近スキャンの N 番目の finding を抑制 |
| `--list` | 抑制リスト表示 |
| `--check` | 期限切れの抑制をチェック |
| `--why` | 抑制理由（非対話モード用） |
| `--why-type` | 理由タイプ: `domain`/`d`, `technical`/`t`, `preference`/`p` |
| `--approved-by` | 承認者名（未指定 = 未承認 = 自己判断） |

### その他

| コマンド | 説明 |
|---------|------|
| `init` | リポジトリの初期化（構造分析 + sibling_map 生成） |
| `view` | ダッシュボードをブラウザで表示（`--regenerate` で再生成） |
| `config init` | デフォルト設定を `.delta-lint/config.json` に書き出し |
| `config show` | 現在の設定を表示 |
| `fix` | finding や GitHub Issue から修正 → デグレチェック → commit → PR |

## Autofix / Fix

### scan --autofix

スキャン結果に対して最小限の修正コードを LLM が生成し、ローカルに適用します。

```bash
python cli.py scan --repo /path/to/repo --autofix
```

### fix (delta-fix)

findings や GitHub Issue から自動修正を生成。1 finding = 1 branch = 1 PR。
修正後にデグレチェック（`delta-scan --scope pr`）を自動実行し、新たな矛盾が生まれたらブロックします。

```bash
# 優先度上位3件を自動修正→PR
python cli.py fix --repo /path/to/repo -n 3

# 特定の finding のみ
python cli.py fix --ids dl-a1b2c3d4,dl-e5f6g7h8

# GitHub Issue から修正PR作成
python cli.py fix --repo /path/to/repo --issue 42

# ドライラン（修正生成のみ、commit/push しない）
python cli.py fix --repo /path/to/repo --dry-run -v
```

## 10. 統合ポイント

| 統合先 | 方法 | 用途 |
|--------|------|------|
| **Claude Code** | 3 Skill（delta-scan, delta-review, delta-fix） | 対話的スキャン・レビュー・修正 |
| **GitHub Actions** | action/entrypoint.py | PR 自動レビュー（review/suggest/autofix の3モード） |
| **CLI** | scripts/cli.py | ローカル開発での直接実行 |
| **CI/CD** | `cli.py scan -p light` | ゲート（high のみ、`fail_on_findings`） |

### GitHub Actions

```yaml
- uses: your-org/delta-lint@v1
  with:
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    mode: "suggest"           # "review" / "suggest" / "autofix"
    severity: "high"
    fail_on_findings: "true"
```

| 入力 | デフォルト | 説明 |
|------|-----------|------|
| `anthropic_api_key` | （必須） | Anthropic API キー |
| `mode` | `"review"` | `review`（コメントのみ）/ `suggest`（インライン提案）/ `autofix`（自動コミット） |
| `severity` | `"high"` | 最小報告重要度 |
| `model` | `"claude-sonnet-4-20250514"` | 使用モデル |
| `max_diff_files` | `20` | 変更ファイル数がこれを超えるとスキップ |
| `comment_on_clean` | `false` | findings 0件でもコメント投稿 |
| `fail_on_findings` | `false` | findings 検出時にワークフローを失敗させる |

## 11. 設計判断記録（ADR）

| ADR | 判断 | 理由 |
|-----|------|------|
| 001 | retrieval 定数を argparse に露出しない | CLI namespace 汚染防止。profile で制御 |
| 002 | スコアリングは3層マージ | チーム別カスタマイズと安全なデフォルト |
| 003 | CLI バックエンド（$0）をデフォルト | コスト最優先。過去に $100+ の浪費実績 |
| 004 | findings は append-only JSONL | LLM 非決定性への対策 |
| 005 | テンプレート3段解決 | カスタマイズ性と簡便性の両立 |
| 006 | ドキュメントを契約面として扱う | コード×ドキュメント矛盾検出 |
| 007 | Deep scan は max_hops=3 の依存解決 | レガシー scope マッピング |

## 12. 強み

1. **仮説検証に基づく設計** — module-level context で Recall 45%→89%、63リポの統計等、実験データに裏付け
2. **LLM 非決定性への体系的多層防御** — ID, dedup, 丸め, 正規化, パースフォールバック
3. **ゼロコスト設計** — claude -p + キャッシュ + バッチで実質無料
4. **自己学習ループ** — finding → sibling_map → 次回のコンテキスト → より高精度な検出
5. **情報理論的カバレッジ推定** — Chao1 で「まだ何件潜んでいるか」を定量化
6. **漸進的導入** — `--baseline` で既存バグを無視し、新規のみ CI 検出
7. **ペルソナ対応** — 同じ finding を engineer / PM / QA それぞれの言語で翻訳

## 13. 潜在的弱点 / レビュー観点

### アーキテクチャ

1. **2フェーズパイプラインのコスト効率**: Phase 1（高recall）→ Phase 2（高precision）の分離は妥当か？1回の精密な呼び出しでトークン効率を上げる方法はないか？
2. **1-hop 依存の制限**: デフォルト1-hop、deep で3-hop。コンテキスト爆発防止と見逃しのトレードオフは適切か？
3. **Tier 3 依存解決の精度**: 同名ファイルが複数存在する大規模リポで誤った依存を拾うリスク

### データ・スケーラビリティ

4. **append-only JSONL のスケーラビリティ**: thousands of findings が蓄積した場合の読み取り性能。インデックスや GC の必要性は？
5. **JSONL のロック機構**: 並列バッチで race condition の可能性
6. **fan_out 計算**: git grep ベースで大規模モノレポではタイムアウト可能性
7. **キャッシュ無効化の粗さ**: prompt や constraints の変更がキャッシュキーに含まれない

### スコアリング・情報理論

8. **Chao1 推定の妥当性**: 種の豊富さ推定をバグ発見に転用する前提条件（独立同分布サンプリング等）は成立しているか？
9. **スコアリングの4軸**: debt_score / roi / info_score / Chao1 が独立に存在する意味と、統合指標の必要性
10. **重複排除の閾値**: trigram 類似度55%、エンティティ重複60%の根拠と調整可能性
11. **Phase 1/2 の閾値固定**: confidence_threshold=0.7 がリポジトリ特性に応じた適応機構なし

### コード品質

12. **findings.py の責務過剰**: 1896行で JSONL管理〜ダッシュボードHTML生成まで。SRP の観点から分離余地
13. **cmd_scan.py のコード重複**: PR/wide/smart/diff の4モードで `run_batch()` がほぼ同一実装（⑧ Duplication Drift に該当）
14. **ステータス遷移制約なし**: STATUS_META に許可遷移が未定義。`found → merged` が validation なしに可能
15. **テストカバレッジ**: scoring.py や info_theory.py のような純粋計算ロジックにユニットテストが不足

### その他

16. **contract_graph（実験的）**: WordPress 以外への汎化可能性と、通常スキャンパスとの統合の見通し
17. **sibling_map の自動学習**: git 履歴から暗黙の結合を学習する仕組みの精度と偽陽性率
18. **プロンプトエンジニアリング**: Escalation Protocol、非対称コスト（29:1）、経験的事前分布のプロンプト埋め込みは効果的か？

## ライセンス

Apache License 2.0
