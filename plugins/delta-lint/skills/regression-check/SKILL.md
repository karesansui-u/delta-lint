---
name: regression-check
description: >
  Unified regression check across all development stages. Use when user says
  "/regression-check", "regression-check", "regression check",
  "デグレチェック", "デグレないか確認", "デグレ確認", "影響範囲確認",
  "仕様検討でデグレ確認", "リリース前デグレ", "リリース後デグレ".
  Asks the user what to check, then routes to the appropriate scan and
  returns a unified result format.
compatibility: Python 3.11+, git. macOS/Linux/Windows.
metadata:
  author: karesansui-u
  version: 0.1.0
---

# regression-check: Unified Regression Check

全開発段階で使える統一デグレチェックコマンド。  
起動したら対象を確認し、適切なスキャンを実行して統一フォーマットで結果を返す。

## 起動フロー

### Step 1: 対象を確認する

起動時に以下を聞く（選択式）：

```
regression-check を開始します。チェックしたい対象を教えてください：

1. 仕様検討　　— 実装前の影響範囲・テスト範囲の確認
2. コミット　　— 直近の変更のデグレチェック
3. PR　　　　　— PR 全体のデグレチェック
4. ブランチ差分 — 2ブランチ間のデグレチェック
5. リリース後確認 — 商用リリース後の差分チェック
```

ユーザーが自然言語で答えた場合も意図を解釈してルーティングする。  
（例: 「今書いたコード」→ 2、「feature ブランチ vs test-2023」→ 4）

---

### Step 2: 対象ごとの処理

#### 1. 仕様検討

追加情報を確認：「どんな機能・変更を検討していますか？」

処理：
- `.delta-lint/knowledge/` の知識ベースを参照
- delta-review と同様の影響範囲分析を実行

出力フォーマット：
```
regression-check 結果 ／ 仕様検討
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
影響範囲:
- <影響するモジュール・機能の一覧>

テストが必要な箇所:
- <変更によってテストが必要になる箇所>

実装・仕様を絞り込むための検討ポイント:
- <リスクを下げるための代替案・注意点>
```

#### 2. コミット

```bash
python cli.py scan --repo <repo> --scope diff
```

#### 3. PR

```bash
python cli.py scan --repo <repo> --scope pr
```

#### 4. ブランチ差分

ブランチ名を確認：「比較元ブランチと比較先（ベース）ブランチを教えてください」

```bash
python cli.py scan --repo <repo> --scope pr --base origin/<base_branch> --profile strict
```

現在のブランチが比較元でない場合は checkout を促す。

#### 5. リリース後確認

リリースブランチ名を確認：
「`release/test-2023/KINGSMAN-xxx` と `release/main-2023/KINGSMAN-xxx` のブランチ名を教えてください」

```bash
python cli.py scan --repo <repo> --scope pr --base origin/<main-release-branch> --profile strict
```

⚠️ コミット単位のチェリーピック漏れは対象外。git log での手動確認を併用すること。

---

### Step 3: 統一出力フォーマット

仕様検討以外の場合（2〜5）：

**デグレなしの場合：**
```
regression-check 結果 ／ <対象>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ デグレなし
```

**デグレの可能性がある場合：**
```
regression-check 結果 ／ <対象>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ デグレの可能性あり（N件）

- [高] <ファイルパス>:<行> → <矛盾の内容>
- [中] <ファイルパス>:<行> → <矛盾の内容>

詳細を確認するには finding ID（dl-xxxxxxxx）を共有してください。
```

findings の生のリストは表示しない。上記フォーマットに変換して返す。
