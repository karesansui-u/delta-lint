# delta-lint コマンドアーキテクチャ設計書

> **この文書が SSoT（Single Source of Truth）。**
> 他ファイルの記述がこの設計書と矛盾する場合、この設計書が正。
> 最終更新: 2026-03-27

## 概要

delta-lint は 3 つのコマンドで構成される。各コマンドの責務は重複しない。

## 1. delta-init — セットアップ（スキャンしない）

リポジトリの構造を解析し、以降のスキャンに必要な前提データを生成する。
**矛盾検出（LLM スキャン）は一切行わない。**

### やること
- Git 履歴分析 → `sibling_map.yml`（co-change ペア）
- 構造解析（LLM） → `structure.json`（モジュール、ホットスポット、暗黙の制約）
- CLAUDE.md へのガードルール注入
- ダッシュボード生成

### やらないこと
- 矛盾検出スキャン（scan_existing を含む）
- ストレステスト
- findings の記録

### 生成物
```
.delta-lint/
├── sibling_map.yml                  # co-change ペア
├── stress-test/structure.json       # モジュール・ホットスポット・制約
├── .gitignore                       # * + !.gitignore
└── findings/dashboard.html          # 空のダッシュボード（findings なし）
CLAUDE.md                            # guard block（<!-- delta-lint:auto-start/end -->）
```

### 所要時間
約 30 秒。LLM 呼び出しは構造解析の 1 回のみ。

### 実装
- CLI: `python cli.py init --repo <path>`
- コード: `cmd_init.py:cmd_init()`

---

## 2. delta-scan — すべての矛盾検出スキャン

ソースコードの構造矛盾を検出し、findings として記録・調査・ステータス更新する。
**「記録して終わり」ではない。全件の調査・判定まで完走する。**

### やること
1. ファイル選択（scope に基づく）
2. LLM 検出 → LLM 検証
3. findings 記録（JSONL）
4. 自動トリアージ（dead code, already fixed, reachability, confirmed 判定）
5. 自動調査 & ステータス更新（`found` が 0 になるまで）
6. ダッシュボード再生成

### 初回動作
`structure.json` がなければ **delta-init を自動実行** してからスキャンに進む。
init はセットアップのみ（スキャンなし）なので、体感の遅延は小さい。

### スコープ・深度・レンズ
| フラグ | 選択肢 | デフォルト |
|--------|--------|-----------|
| `--scope` | `diff`, `smart`, `wide`, `pr` | `diff`（3ヶ月） |
| `--depth` | `default`, `deep` | `default` |
| `--lens` | `default`, `security` | `default` |
| `--since` | `1week`, `3months`, `1year` 等 | `3months` |

### 実装
- CLI: `python cli.py scan --repo <path> [flags]`
- コード: `cmd_scan.py:cmd_scan()`

---

## 3. delta-stress — ストレステスト（独立・重い）

仮想改修を N 件生成し、各改修後のコードをスキャンして改修リスクの高いファイルを特定する。
**delta-scan / delta-init とは独立。単独で呼び出す。**

### やること
1. structure.json からホットスポットを取得
2. 仮想改修を N 件生成
3. 各改修をスキャン（矛盾が出るか）
4. ヒートマップ（地雷マップ）生成
5. 技術的負債を findings に登録（`ingest_stress_test_debt`）

### やらないこと
- 既存コードのスキャン（それは scan の仕事）

### 所要時間
10〜30 分。**バックグラウンド実行が必須。**

### 呼び出し方
- CLI: `python cli.py scan --repo <path> --lens stress`
- スキル: `/delta-scan` で「ストレステスト」と言う
- 直接: `python stress_test.py --repo <path> --parallel 10 --verbose --visualize`

### 実装
- CLI ルーティング: `cli.py` で `--lens stress` → `cmd_scan_full()`
- コード: `cmd_scan.py:cmd_scan_full()` → `stress_test.py:run_stress_test()`

---

## 4. コマンド間の依存関係

```
delta-scan (初回)
  └── structure.json がない
        → delta-init を自動実行（セットアップのみ、~30秒）
        → 完了後、scan 本体を続行

delta-stress
  └── structure.json がない
        → delta-init を自動実行
        → 完了後、ストレステスト本体を続行

delta-init
  └── 依存なし。単独で実行可能。
```

**scan と stress は互いに独立。どちらを先に実行してもよい。**

---

## 5. scan_existing() の扱い

`stress_test.py:scan_existing()` はホットスポットクラスタを対象にした既存矛盾スキャン。

**init には含めない。** init はセットアップのみ。
**scan で自然にカバーされる。** `--scope smart` がホットスポット優先でファイルを選択するため、
init 直後の初回 scan で scan_existing 相当の範囲がスキャンされる。

---

## 6. ファイル別の責務マトリクス

各ファイルが「何について記述してよいか」を定義する。

### 実装（Python）

| ファイル | 記述してよい内容 | 記述してはいけない内容 |
|---|---|---|
| `cmd_init.py` | init（構造解析、sibling_map、CLAUDE.md 注入） | スキャンロジック（detector/verifier の import 禁止） |
| `cmd_scan.py` | scan / scan_full（ファイル選択、検出、検証、findings 記録） | init のセットアップロジック |
| `stress_test.py` | `init_lightweight()`, `run_stress_test()`, `scan_existing()` | — |
| `cli.py` | argparse 定義、コマンドルーティング | 各コマンドの実装詳細 |
| `findings.py` | JSONL 管理、ダッシュボード生成 | — |

### スキルワークフロー（Markdown）

| ファイル | 記述してよい内容 | 記述してはいけない内容 |
|---|---|---|
| `SKILL.md` | トリガー語、ルーティング、Critical Rules | ワークフローの詳細手順 |
| `workflow-init.md` | init の手順（セットアップのみ） | スキャン手順、ストレステスト手順 |
| `workflow-scan.md` | scan の手順（初回 auto-init 含む） | ストレステスト手順 |
| `workflow-stress.md` | ストレステストの手順 | init/scan の手順 |

### プロジェクト文書

| ファイル | 記述してよい内容 |
|---|---|
| `plugins/delta-lint/CLAUDE.md` | モジュール依存関係、アーキテクチャ制約、実行例 |
| `CLAUDE.md`（root） | ユーザー向けガードルール |
| `docs/architecture-commands.md`（本文書） | コマンド責務定義（SSoT） |

---

## 7. 矛盾チェックリスト

設計変更時に確認すること:

- [ ] `cmd_init.py` に `detector`, `verifier`, `scan_existing` の import がないか
- [ ] `workflow-init.md` に scan/stress の手順が混入していないか
- [ ] `workflow-scan.md` Step -1 が「init = セットアップのみ」と記述しているか
- [ ] `workflow-stress.md` が init/scan の手順を含んでいないか
- [ ] `SKILL.md` のルーティングが 3 コマンドの責務分離と一致しているか
- [ ] `plugins/delta-lint/CLAUDE.md` のモジュール依存図が実装と一致しているか
