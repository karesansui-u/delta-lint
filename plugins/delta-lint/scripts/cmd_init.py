"""cmd_init – extracted from cli.py for modularity."""

import re
import sys
from pathlib import Path

from cli_utils import _open_dashboard


# ── CLAUDE.md auto-injection ──────────────────────────────────────

_CLAUDE_MD_MARKER_START = "<!-- delta-lint:auto-start -->"
_CLAUDE_MD_MARKER_END = "<!-- delta-lint:auto-end -->"

_CLAUDE_MD_BLOCK = f"""{_CLAUDE_MD_MARKER_START}
## delta-lint guard

- **scan 完了後、`found` が 0 になるまで全件調査・ステータス更新を自律完走すること。** ユーザーの指示を待たない。
- コード変更前に `/delta-review` を実行して影響範囲を確認すること
- コード変更後は `/delta-scan` の実行を提案すること
- **delta fix**: finding の修正は必ず以下の CLI コマンドで実行すること。自分で Grep/Read/Edit してコードを手動修正してはいけない。
  ```bash
  cd ~/.claude/skills/delta-lint/scripts && python cli.py fix --repo <REPO_PATH> --ids <FINDING_IDS> -v
  ```
  このコマンドがブランチ作成→fix生成→適用→デグレチェック→commit→push→PR→ステータス更新を全自動で行う。
{_CLAUDE_MD_MARKER_END}"""


def inject_claude_md(repo_path: str, *, verbose: bool = False) -> bool:
    """Inject or update delta-lint guard rules in the repo's CLAUDE.md.

    Uses HTML comment markers to manage its own block without touching
    user-written content. Returns True if the file was modified.
    """
    claude_md = Path(repo_path) / "CLAUDE.md"
    marker_re = re.compile(
        re.escape(_CLAUDE_MD_MARKER_START) + r".*?" + re.escape(_CLAUDE_MD_MARKER_END),
        re.DOTALL,
    )

    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if _CLAUDE_MD_MARKER_START in content:
            new_content = marker_re.sub(_CLAUDE_MD_BLOCK, content)
            if new_content == content:
                return False  # already up-to-date
            claude_md.write_text(new_content, encoding="utf-8")
            if verbose:
                print("  📝 CLAUDE.md の delta-lint ブロックを更新しました", file=sys.stderr)
            return True
        else:
            claude_md.write_text(content.rstrip() + "\n\n" + _CLAUDE_MD_BLOCK + "\n", encoding="utf-8")
            if verbose:
                print("  📝 CLAUDE.md に delta-lint guard を追加しました", file=sys.stderr)
            return True
    else:
        claude_md.write_text(_CLAUDE_MD_BLOCK + "\n", encoding="utf-8")
        if verbose:
            print("  📝 CLAUDE.md を作成しました（delta-lint guard）", file=sys.stderr)
        return True


def cmd_init(args):
    """Initialize delta-lint for a repository (lightweight)."""
    repo_path = str(Path(args.repo).resolve())

    # Intro animation (skip if --quiet or non-TTY)
    if not getattr(args, 'quiet', False) and sys.stderr.isatty():
        try:
            from intro_animation import run_animation
            run_animation()
        except Exception:
            pass

    print("── δ-lint ── 初期化開始", file=sys.stderr)

    # Step 0.5: Git history analysis → initial sibling map
    print("  Git 履歴を解析中...", file=sys.stderr)
    git_siblings_count = 0
    try:
        from sibling import (
            generate_siblings_from_git_history,
            load_sibling_map,
            save_sibling_map,
            get_git_churn,
        )
        # Generate siblings from co-change history
        new_siblings = generate_siblings_from_git_history(
            repo_path, months=6, min_co_changes=3, verbose=args.verbose,
        )
        if new_siblings:
            existing = load_sibling_map(repo_path)
            all_entries = existing + new_siblings
            save_sibling_map(repo_path, all_entries)
            git_siblings_count = len(new_siblings)

        # Get churn data for display
        churn_data = get_git_churn(repo_path, months=6)
        if churn_data:
            print(f"  📊 よく改修されるファイル TOP 5:", file=sys.stderr)
            for item in churn_data[:5]:
                print(f"    {item['path']} ({item['changes']}回変更)", file=sys.stderr)

        if git_siblings_count:
            print(f"  🔗 co-change ペア: {git_siblings_count} 件を sibling_map に追加", file=sys.stderr)
    except Exception as e:
        if args.verbose:
            print(f"  [warn] Git history analysis failed: {e}", file=sys.stderr)

    # Step 1: Structure analysis (LLM)
    print("  構造解析を実行中（~30秒）...", file=sys.stderr)

    from stress_test import init_lightweight
    structure = init_lightweight(repo_path, verbose=args.verbose)

    modules = structure.get("modules", [])
    hotspots = structure.get("hotspots", [])

    print(f"\n  {len(modules)} モジュール, {len(hotspots)} ホットスポット検出", file=sys.stderr)
    if hotspots:
        print("  🔥 変更リスクが高いファイル:", file=sys.stderr)
        for h in hotspots[:5]:
            path = h.get("path", h.get("file", ""))
            reason = h.get("reason", "")
            print(f"    {path} — {reason}", file=sys.stderr)

    # Show dev_patterns (from git history analysis)
    dev_patterns = structure.get("dev_patterns", [])
    if dev_patterns:
        _PATTERN_ICONS = {
            "bug-prone": "🐛", "expanding": "📈", "refactoring": "🔧",
            "stable": "✅", "single-owner": "👤",
        }
        print(f"\n  📊 開発パターン分析 ({len(dev_patterns)} エリア):", file=sys.stderr)
        for dp in dev_patterns[:8]:
            icon = _PATTERN_ICONS.get(dp.get("pattern", ""), "❓")
            area = dp.get("area", "?")
            pattern = dp.get("pattern", "?")
            risk = dp.get("risk", "")
            print(f"    {icon} {area} [{pattern}]", file=sys.stderr)
            if risk:
                print(f"      → {risk[:120]}", file=sys.stderr)

    # Show constraints info
    constraints_path = Path(repo_path) / ".delta-lint" / "constraints.yml"
    if constraints_path.exists():
        print(f"\n  📋 constraints.yml 生成済み: {constraints_path}", file=sys.stderr)
        print("    ベテランの知識を追記すると scan 精度が上がります", file=sys.stderr)

    # Show sibling map summary
    try:
        from sibling import load_sibling_map as _load_sib
        all_sibs = _load_sib(repo_path)
        if all_sibs:
            git_count = sum(1 for s in all_sibs if s.source == "git-history")
            finding_count = sum(1 for s in all_sibs if s.source == "finding")
            print(f"\n  🔗 sibling_map: {len(all_sibs)} ペア（git履歴: {git_count}, finding: {finding_count}）", file=sys.stderr)
    except Exception:
        pass

    # Show progressive scan coverage
    try:
        from stress_test import load_coverage
        coverage = load_coverage(repo_path)
        n_covered = len(coverage.get("scanned_files", {}))
        total_scans = coverage.get("total_scans", 0)
        if total_scans > 0:
            from stress_test import _list_source_files
            n_total = len(_list_source_files(repo_path))
            pct = round(n_covered / max(n_total, 1) * 100)
            print(f"\n  📈 スキャンカバレッジ: {n_covered}/{n_total} ファイル ({pct}%) — {total_scans}回実行", file=sys.stderr)
            if pct < 100:
                print(f"    → 再実行するとカバレッジが拡大します（未分析エリアを優先）", file=sys.stderr)
    except Exception:
        pass

    # Inject/update CLAUDE.md guard rules
    try:
        if inject_claude_md(repo_path, verbose=args.verbose):
            print("  📝 CLAUDE.md に delta-lint guard を注入しました", file=sys.stderr)
    except Exception as e:
        if args.verbose:
            print(f"  [warn] CLAUDE.md injection failed: {e}", file=sys.stderr)

    print("\n── δ-lint ── 初期化完了 ✅", file=sys.stderr)

    # Auto-open dashboard after init
    if not getattr(args, 'no_open', False):
        try:
            from findings import generate_dashboard
            _dash_tpl = getattr(args, '_dashboard_template', "")
            dash_path = generate_dashboard(repo_path, dashboard_template=_dash_tpl)
            if dash_path:
                if _open_dashboard(str(dash_path), force=True, live=True):
                    print("  📊 ダッシュボードを開きました", file=sys.stderr)
        except Exception as e:
            if args.verbose:
                print(f"  [warn] ダッシュボードを開けませんでした: {e}", file=sys.stderr)

    print("  次のステップ:", file=sys.stderr)
    print("    delta-scan                    — 変更ファイルをスキャン", file=sys.stderr)
    print("    delta-scan --scope wide       — 全ファイルスキャン（バッチ分割）", file=sys.stderr)
    print("    delta-scan --lens stress      — ストレステスト（地雷マップ生成）", file=sys.stderr)
    print("    delta-scan --lens security    — セキュリティ重点スキャン", file=sys.stderr)
