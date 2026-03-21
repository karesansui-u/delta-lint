# プラグイン掃除インベントリ（他LLMレビュー用）

2026-03 時点で実施した「削除・無視対象」の洗い出しと判断根拠。二次レビューにそのまま渡せます。

## 削除済み（Git から `git rm`）

| パス | 内容 | 削除理由 |
|------|------|----------|
| `plugins/delta-lint/scripts/_archived/` | `aggregator.py`, `constraint.py` | どの `.py` からも import されていないレガシー |
| `plugins/delta-lint/scripts/prompts/_archived/` | `detect_deep.md`, `review.md` | `detector.py` 等が参照しない旧プロンプト |
| `plugins/delta-lint/skills/_archived/delta-init/` | `SKILL.md` + `references/*` | 現行は `delta-scan` に集約。skill としても未推奨 |
| （以前の作業）`plugins/delta-lint/skills/delta-lint/` | 重複 `references/` | `SKILL.md` なし・他 skill と内容重複 |

## 作業ツリーから削除のみ（未追跡 or キャッシュ）

| パス | 理由 |
|------|------|
| `plugins/delta-lint/scripts/aider/` | 空ディレクトリ。コード参照なし。`.gitignore` に追加 |
| `plugins/delta-lint/scripts/.pytest_cache/` | pytest のキャッシュ。`.gitignore` で除外 |
| `plugins/delta-lint/scripts/__pycache__/`, `scripts/tests/__pycache__/` | ビルド成果物。既存の `__pycache__/` ルールと合わせて削除のみ |

## `.gitignore` に追加したもの

- `.pytest_cache/`（リポジトリ全体）
- `plugins/delta-lint/scripts/aider/`
- `plugins/delta-lint/scripts/.delta-lint/`（ローカル実行で生成されうる作業用データ）

## 削除しなかったもの（要レビュー時のチェックリスト）

以下は **意図的に残す**。誤削除しないこと。

| パス / 種別 | 残す理由 |
|-------------|----------|
| `plugins/delta-lint/scripts/prompts/*.md`（`_archived` 以外） | `detector.py` の `load_system_prompt` 等が参照 |
| `demo.tape`, `demo_animation.py`, `intro_animation.py` | デモ・UX 用。`cmd_init` / ドキュメントで言及 |
| `plugins/delta-lint/scripts/_archived` 以外の全 `.py` | 本番 CLI・スキャン経路 |
| `plugins/delta-lint/skills/delta-scan|delta-review|delta-fix/` | 公開 skill。`SKILL.md` + `references/` がエントリ |

## 静的確認コマンド（レビュー用）

```bash
# アーカイブ参照がコードに残っていないか
rg '_archived|skills/_archived|prompts/_archived' plugins/delta-lint --glob '*.py'

# テスト
cd plugins/delta-lint/scripts && python -m pytest tests/ -q
```

## 更新したドキュメント

- `docs/skills-layout-handoff-for-llms.md` — `_archived` skill 削除に合わせて文言更新
