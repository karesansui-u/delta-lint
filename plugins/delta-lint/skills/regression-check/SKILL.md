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

## 基本モデル

全段階で共通の処理軸：

```
入力
  1・2（仕様検討・設計相談）→ ユーザーが考えていること（仕様・設計のアイデア）
  3〜10（コードあり）       → 差分ファイル（git diff）

処理（全段階共通）
  → .delta-lint/knowledge/rules/design_review.md の観点でチェック
  → 過去バグ・インバリアント（knowledge/）との照合
  → 3〜10 はさらに差分ファイルの矛盾チェック（deepスキャン）も実行

出力
  1・2 → 考慮すべき点・影響範囲・テストが必要な箇所
  3〜10 → デグレの可能性有無・レポート
```

---

## 起動フロー

### Step 1: 対象を確認する

起動時に以下を聞く（選択式）：

```
regression-check を開始します。チェックしたい対象を教えてください：

1. 仕様検討　　　　— 実装前の影響範囲・テスト範囲の確認
2. 設計相談　　　　— 設計イメージのデグレ・考慮漏れ確認
3. コード差分チェック — コミット前・後の変更のデグレチェック
4. PR　　　　　　— PR 全体のデグレチェック
5. ブランチ差分　　— 2ブランチ間のデグレチェック
6. リリース後確認　— 商用リリース後の差分チェック
```

ユーザーが自然言語で答えた場合も意図を解釈してルーティングする。  
（例: 「今書いたコード」→ 3、「feature ブランチ vs test-2023」→ 5）

---

### Step 2: 対象ごとの処理

#### 1. 仕様検討 / 2. 設計相談

追加情報を確認：「どんな機能・変更を検討していますか？」

処理：
- `.delta-lint/knowledge/` の全ファイルを参照（rules/design_review.md・bugs/・invariants.md 含む）
- 考えている内容を design_review.md の7観点でチェック
- 過去バグ・インバリアントと照合して考慮漏れを指摘

出力フォーマット：
```
regression-check 結果 ／ 仕様検討
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
影響範囲:
- <影響するモジュール・機能の一覧>

テストが必要な箇所:
- <変更によってテストが必要になる箇所>

検討ポイント（design_review.md 観点）:
- <変数・関数 / フック / DB / ACF・ACP / JS・CSS / メモリ / クエリ数 で影響ありの項目>

考慮すべき過去バグ・ルール:
- <関連する invariants / bugs からの注意点>
```

#### 3. コミット

```bash
python cli.py scan --repo <repo> --scope diff
```

#### 4. PR（通常）

```bash
python cli.py scan --repo <repo> --scope pr
```

#### 4b. 修正PR（マージ済みの関連PRがある場合）

修正PR は既にマージされた関連PRの内容と合わせて評価する。
- 直近マージされたコミット（`git log --merges -10` 等）を確認し、関連する変更を把握する
- その上で修正差分をスキャンし、既存の変更との整合性も確認する

```bash
python cli.py scan --repo <repo> --scope pr --depth deep
```

#### 5. ブランチ差分

ブランチ名を確認：「比較元ブランチと比較先（ベース）ブランチを教えてください」

```bash
python cli.py scan --repo <repo> --scope pr --base origin/<base_branch> --profile strict
```

現在のブランチが比較元でない場合は checkout を促す。

#### 6. リリース後確認

リリースブランチ名を確認：
「`release/test-2023/KINGSMAN-xxx` と `release/main-2023/KINGSMAN-xxx` のブランチ名を教えてください」

```bash
python cli.py scan --repo <repo> --scope pr --base origin/<main-release-branch> --profile strict
```

⚠️ コミット単位のチェリーピック漏れは対象外。git log での手動確認を併用すること。

---

### Step 3: 統一出力フォーマット（3〜6）

スキャン結果は以下フォーマットに変換して返す。findings の生のリストは表示しない。

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
