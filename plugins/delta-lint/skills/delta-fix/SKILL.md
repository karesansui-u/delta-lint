---
name: delta-fix
user-invocable: true
description: >
  Debt scoring, fix generation, and PR submission.
  Prioritizes findings by scoring.py の計算結果, generates fixes,
  and creates one branch + PR per finding (with pre-commit regression check).
  Also supports fixing from GitHub Issues (--issue flag).
  Triggers on "負債解消", "バグ直して", "findings直して", "自動修正", "採点して",
  "優先度つけて", "PR出して", "Issue出して", "delta fix", "Issue直して", or similar.
  Requires delta-scan findings first (except --issue mode).
compatibility: Python 3.11+, git, gh CLI. macOS/Linux/Windows.
metadata:
  author: karesansui-u
  version: 0.6.0
---

# delta-fix: Debt Scoring & Fix & PR

confirmed findings または GitHub Issue を入力に、ブランチ→修正→デグレチェック→PR を自動実行する。

## Critical Rules

- **finding の修正は必ず以下の CLI コマンドで実行すること。自分で Grep/Read/Edit してコードを修正してはいけない。**
  ```bash
  cd ~/.claude/skills/delta-lint/scripts && python cli.py fix --repo <REPO_PATH> --ids <FINDING_ID> -v
  ```
  このコマンドがブランチ作成・fix生成・適用・デグレチェック・commit・push・PR・ベースブランチ復帰を全自動で行う。手動で `git checkout -b`、`git commit`、`git push`、`gh pr create` を個別に実行してはいけない。手動実行するとデグレチェックのスキップ、フォーク元への誤送信、ブランチ未復帰などの問題が発生する。
- **PR/コミットに Co-Authored-By 行や「Generated with Claude Code」等のブランディングを入れない**（グローバルポリシー）。

## Prerequisites

- Python 3.11+, git, gh CLI（認証済み）
- delta-scan findings が存在すること（先に `/delta-scan` を実行）。`--issue` モードは findings 不要
- ワーキングディレクトリがクリーンであること

## Workflow

### CLI コマンド（推奨）

```bash
# findings の優先度上位3件を自動修正→PR
cd ~/.claude/skills/delta-lint/scripts && python cli.py fix --repo <REPO_PATH> -v

# 特定の findings のみ
cd ~/.claude/skills/delta-lint/scripts && python cli.py fix --repo <REPO_PATH> --ids dl-a1b2c3d4,dl-e5f6g7h8 -v

# GitHub Issue から修正PR作成
cd ~/.claude/skills/delta-lint/scripts && python cli.py fix --repo <REPO_PATH> --issue 42 -v

# dry-run（修正生成のみ、commit/push しない）
cd ~/.claude/skills/delta-lint/scripts && python cli.py fix --repo <REPO_PATH> --dry-run -v
```

**これ1行で以下が全自動実行される。手動でステップを実行しないこと。**

1. findings を優先度順にソート（または Issue を取得→finding形式に変換）
2. finding ごとに: ブランチ作成 → fix生成 → 適用 → **デグレチェック** → commit → push → PR
3. デグレチェックで high finding → 自動ブロック（修正を revert してスキップ）
4. 全件完了後、ベースブランチ（main）に自動復帰
5. Issue モードの場合、PR に `Closes #N` を自動付与

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--repo` | `.` | Target git repository path |
| `--count` / `-n` | 3 | Number of findings to process |
| `--ids` | (none) | Comma-separated finding IDs to fix (overrides priority) |
| `--issue` | (none) | GitHub Issue番号を指定して修正PRを作成 |
| `--model` | claude-sonnet-4-20250514 | LLM model for fix generation |
| `--backend` | cli | `cli` ($0) or `api` (pay-per-use) |
| `--status` | found,confirmed | Statuses to include |
| `--base-branch` | (current) | Base branch for fix branches |
| `--dry-run` | false | Generate fixes + show diff only (no commit/push/PR) |
| `--verbose` / `-v` | false | Show progress |
| `--json` | false | JSON output |

## Triggers

| ユーザー発話 | 動作 |
|-------------|------|
| `delta fix` | confirmed 上位3件を自動PR |
| `delta fix --issue 42` | Issue #42 から修正PR |
| `delta scan --autofix` | scan 内で confirmed 全件を自動PR |
| 「PR出して」「Issue出して」「Issue直して」 | confirmed 全件を自動PR |
| 「採点して」「優先度つけて」 | dry-run（スコア表示のみ） |
| `--ids F001,F002` | 指定 findings のみ処理 |
