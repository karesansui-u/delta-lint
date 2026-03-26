# Workflow 0: Init (`delta init`)

Initialize delta-lint for a repository. Analyzes structure and sets up scanning prerequisites.
**init はセットアップのみ。矛盾検出スキャンは一切行わない。**

**Trigger**: User says "delta init", "初期化", "initialize delta-lint", or similar.

**CRITICAL: This workflow is FULLY AUTONOMOUS. Do NOT ask the user for confirmation at any step (except if already initialized). Execute Steps 1→2→3 immediately in sequence without pausing.**

## Step 0.5: Check git availability

```bash
git -C "{repo_path}" rev-parse --is-inside-work-tree 2>/dev/null
```

- If git repo: proceed normally.
- If NOT git repo: **proceed anyway**, but display this warning once:

```
⚠️ git リポジトリではないため、.gitignore によるフィルタリングが使えません。
node_modules 等は自動除外しますが、git 管理下のリポジトリと比べて精度が下がります。
git init してからの実行を推奨します。
```

## Step 1: Check if already initialized

```bash
ls {repo_path}/.delta-lint/stress-test/structure.json 2>/dev/null
```

- If exists: Tell user "このリポは初期化済みです。再実行しますか？" and wait for confirmation.
- If not: **Immediately proceed to Step 2. Do NOT ask "実行しますか？" — the user already said "delta init", that IS the instruction.**

## Step 1.5: Instant banner — OUTPUT IMMEDIATELY (テキスト出力のみ)

**このステップでは Bash ツールを使わない。** Claude のテキスト出力として以下をそのまま表示する。これが最初にユーザーの目に入るもの。

```
── δ-lint ── 初期化開始
  デグレ特化型構造矛盾検出
  構造解析 + セットアップを実行します...
```

**この出力を最初に行ってから** Step 2 に進む。

## Step 2: Run init CLI

```bash
cd ~/.claude/skills/delta-lint/scripts && python cli.py init --repo "{repo_path}" --verbose 2>&1
```

Use `timeout: 600000` (10 minutes).

init CLI が以下を順次実行する:
1. Git 履歴分析 → sibling_map.yml（co-change ペア）
2. 構造解析（init_lightweight） → structure.json（モジュール、ホットスポット）
3. CLAUDE.md guard 注入
4. ダッシュボード生成 & 表示

## Step 2.1: 構造分析の結果を表示

init CLI の出力からモジュール数・ホットスポット・開発パターンを読み取り、以下のフォーマットでユーザーに表示する:

```
── δ-lint ── 初期化中...

📊 リポジトリ概要:
  {n_modules} モジュール, {n_hotspots} ホットスポット

🔥 変更リスクが高いファイル:
  1. {dir/file1} — {reason1}
  2. {dir/file2} — {reason2}
  3. {dir/file3} — {reason3}

📊 開発パターン:
  {pattern_summary}
```

## Step 2.15: 過去バグ履歴の収集 — BACKGROUND CONTEXT

**init CLI の実行中に並行して、リポの過去バグ傾向を収集する。**
この情報は `.delta-lint/bug_history.json` に保存し、以降のスキャン時にコンテキストとして活用する。

```bash
cd {repo_path} && python3 -c "
import json, subprocess, os, re
result = {}

# 0. リモートURLからowner/repoを取得
try:
    remote = subprocess.run(['git', 'remote', 'get-url', 'origin'], capture_output=True, text=True).stdout.strip()
    repo_slug = '/'.join(remote.replace('.git','').split('/')[-2:])
    if ':' in repo_slug:
        repo_slug = repo_slug.split(':')[-1]
    result['repo_slug'] = repo_slug
except Exception:
    repo_slug = ''

    # 1. ラベル体系の把握 + 両方の経路で検索してマージ
if repo_slug:
    try:
        labels_out = subprocess.run(
            ['gh', 'label', 'list', '--repo', repo_slug, '--limit', '200', '--json', 'name'],
            capture_output=True, text=True, timeout=15
        )
        all_labels = [l['name'] for l in json.loads(labels_out.stdout)] if labels_out.returncode == 0 else []
        result['all_labels'] = all_labels

        bug_pat = re.compile(r'bug|defect|regression|broken|error|crash|fault', re.I)
        bug_labels = [l for l in all_labels if bug_pat.search(l)]
        result['bug_labels'] = bug_labels

        # 両方の経路で検索して URL で重複排除
        seen_urls = set()
        all_issues = []
        all_prs = []

        # 経路A: ラベル検索（各バグ系ラベルで）
        for label in bug_labels[:3]:
            try:
                r = subprocess.run(
                    ['gh', 'search', 'issues', '--repo', repo_slug, '--label', label,
                     '--limit', '30', '--json', 'title,url,state,createdAt,labels'],
                    capture_output=True, text=True, timeout=10
                )
                if r.returncode == 0:
                    for item in json.loads(r.stdout):
                        if item['url'] not in seen_urls:
                            seen_urls.add(item['url'])
                            item['_source'] = 'label:' + label
                            all_issues.append(item)
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ['gh', 'search', 'prs', '--repo', repo_slug, '--label', label,
                     '--state', 'merged', '--limit', '20', '--json', 'title,url,mergedAt'],
                    capture_output=True, text=True, timeout=10
                )
                if r.returncode == 0:
                    for item in json.loads(r.stdout):
                        if item['url'] not in seen_urls:
                            seen_urls.add(item['url'])
                            item['_source'] = 'label:' + label
                            all_prs.append(item)
            except Exception:
                pass

        # 経路B: タイトル検索（ラベルの有無に関係なく常にやる）
        try:
            r = subprocess.run(
                ['gh', 'search', 'issues', '--repo', repo_slug, 'bug OR fix OR regression OR broken',
                 '--limit', '50', '--json', 'title,url,state,createdAt,labels'],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                for item in json.loads(r.stdout):
                    if item['url'] not in seen_urls:
                        seen_urls.add(item['url'])
                        item['_source'] = 'title_search'
                        all_issues.append(item)
        except Exception:
            pass
        try:
            r = subprocess.run(
                ['gh', 'search', 'prs', '--repo', repo_slug, 'fix OR bug OR regression OR revert',
                 '--state', 'merged', '--limit', '30', '--json', 'title,url,mergedAt'],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                for item in json.loads(r.stdout):
                    if item['url'] not in seen_urls:
                        seen_urls.add(item['url'])
                        item['_source'] = 'title_search'
                        all_prs.append(item)
        except Exception:
            pass

        result['bug_issues'] = all_issues[:80]
        result['bugfix_prs'] = all_prs[:50]

        # ラベルカバレッジ: ラベル経路の件数 vs 全件数で信頼度を判定
        label_hits = len([i for i in all_issues if i.get('_source','').startswith('label:')])
        title_only = len([i for i in all_issues if i.get('_source') == 'title_search'])
        result['label_coverage'] = {
            'labeled': label_hits,
            'title_only': title_only,
            'total': len(all_issues),
            'reliability': 'high' if label_hits > title_only else 'low' if label_hits < title_only * 0.3 else 'medium',
        }
    except Exception as e:
        result['gh_error'] = str(e)

# 2. git log: fix/revert/bug コミット（直近6ヶ月）
try:
    log = subprocess.run(
        ['git', 'log', '--since=6 months', '--grep=fix\\|revert\\|bug', '-i',
         '--format=%H|%s|%an|%ai', '--', '*.py', '*.ts', '*.js', '*.go', '*.rs', '*.java', '*.rb', '*.php'],
        capture_output=True, text=True, timeout=15
    )
    commits = []
    for line in log.stdout.strip().split('\n'):
        if '|' in line:
            parts = line.split('|', 3)
            commits.append({'hash': parts[0][:8], 'subject': parts[1], 'author': parts[2], 'date': parts[3][:10]})
    result['bugfix_commits'] = commits[:100]
except Exception as e:
    result['git_error'] = str(e)

# 3. ファイル別バグ頻度
try:
    freq = subprocess.run(
        ['git', 'log', '--since=6 months', '--grep=fix\\|revert\\|bug', '-i',
         '--name-only', '--format='],
        capture_output=True, text=True, timeout=15
    )
    from collections import Counter
    files = [f.strip() for f in freq.stdout.split('\n') if f.strip()]
    result['bugfix_hotfiles'] = [{'file': f, 'count': c} for f, c in Counter(files).most_common(20)]
except Exception:
    pass

os.makedirs('.delta-lint', exist_ok=True)
with open('.delta-lint/bug_history.json', 'w') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
bl = result.get('bug_labels', [])
lc = result.get('label_coverage', {})
n_issues = len(result.get('bug_issues', []))
n_prs = len(result.get('bugfix_prs', []))
n_commits = len(result.get('bugfix_commits', []))
n_hotfiles = len(result.get('bugfix_hotfiles', []))
print(f'bug_labels: {bl[:5] if bl else \"(なし)\"}')
if lc:
    print(f'label_coverage: labeled={lc.get(\"labeled\",0)}, title_only={lc.get(\"title_only\",0)}, reliability={lc.get(\"reliability\",\"?\")}'  )
print(f'bug_issues: {n_issues}, bugfix_prs: {n_prs}, bugfix_commits: {n_commits}, hotfiles: {n_hotfiles}')
if result.get('bugfix_hotfiles'):
    print('Top bugfix files:')
    for h in result['bugfix_hotfiles'][:5]:
        print(f'  {h[\"file\"]} ({h[\"count\"]}回)')
"
```

**結果がある場合、ユーザーに報告する:**

```
📋 過去バグ履歴:
  ラベル体系: {bug_labels} / 信頼度: {reliability}
    (high=ラベル運用が定着 / medium=混在 / low=ラベルほぼ未使用)
  GitHub Issues: {n_issues} 件 (ラベル経由 {labeled}件 + タイトル検索 {title_only}件)
  マージ済み bugfix PR: {n_prs} 件
  bugfix コミット(6ヶ月): {n_commits} 件

🔧 バグ修正が多いファイル TOP 5:
  1. {file1} — {count1}回
  2. {file2} — {count2}回
  ...

この情報を以降のスキャンで優先度判定に使います。
```

**gh コマンドが使えない（認証なし等）場合は git log のみで進める。エラーでブロックしない。**

## Step 3: Add guard rules to CLAUDE.md — EXECUTE IMMEDIATELY (only if not exists)

init CLI が自動で CLAUDE.md に guard を注入する。CLI 出力に「CLAUDE.md に delta-lint guard を注入しました」と表示されていれば成功。

表示されていなければ手動で確認:

```markdown

## delta-lint guard
- コード変更前に `/delta-review` を実行して影響範囲を確認すること
- コード変更後は `/delta-scan` の実行を提案すること
- **delta fix**: finding の修正は必ず以下の CLI コマンドで実行すること。自分で Grep/Read/Edit してコードを手動修正してはいけない。
  ```bash
  cd ~/.claude/skills/delta-lint/scripts && python cli.py fix --repo <REPO_PATH> --ids <FINDING_IDS> -v
  ```
  このコマンドがブランチ作成→fix生成→適用→デグレチェック→commit→push→PR→ステータス更新を全自動で行う。
```

**If CLAUDE.md already exists and contains "delta-lint guard", skip this step.**

## Step 3.5: .gitignore は自動生成 — 操作不要

`stress_test.py` が `.delta-lint/.gitignore`（`*` + `!.gitignore`）を自動生成する。
プロジェクトの root `.gitignore` を編集する必要はない。

チームで地雷マップを共有したい場合は、`.delta-lint/.gitignore` を編集して必要なファイルを除外解除する。

## Step 4: 完了報告

init CLI の出力を読み取り、以下のフォーマットで報告する:

```
── δ-lint ── 初期化完了 ✅

📊 結果サマリー:
- {n_modules} モジュール, {n_hotspots} ホットスポット検出

次のステップ:
  delta-scan                    — 変更ファイルをスキャン
  delta-scan --scope wide       — 全ファイルスキャン
  delta-scan --lens stress      — ストレステスト（地雷マップ生成）
  delta-scan --lens security    — セキュリティ重点スキャン
```

## If init fails

1. Read stderr to diagnose
2. Common fixes:
   - `claude -p failed` → suggest `--backend api`
   - Timeout → try again with smaller repo scope
   - Not a git repo → tell user
3. **Auto-retry once** before reporting to user
