# DeltaLint プラグイン — skills / references レイアウト（他LLM・開発者向けハンドオフ）

この文書は **別の言語モデル（LLM）や新しい開発者**がリポジトリを読むときに、そのまま渡してよい説明です。前提知識は不要に近づけてあります。

---

## 1. 結論（壊れていないか・他LLMで問題ないか）

- **プラグインの実行ロジック**は `plugins/delta-lint/scripts/` の Python です。skills は **手順書・参照 Markdown** であり、Python の import 対象ではありません。
- 各 skill は **`SKILL.md` をルートとする小さなバンドル**です。`SKILL.md` 内のリンク `references/foo.md` は **必ずその skill ディレクトリ直下**の `references/foo.md` を指します（相対パス）。
- **重複していた** `plugins/delta-lint/skills/delta-lint/references/` は削除済みです。中身は **`delta-scan/references/` および `delta-review/references/` に既に同一内容が存在**しており、`delta-lint` 用フォルダには **`SKILL.md` が無かった**ため、Claude の skill としても読み込まれていませんでした。**削除は安全**です。
- **他の LLM** がこのリポの Markdown を読む場合も、上記の「skill ごとに自己完結」という規則だけ押さえれば **パス解決は一貫**しています。削除したディレクトリを参照する `SKILL.md` は存在しません。

---

## 2. 編集するときの正しい場所（単一の正）

| 目的 | パス（リポジトリルートから） |
|------|------------------------------|
| CLI・スキャン・findings などのコード | `plugins/delta-lint/scripts/` |
| delta-scan のワークフロー本文・詳細手順 | `plugins/delta-lint/skills/delta-scan/SKILL.md` と `plugins/delta-lint/skills/delta-scan/references/*.md` |
| delta-review | `plugins/delta-lint/skills/delta-review/SKILL.md` と `.../delta-review/references/*.md` |
| delta-fix（references なし） | `plugins/delta-lint/skills/delta-fix/SKILL.md` のみ |

**もう存在しないパス:** `plugins/delta-lint/skills/delta-lint/`（削除済み。ここを編集対象にしないこと）

---

## 3. ワークフロー文書内の `~/.claude/skills/delta-lint/scripts`

`references/*.md` や `SKILL.md` に、`cd ~/.claude/skills/delta-lint/scripts && python cli.py ...` のような例が出てきます。

- これは **ユーザー環境にプラグインがインストールされたあとのパス**を想定した例です。
- **リポジトリ内で開発するとき**は、同じコマンドを `plugins/delta-lint/scripts/` をカレントにして実行すればよいです。

LLM が「どちらのパスを使うべきか」迷った場合: **編集・検証はリポ内 `plugins/delta-lint/scripts/`**、ドキュメントの例文はそのままユーザー向けに残してよい。

---

## 4. 検証コマンド（回帰の最低ライン）

```bash
cd plugins/delta-lint/scripts && python -m pytest tests/ -q
```

---

## 5. 既知の周辺メモ（任意）

- 旧 `delta-init` 専用 skill（`skills/_archived/`）はリポジトリから削除済み。init 相当の手順は `delta-scan` のワークフロー（`references/workflow-init.md` 等）に集約されている。
- 詳細な開発規約は `plugins/delta-lint/CLAUDE.md` を参照してください。

---

## 6. 変更履歴（要約）

- **重複排除:** `skills/delta-lint/references/` を削除（内容は `delta-scan` / `delta-review` 側に既存）。
- **アーカイブ掃除:** `scripts/_archived/`、`prompts/_archived/`、`skills/_archived/` を削除。一覧は [review-plugin-cleanup-inventory.md](review-plugin-cleanup-inventory.md)。
- **意図:** 1 箇所だけを正としてメンテし、他LLM・人間の両方がパスを追いやすくする。
