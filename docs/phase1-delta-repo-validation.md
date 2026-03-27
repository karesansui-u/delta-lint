# Phase 1 — δ_repo の実証検証

## 目的

δ_repo（および関連するカバレッジ推定）が、**リポジトリの「外から見える健全性」**とどの程度一致するかをデータで見る。理論上の限界（二重計上・未検出）を議論するだけでなく、**相関の有無**から次の改善（閾値キャリブレーション vs 集約ロジックの見直し）の優先度を決める。

## 核心の問い

1. **δ_repo が高いリポ**は、選んだ外部プロキシ（例: 期間内の bug 系 issue 数）も悪いか。
2. **δ は低いが Chao1 の unseen が大きい**リポが偏るか（スキャン不足・見つかっていないだけ）。

## 外部プロキシの選び方（1 本に絞って開始）

次のいずれか **1 指標**でよい。複数は Phase 1 の後半で追加する。

| プロキシ例 | 取得のしかた |
|------------|----------------|
| GitHub: `bug` / `type/bug` ラベル付き issue（open） | `gh issue list --label bug --state open --json number` を期間でフィルタ、または Search API |
| GitHub: マージ済み fix PR 数（週次・月次） | `gh pr list --state merged` + `mergedAt` |
| 社内 | インシデント件数・サポートチケット等（手入力列で可） |

**注意**: リポの規模差をならすなら、LOC・コミット数・コントリビュータ数で割った値を別列に残す（回帰の共変量）。

## データ収集フロー

1. 対象リポごとに delta-lint を走らせ、`.delta-lint/findings/*.jsonl` と `scan_history.jsonl` が溜まっている状態にする（同一コミット付近でスナップショットを取るとよい）。
2. 本リポジトリのスクリプトでメトリクスをエクスポートする（次節）。
3. スプレッドシートまたはノートブックで **外部プロキシ列を結合**する。
4. **Spearman 相関**（δ_repo vs プロキシ）と散布図を見る。外れ値はリポ名で確認。

## メトリクス出力（自動）

`plugins/delta-lint/scripts` から:

```bash
cd plugins/delta-lint/scripts

# 単一リポ
python -m calibration.export_phase1_metrics --repo /path/to/repo

# 複数（改行区切りパス）
python -m calibration.export_phase1_metrics --repos-file ./my_repos.txt

# JSON Lines（1 行 1 リポ）も出す
python -m calibration.export_phase1_metrics --repo /path/to/repo --jsonl /tmp/phase1.jsonl
```

CLI からも同じ内容を出力できる（`python cli.py` = 上記ディレクトリで実行）:

```bash
cd plugins/delta-lint/scripts

# カレントの .delta-lint のみ → CSV
python cli.py findings phase1-export -o phase1.csv

# 明示パス（複数可）+ ファイルリスト
python cli.py findings phase1-export /path/a /path/b --repos-file calibration/repos.example.txt -o phase1.csv
```

リポリストの雛形: [plugins/delta-lint/scripts/calibration/repos.example.txt](../plugins/delta-lint/scripts/calibration/repos.example.txt)

生成 CSV の列はスクリプトのヘルプと `export_phase1_metrics.py` 先頭の docstring を参照。`ext_proxy_*` は空のまま手埋めする。

## 解釈のガイド

| 観測 | 示唆 |
|------|------|
| δ とプロキシに**正の単調な関係** | 閾値（🟢〜💀）を分位点で再キャリブレーションしやすい |
| **ほぼ無相関** | 独立性仮定の二重計上、I_BASE のセル定義、fallback、プロキシの不適合のどれかを疑う |
| δ は低いが **unseen が大きい**が多い | スキャン回数・スコープを揃えた再測定、または「低 δ でもリスクあり」帯の UI 表現を検討 |

## 次フェーズ（Phase 1 の外、メモ）

- 同一根本原因の二重計上を減らす集約（ファイルペア単位の max、クラスタ ID など）を定義し、**δ_alt** を別列で出して相関を比較する。
- プロキシを複数に増やし、感度分析する。

## 参照

- I_BASE / δ_repo の定義: [plugins/delta-lint/scripts/info_theory.py](../plugins/delta-lint/scripts/info_theory.py)
- Chao1 / カバレッジ: 同ファイル `compute_coverage_from_history`
