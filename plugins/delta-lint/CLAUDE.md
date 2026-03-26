# delta-lint 開発ガイド

## ソースの正（最重要）

**コードの編集は本リポジトリのプラグイン直下で行う:**
```
<repo>/plugins/delta-lint/scripts/
```

Claude Code にインストール後は `~/.claude/plugins/.../delta-lint/.../scripts/` などにコピーが展開される。`plugin update` で GitHub の最新に追従する。

ワークフロー Markdown に出てくる `cd ~/.claude/skills/delta-lint/scripts` は **ユーザー環境向けの例**。開発時は `plugins/delta-lint/scripts/` に置き換えて実行すればよい。

**Skills の参照文書（重複なし）:** `delta-scan` / `delta-review` それぞれの `SKILL.md` と同階層の `references/` のみ。削除済みの `skills/delta-lint/references/` は触らない。レイアウトの説明は [docs/skills-layout-handoff-for-llms.md](../../docs/skills-layout-handoff-for-llms.md)。

## アーキテクチャ制約

### データ（append-only ログ）
- `findings.jsonl` — append-only イベントログ。同一IDの後発エントリが最新状態。直接編集禁止。API: `add_finding()` / `update_finding()`
- `scan_history.jsonl` — append-only。`finding_ids` と `patterns_found` は Chao1 カバレッジ推定に使用
- `generate_id()` — ファイルペア+パターンからの構造ベースハッシュ。LLMの表現揺れに依存しない。**ロジック変更禁止**（IDが変わると重複発生）

### スコアリング
- `scoring.py` が唯一の重み定義元。findings.py は scoring.py から import する。独自の重みを定義するな
- `info_theory.py` — 情報理論ベースのスコアリング（surprise, Chao1, entropy）。scoring.py と並行して存在

### テンプレート
- `templates/findings_dashboard.html` — Python の `string.Template` (`$variable`)。Jinja2 ではない。`${}` は使えるが `{% %}` は使えない

### taxonomies
- `dict` 型で自由にキー追加可能。値は `str | list[str]`
- 旧 `category` フィールドは後方互換用。新規コードは `taxonomies` を使え
- `_migrate_taxonomies()` が旧→新の変換を担当

### LLM 呼び出し
- `claude -p` (CLI) を使え。Anthropic API 直叩き禁止（コスト理由）
- **全 LLM 呼び出しは `llm.py` の `call_llm()` を経由すること**。各モジュールで直接 `subprocess` や SDK を呼ばない
- バックエンド自動選択: CLI → Anthropic SDK → HTTP の3層フォールバック

## モジュール依存関係

> コマンド責務の詳細は [docs/architecture-commands.md](../../docs/architecture-commands.md) を参照。

```
cli.py              ─── メイン CLI エントリポイント。argparse + view/suppress/config コマンド
├── cli_utils.py        ─── 共通ユーティリティ。環境チェック、config/profile 読込、ベースライン
├── llm.py              ─── LLM バックエンド抽象化（call_llm: CLI→SDK→HTTP 3層フォールバック）
├── cmd_init.py         ─── init コマンド。セットアップのみ（スキャンしない）
├── cmd_scan.py         ─── scan コマンド群。cmd_scan, cmd_scan_deep, cmd_scan_full, cmd_watch
├── scanner.py          ─── コア検出パイプライン（context→detect→verify→filter）。cmd_scan/CI 共通
├── output_formats.py   ─── CI 出力フォーマッター（JSON/PR Markdown/annotations/SARIF）
├── detector.py         ─── LLM スキャン（通常）→ llm.py 経由
├── retrieval.py        ─── ファイル取得 + import 依存解析
├── findings.py         ─── JSONL 管理 + ダッシュボード生成
│   ├── scoring.py          ─── スコアリング重み（ROI, debt_score）
│   └── info_theory.py      ─── 情報理論スコアリング（surprise, Chao1）
├── surface_extractor.py ─── Deep scan Phase 0（正規表現抽出）
├── contract_graph.py    ─── 契約グラフ検出（WordPress 等フック系向け、レガシー）
├── deep_verifier.py     ─── Deep scan Phase 2（LLM 検証）→ llm.py 経由
│   ※ --depth deep は通常スキャンと同じ LLM パスで max_hops=3 の依存解決を行う
├── git_enrichment.py    ─── Git churn/fan_out 計算（スキャン時に finding へ埋め込み）
├── output.py            ─── 表示フォーマット
├── verifier.py          ─── LLM 検証（通常スキャン用）→ llm.py 経由
├── suppress.py          ─── 抑制管理
├── fixgen.py            ─── Autofix 生成 → llm.py 経由
├── debt_loop.py         ─── 自動負債解消ループ（finding → branch → fix → PR）
└── stress_test.py       ─── ストレステスト + 地雷マップ（scan/init とは独立）
```

## 実行方法

```bash
cd scripts/
python cli.py scan --repo /path/to/repo         # 通常スキャン
python cli.py scan --repo /path/to/repo --depth deep   # 深層スキャン（依存チェーンを辿る）
python cli.py findings list --repo /path/to/repo # Findings 一覧
python cli.py findings dashboard --repo /path    # ダッシュボード生成
```

## PR/コミットのルール

- 「Claude Code」「Generated with」等のブランディングを入れるな
- Co-Authored-By 行も入れるな
