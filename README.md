<p align="center">
  <img src="assets/top_hero.png" alt="DeltaLint — OSS貢献実績" width="100%">
</p>

# DeltaLint

## リグレッションバグの検出を、エンジニアリングする

**機能改修したら、想定してない箇所でバグが発生しました。**<br>
**—— DeltaLint はその「壊れる場所」を特定します。**

## インストール

```bash
claude plugin marketplace add karesansui-u/delta-lint
claude plugin install delta-lint@delta-lint
```

## 本ハッカソン OSS貢献実績<br>(7日間) 2026/3/13〜3/20
**★PRマージ 14件（10リポ）**

| 対象リポ | Stars | 結果 |
|---------|-------|------|
| microsoft/playwright | 70K | PRマージ 1件 — [#39744](https://github.com/microsoft/playwright/pull/39744) |
| microsoft/fluentui | 19K | PRマージ 1件 — [#35877](https://github.com/microsoft/fluentui/pull/35877) |
| facebook/lexical | 20K | PRマージ 1件 — [#8235](https://github.com/facebook/lexical/pull/8235) |
| bytedance/deer-flow | 31K | PRマージ 3件 — [#1161](https://github.com/bytedance/deer-flow/pull/1161) [#1162](https://github.com/bytedance/deer-flow/pull/1162) [#1163](https://github.com/bytedance/deer-flow/pull/1163) |
| promptfoo/promptfoo | 16K | PRマージ 3件 — [#8163](https://github.com/promptfoo/promptfoo/pull/8163) [#8165](https://github.com/promptfoo/promptfoo/pull/8165) [#8182](https://github.com/promptfoo/promptfoo/pull/8182) |
| getsentry/sentry | 43K | PRマージ 1件 — [#110504](https://github.com/getsentry/sentry/pull/110504) |
| coder/code-server | 77K | PRマージ 1件 — [#7709](https://github.com/coder/code-server/pull/7709) |
| trpc/trpc | 37K | PRマージ 1件 — [#7262](https://github.com/trpc/trpc/pull/7262) |
| D4Vinci/Scrapling | 30K | PRマージ 1件 — [#201](https://github.com/D4Vinci/Scrapling/pull/201) |
| abhigyanpatwari/GitNexus | 17K | PRマージ 1件 — [#350](https://github.com/abhigyanpatwari/GitNexus/pull/350) |

**PRマージ 14件（10リポ）** / Issue起因マージ 2件（dify 133K, hono 29K） / セキュリティ脆弱性報告 4件 / リジェクト 1件

![DeltaLint Demo](plugins/delta-lint/demo.gif)



## 使い方

```
delta-scan                    # 直近3ヶ月の変更ファイルを自動スキャン
delta-view                    # ダッシュボードを表示
```

### 自律的なリグレッションバグ検知ワークフロー

<p align="center">
  <img src="assets/architecture-flow.svg" alt="DeltaLint ワークフロー" width="100%">
</p>

> 詳細: **[アーキテクチャ設計書](plugins/delta-lint/README.md)** | **[モジュールマップ](plugins/delta-lint/ARCHITECTURE.md)** | **[設計判断記録（ADR）](plugins/delta-lint/docs/decisions/)** | **[インタラクティブ図](https://karesansui-u.github.io/delta-lint/docs/architecture-diagram.html)**


### 環境と自律セットアップ（デモ向け）

- 初回の `scan` でも **対話で「インストールしますか？」とは聞かない**。不足している CLI や Python パッケージは **標準エラーに警告を出しつつ**、環境に応じて `npm` / `pip` / `brew` / `conda` などで **自動取得を試行**する。失敗したら **別バックエンドや機能スキップ**に落として処理を続ける（例: `gh` が無いときは Issue/PR 連携だけオフ）。
- **ハッカソン審査の「一度命じたら最後まで」「手間ゼロ」** と相性がよい動き。なお、スクリプトが使う Python は **ユーザーマシン上のインタプリタ**（Claude 本体の内部環境ではない）。
- **注意**: `gh` が未認証のときに `gh auth login --web` が走ると、**ブラウザでのログイン**が一度挟まることがある。デモを安定させるなら、事前に `claude` CLI が PATH に通っている状態にしておくとよい。企業端末などポリシーが厳しい環境では、勝手なパッケージ取得が許可されない場合がある。
- **審査員・審査環境の方へ**: Issue/PR 連携や `delta-fix` など GitHub 操作をフルで確認される場合は、**`gh` のインストールと `gh auth login`（認証済み状態）が必要**になることがあります。お手数ですが、事前のログインにご協力ください（スキャン単体は `gh` なしでも動作します）。

### 修正 → PR

```
delta-fix // 優先度上位3件を自動修正→PR
delta-fix --issue xx // GitHub Issue から修正PR作成
delta-fix --ids dl-xxxxxxxx // 特定の dl-ID を修正
```

## 6つの検出パターン

| # | パターン | 例 | なぜ起きる |
|---|---------|---|-----------|
| ① | **非対称デフォルト** | 会員登録で名前を空欄にするとプロフィール画面に「null」と表示される | 保存と表示で「空」の扱いが違う<br>*← 入力パスと出力パスでデフォルト値が非対称* |
| ② | **意味的不一致** | 注文ステータス「0」が画面Aでは「未処理」、画面Bでは「キャンセル済」になる | 同じ名前が場所によって別の意味<br>*← 共有名の意味がモジュール間で暗黙に分岐* |
| ③ | **仕様乖離** | 「全APIに認証必須」と書いてあるが認証なしで叩けるAPIがある | ドキュメントと実装がズレている<br>*← 仕様と実装の同期が手動依存で維持されない* |
| ④ | **ガード欠落** | 新規投稿にはXSS対策があるが編集画面にはない | 片方のパスだけチェックが抜けている<br>*← 並行パスへのガード伝搬が保証されない* |
| ⑤ | **設定干渉** | セッション有効期限30分 × 自動保存間隔40分で下書きが毎回消える | 独立に見える設定が裏で矛盾する<br>*← 設定間の暗黙の制約が文書化されていない* |
| ⑥ | **順序依存** | ログイン直後だけ「あなたへのおすすめ」が他人のデータで表示される | 特定の条件で実行順が入れ替わる<br>*← 実行順序の前提がコードに明示されていない* |

共通する原因：**開発者はスコープを絞って作業する**。機能Aを修正してテストが通れば完了。しかし機能Aと暗黙の前提を共有する機能Bが壊れていないかは、誰もチェックできていない可能性がある。DeltaLint はこの「スコープ外」を狙って検出する技術。

## なぜこれがこんなに機能するのか(背景技術)

この技術は、下記の論文を応用したカタチです<br>
[Structural Collapse as Information Loss: The Exponential Decay Mechanism under Accumulating Constraints](https://zenodo.org/records/19254667)（[PDF](assets/Information_loss.pdf)） <br>
※東大松尾研OBの方に査読してもらいました<br>

構造矛盾（δ）が蓄積するとシステムの生存ポテンシャルは **S = N_eff · (μ/μ_c) · e^{-δ}** に従って**指数関数的に**崩壊する（足し算ではなく掛け算）という理論です。

- **LLM 11モデル・5ベンダー（4B〜70B+パラメータ）** での制御実験で、構造矛盾と文脈マージンの乗法的相互作用を確認
- **SAT問題での数学的検証** により、e^{-δ} が解の個数の第一モーメントと数学的に一致することを証明
- **Lean 4 による形式検証**（16モジュール・160命題、sorry = 0）で数学的性質を機械証明

ソフトウェアを構造物と捉え、コード内の矛盾（非対称デフォルト、ガード欠落など）を情報損失 δ として測定・検出するのが DeltaLint の技術基盤です。普通のリンターやテストでは見つからない「スコープ外」のバグが見つかります。

## License

Apache License 2.0 — 詳細は [LICENSE](LICENSE) を参照。
