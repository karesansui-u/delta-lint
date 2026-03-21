<!-- delta-lint:auto-start -->
## delta-lint guard

- **scan 完了後、`found` が 0 になるまで全件調査・ステータス更新を自律完走すること。** ユーザーの指示を待たない。
- コード変更前に `/delta-review` を実行して影響範囲を確認すること
- コード変更後は `/delta-scan` の実行を提案すること
- **delta fix**: finding の修正は必ず以下の CLI コマンドで実行すること。自分で Grep/Read/Edit してコードを手動修正してはいけない。
  ```bash
  cd ~/.claude/skills/delta-lint/scripts && python cli.py fix --repo <REPO_PATH> --ids <FINDING_IDS> -v
  ```
  このコマンドがブランチ作成→fix生成→適用→デグレチェック→commit→push→PR→ステータス更新を全自動で行う。
<!-- delta-lint:auto-end -->
