"""
Visualization layer for delta-lint stress-test.

Generates a single self-contained HTML file with a D3.js treemap heatmap
showing per-file risk scores from stress-test results.

No Python dependencies beyond stdlib. Output HTML uses D3.js via CDN.

Template is loaded from templates/dashboard.html and injected with data
using string.Template ($variable substitution). This keeps HTML/CSS/JS
in a proper .html file where editors can lint and highlight them.
"""

import argparse
import json
import sys
from pathlib import Path

from aggregate import aggregate_results, build_treemap_data, FileRisk


_CATEGORY_RULES: list[tuple[str, str]] = [
    # (path substring, human label)
    ("providers", "外部連携"),
    ("provider", "外部連携"),
    ("llm", "LLM / AI"),
    ("model", "モデル層"),
    ("agent", "エージェント"),
    ("chain", "エージェント"),
    ("pipeline", "パイプライン"),
    ("redteam", "セキュリティ"),
    ("security", "セキュリティ"),
    ("auth", "認証"),
    ("middleware", "ミドルウェア"),
    ("gateway", "APIゲートウェイ"),
    ("api", "API層"),
    ("route", "ルーティング"),
    ("handler", "リクエスト処理"),
    ("controller", "コントローラー"),
    ("service", "ビジネスロジック"),
    ("util", "ユーティリティ"),
    ("helper", "ユーティリティ"),
    ("lib", "ライブラリ"),
    ("common", "共通"),
    ("shared", "共通"),
    ("core", "コアエンジン"),
    ("config", "設定"),
    ("env", "環境設定"),
    ("db", "データベース"),
    ("database", "データベース"),
    ("store", "データストア"),
    ("cache", "キャッシュ"),
    ("queue", "メッセージキュー"),
    ("event", "イベント / フック"),
    ("hook", "イベント / フック"),
    ("plugin", "プラグイン"),
    ("extension", "拡張機能"),
    ("cli", "CLI"),
    ("cmd", "CLI / コマンド"),
    ("command", "CLI / コマンド"),
    ("test", "テスト"),
    ("spec", "テスト"),
    ("ui", "UI / フロントエンド"),
    ("view", "UI / フロントエンド"),
    ("component", "UIコンポーネント"),
    ("page", "ページ"),
    ("style", "スタイル / CSS"),
    ("template", "テンプレート"),
    ("script", "スクリプト"),
    ("tool", "ツール"),
    ("deploy", "デプロイ"),
    ("docker", "Docker / インフラ"),
    ("infra", "インフラ"),
    ("ci", "CI/CD"),
    ("log", "ログ / 監視"),
    ("metric", "メトリクス / 監視"),
    ("eval", "評価"),
    ("assertion", "アサーション"),
    ("grader", "アサーション"),
    ("prompt", "プロンプト管理"),
    ("transform", "データ変換"),
    ("parse", "パーシング"),
    ("serial", "シリアライズ"),
    ("format", "フォーマット"),
    ("export", "エクスポート"),
    ("import", "インポート"),
    ("migrate", "マイグレーション"),
    ("schema", "スキーマ / 型"),
    ("type", "スキーマ / 型"),
    ("error", "エラー処理"),
    ("exception", "エラー処理"),
]


def _categorize_dir(dir_name: str) -> str:
    """Map a directory name to a human-readable category label."""
    lower = dir_name.lower()
    for keyword, label in _CATEGORY_RULES:
        if keyword in lower:
            return label
    return ""


def _add_category_labels(node: dict) -> None:
    """Walk treemap tree and add 'category' to directory nodes."""
    if "children" not in node:
        return
    for child in node["children"]:
        if "children" in child:
            cat = _categorize_dir(child["name"])
            if cat:
                child["category"] = cat
            _add_category_labels(child)



def build_treemap_json(
    results_path: str,
    confirmed_bugs: dict[str, list[dict]] | None = None,
) -> str:
    """Build treemap JSON string from stress-test results.

    Returns JSON string suitable for passing to generate_dashboard(treemap_json=...).
    """
    data = json.loads(Path(results_path).read_text(encoding="utf-8"))
    results = data.get("results", [])
    metadata = data.get("metadata", {})
    n_modifications = metadata.get("n_modifications", len(results))
    repo_name = metadata.get("repo_name", "repository")

    # Derive repo_path from results_path: {repo}/.delta-lint/stress-test/results.json
    repo_path = str(Path(results_path).resolve().parent.parent.parent)
    file_risks = aggregate_results(results, n_modifications, confirmed_bugs,
                                   repo_path=repo_path)
    risky_files = {k: v for k, v in file_risks.items() if v.risk_score > 0}
    treemap_data = build_treemap_data(risky_files, repo_name)
    _add_category_labels(treemap_data)

    return json.dumps(treemap_data, ensure_ascii=False)


def generate_heatmap(
    results_path: str,
    output_path: str,
    confirmed_bugs: dict[str, list[dict]] | None = None,
) -> str:
    """Generate unified dashboard with landmine map from stress-test results.

    The output_path is kept for backward compatibility but the actual output
    goes to the unified dashboard at .delta-lint/findings/dashboard.html.
    A symlink or copy is placed at output_path for callers that expect it there.

    Returns:
        Path to the generated HTML file
    """
    # Build treemap JSON
    treemap = build_treemap_json(results_path, confirmed_bugs)

    # Determine repo base path from output_path
    # output_path is typically .delta-lint/stress-test/landmine_map.html
    out = Path(output_path)
    # Walk up to find .delta-lint parent
    base_path = out.parent.parent.parent  # repo root (above .delta-lint/)
    if not (base_path / ".delta-lint").exists():
        # Fallback: try output_path's grandparent
        base_path = out.parent.parent

    from findings import generate_dashboard
    dash_path = generate_dashboard(base_path, treemap_json=treemap)

    print(f"Dashboard generated: {dash_path}", file=sys.stderr)
    return str(dash_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate HTML heatmap from delta-lint stress-test results"
    )
    parser.add_argument("--input", required=True, help="Path to results.json")
    parser.add_argument("--output", default="landmine_map.html", help="Output HTML path")
    parser.add_argument("--bugs", default=None, help="Path to confirmed_bugs.json (optional)")

    args = parser.parse_args()

    confirmed = None
    if args.bugs:
        confirmed = json.loads(Path(args.bugs).read_text(encoding="utf-8"))

    generate_heatmap(
        results_path=args.input,
        output_path=args.output,
        confirmed_bugs=confirmed,
    )
