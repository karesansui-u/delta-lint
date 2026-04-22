<p align="center">
  <img src="assets/top_hero.png" alt="DeltaLint" width="100%">
</p>

# DeltaLint

## リグレッションバグの検出を、エンジニアリングする

**機能改修したら、想定してない箇所でバグが発生しました。**<br>
**—— DeltaLint はその「壊れる場所」を特定します。**

普通のリンターやテストは、**明示された型・規約・実行例**に強い。DeltaLint が狙うのは、その後に残る **構造矛盾**——モジュール間で暗黙に共有されていた前提の食い違いです。<br>
63 の OSS リポジトリを調査し、ソースコード検証後に 62 リポで PR / Issue 提出に値する確定バグを確認。候補 101 件を再検証し、92 件が confirmed、9 件が false positive に転落しました。

---

## 公開 OSS での外部検証

2026 年 3 月中旬の約 2 週間で、外部 OSS へ **39 件の PR** を提出し、**26 件の Issue** を報告しました。そのうち **2026-03-15〜2026-03-18 の約 69 時間**に、現在の主要マージ実績 16 件のうち **14 件の PR** を集中的に提出しています（平均 4.9 時間 / PR）。

結果として、Microsoft / Facebook / Bytedance / Sentry / coder / tRPC などの大型 OSS に **PR 16 件（12 リポ）**がマージされました。これは単なる自己評価ではなく、各プロジェクトの maintainer が既存のレビュー基準の上で「修正として妥当」と判断した外部検証です。

このスループットは、DeltaLint が構造矛盾を検出し、Claude Code が再現・修正・PR 記述を補助し、人間が最終選別と maintainer コミュニケーションを担当するワークフローによって実現しています。

この実績が示すのは、**短時間で実在バグ候補を生成し、検証し、maintainer review に通る修正まで持っていく実運用ワークフロー**の有効性です。一方で、未選別の raw detection に対する precision / recall や、既存ツールとの定量比較を示すものではありません。そこは別途、事前登録した no-cut 評価で検証すべき領域です。

> Stars は検証当時の概数です。

| 対象リポ | Stars | マージ済み PR | DeltaLint が捉えた構造矛盾 |
|---------|-------|---------------|----------------------------|
| microsoft/playwright | 70K | [#39744](https://github.com/microsoft/playwright/pull/39744) | `quality: 0` が falsy 扱いされ、上流の `quality ?? 80` と異なるデフォルトになる |
| microsoft/fluentui | 19K | [#35877](https://github.com/microsoft/fluentui/pull/35877) | slot の `onChange` が merge されず上書きされ、同種コンポーネントの契約から外れる |
| facebook/lexical | 20K | [#8235](https://github.com/facebook/lexical/pull/8235) | `getWritable()` の戻り値を使わず元ノードへ書き込み、immutability プロトコルを破る |
| bytedance/deer-flow | 31K | [#1161](https://github.com/bytedance/deer-flow/pull/1161) [#1162](https://github.com/bytedance/deer-flow/pull/1162) [#1163](https://github.com/bytedance/deer-flow/pull/1163) | Makefile ターゲット名、プロセス終了、help 文言の間で実装と運用説明がずれる |
| promptfoo/promptfoo | 16K | [#8163](https://github.com/promptfoo/promptfoo/pull/8163) [#8165](https://github.com/promptfoo/promptfoo/pull/8165) [#8182](https://github.com/promptfoo/promptfoo/pull/8182) | `0` / `null` / `"0"` など有効な境界値が「未指定」と誤判定される |
| getsentry/sentry | 43K | [#110504](https://github.com/getsentry/sentry/pull/110504) | `datetime.replace()` の戻り値を捨て、UTC 前提が実際の値へ反映されない |
| coder/code-server | 77K | [#7709](https://github.com/coder/code-server/pull/7709) | ログイン画面で設定ファイルパスを表示し、不要な環境情報を公開する |
| trpc/trpc | 37K | [#7262](https://github.com/trpc/trpc/pull/7262) | streaming batch の 2 件目以降のエラー処理が、常に 1 件目の call 情報を参照する |
| D4Vinci/Scrapling | 30K | [#201](https://github.com/D4Vinci/Scrapling/pull/201) | retry 前に request kwargs を破壊的に変更し、HTTP method が失われる |
| abhigyanpatwari/GitNexus | 17K | [#350](https://github.com/abhigyanpatwari/GitNexus/pull/350) | ドキュメントにある relation type が allowlist に存在せず、指定が黙って落ちる |
| openclaw/openclaw | 19K | [#47488](https://github.com/openclaw/openclaw/pull/47488) | webhook mode が runtime snapshot に伝搬せず、stale 判定の前提が崩れる |
| labstack/echo | 32K | [#2925](https://github.com/labstack/echo/pull/2925) | rate limiter の実装変更に対してドキュメント上の default 説明が古いまま残る |

加えて、Issue 起因マージ 2 件（dify [#33329](https://github.com/langgenius/dify/issues/33329) → [#33373](https://github.com/langgenius/dify/pull/33373)、hono [#4806](https://github.com/honojs/hono/issues/4806) → [#4807](https://github.com/honojs/hono/pull/4807)）、セキュリティ脆弱性報告 4 件、リジェクト 1 件。AGI ラボ ハッカソン 2026＠GMO Yours では 3 位に入賞しました。

---

## 既存ツールとの違い

DeltaLint は ESLint、TypeScript、SonarQube、Semgrep、CodeQL、テストスイートを置き換えるものではありません。これらは **明示された型・規約・クエリ・実行例**を検査します。DeltaLint はその上に重ねて、**どこにも明示されていないが、モジュール間で共有されている前提**を検査します。

| 層 | 代表ツール | 強い対象 | 構造矛盾で弱い理由 |
|----|------------|----------|--------------------|
| 型 | TypeScript, mypy など | 値の型・呼び出し形 | 型は正しくても、`0` / `null` / `undefined` の意味がモジュール間で違うことは見えにくい |
| lint / 品質指標 | ESLint, SonarQube など | 局所的な anti-pattern、複雑度、重複 | 1 行・1 関数としては正しいが、別モジュールの前提と矛盾するケースは規則化しにくい |
| セマンティック解析 | Semgrep, CodeQL など | 事前に書いた query、source/sink、dataflow | query に書かれていない暗黙プロトコルや、仕様・実装・文書の境界ずれは漏れやすい |
| テスト / レビュー | unit, integration, maintainer review | 既知の仕様・再現済みケース | テストされていない境界値や、レビュー範囲外の sibling path には届きにくい |
| **DeltaLint** | LLM + 構造矛盾パターン | **モジュール間の前提境界** | 既存ツールを通過した後に残る「前提の伝搬漏れ」を検査する |

例えば Playwright [#39744](https://github.com/microsoft/playwright/pull/39744) の `quality ? quality / 100 : 0.8` は、単体では正しい JavaScript です。型も構文も通り、truthy check としても自然に見えます。問題は、別の経路で `quality ?? 80` というデフォルト解決が行われているため、`quality: 0` だけ意味が分岐することでした。

Lexical [#8235](https://github.com/facebook/lexical/pull/8235) も同じです。`this.__tag` への代入は型上は成立しますが、Lexical の node 更新プロトコルでは `getWritable()` が返した writable clone に書く必要があります。バグは 1 行の構文ではなく、**オブジェクト更新プロトコルと実際の書き込み先の不一致**にありました。

このようなバグは、既存ツールが弱いからではなく、**見ている対象が違う**ために残ります。DeltaLint は既存ツールの後段で、リグレッションが生まれやすい前提境界を追加で見るためのレイヤーです。

また、DeltaLint は警告を単純な件数として扱いません。12 の OSS リポジトリを使った予備検証では、重みなし件数 `active_count` は外部 bug 指標と有意な相関を示さず（Spearman r=0.381, p=0.222）、情報量で重み付けした `δ_cal` だけが有意な正の相関を示しました（r=0.653, p=0.021）。「何件出たか」よりも、「どの種類の前提境界がどれだけ崩れているか」を見る設計です。

---

## 3つの使い方

### 1. Claude Code プラグイン（ローカル）

```bash
claude plugin marketplace add karesansui-u/delta-lint
claude plugin install delta-lint@delta-lint
```

```
delta-scan                    # 直近3ヶ月の変更ファイルを自動スキャン
regression-check              # 仕様検討〜リリース後まで使える統一デグレチェック
```

`regression-check` は起動後に対象を確認し、仕様検討・設計相談・コード差分・PR・ブランチ差分・リリース後確認のいずれかにルーティングします。コードがある場合は差分に応じて `delta-scan` を実行し、コードがまだない段階では影響範囲やテスト観点を洗い出します。

### 2. GitHub App（PR ごとに自動レビュー）

リポジトリに DeltaLint GitHub App をインストールするだけ。設定ファイル不要。

- **PR を開くと自動スキャン** → インラインコメントで指摘 + 修正方針を提示
- `/delta-scan` `/delta-review` コメントで手動トリガーも可能
- OSS リポジトリは無料

> セルフホスト版は今すぐ使用可能。Marketplace 版は公開準備中。セットアップ: **[GitHub App SETUP.md](plugins/delta-lint/app/SETUP.md)**

### 3. GitHub Actions（CI パイプライン）

```yaml
- uses: karesansui-u/delta-lint@main
  with:
    mode: review          # review | suggest | scan
    fail_severity: high   # CI を落とす閾値
```

> SARIF 出力 → GitHub Code Scanning 連携にも対応。

---

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

---

## アーキテクチャ

<p align="center">
  <img src="assets/architecture-flow.svg" alt="DeltaLint ワークフロー" width="100%">
</p>

```
PR / push / コメント
  ↓
Webhook or GitHub Actions
  ↓
scanner.scan()  ← 6パターン検出パイプライン
  ├── context 収集 → detect → verify → filter
  └── severity / scope / lens で制御
  ↓
結果出力
  ├── PR インラインコメント（GitHub App）
  ├── PR レビュー / Check Run（Actions）
  ├── SARIF → Code Scanning
  └── ダッシュボード HTML（ローカル）
```

> 詳細: **[アーキテクチャ設計書](plugins/delta-lint/README.md)** | **[モジュールマップ](plugins/delta-lint/ARCHITECTURE.md)** | **[設計判断記録（ADR）](plugins/delta-lint/docs/decisions/)** | **[インタラクティブ図](https://karesansui-u.github.io/delta-lint/docs/architecture-diagram.html)**

---

## 理論との接続

この技術は、下記の論文を応用しています。

> [構造持続の最小形式 — 制約蓄積による構造損失の最小形式 —](https://zenodo.org/records/19584667)<br>

ソフトウェア本体を構造物と捉え、仕様・コード・設定・実行順序の間で共有される前提を **維持すべき構造**として扱います。既存ツールが主に各モジュール内の局所的な正しさを見るのに対し、DeltaLint はモジュール間の前提整合性が保てる状態集合を見ます。

構造持続理論では、制約が追加されるたびに維持可能な状態集合が縮小し、その損失は `-log(残存率)` として加算されます。したがって構造矛盾の蓄積は、直感的な「警告件数の足し算」ではなく、残存する健全な選択肢を **指数関数的に**削ります。DeltaLint の δ は、この考え方をソフトウェアリポジトリの実用指標へ落としたものです。

---

## デモ

![DeltaLint Demo](plugins/delta-lint/demo.gif)

---

## 環境と自律セットアップ

- 初回の `scan` でも **対話で「インストールしますか？」とは聞かない**。不足している CLI や Python パッケージは **標準エラーに警告を出しつつ**、環境に応じて `npm` / `pip` / `brew` / `conda` などで **自動取得を試行**する。失敗したら **別バックエンドや機能スキップ**に落として処理を続ける。
- スクリプトが使う Python は **ユーザーマシン上のインタプリタ**（Claude 本体の内部環境ではない）。
- Issue/PR 連携や `delta-fix` など GitHub 操作をフルで使う場合は、**`gh` のインストールと `gh auth login`（認証済み状態）が必要**。スキャン単体は `gh` なしでも動作します。


## 情報量（nats）でいう「矛盾の重さ」— 理論の実用化

論文側では「制約の蓄積」と情報損失を結びつけています。DeltaLint のダッシュボードやスキャン完了レポートに出る **δ（デルタ）** は、その考え方を **リポジトリ単位のスカラー**に落とした実装です。ポイントだけ、やや細かめに書きます。

1. **セルごとの情報量 I（単位: nats）**
   各 (検出パターン ①〜⑥ × severity) について、校正実験（コード断片のみ vs 注釈付き）で LLM の正答率の比 `acc_A / acc_B` を取り、
   `I = -ln(acc_A / acc_B)`（改善しない場合は 0）として **「文脈を足すとどれだけ当たりやすくなるか」** を nats で表現します。
   これが **I_BASE** テーブルに格納される **キャリブレーション済み**の値です。パターン ⑦以降などテーブルにないセルは、正のセルの中央値を **fallback** として使います（便宜値であり実測ではない）。

2. **リポジトリ全体の δ_repo**
   アクティブな finding ごとに、その (pattern, severity) の I を **足し算**して
   **δ_repo = Σ I** [nats]
   とします（ストレステスト由来の特定パターンは δ の対象から除外するなど、意味が混ざらないよう分離）。
   **件数だけではない重み付け**がここに入ります。同じ 1 件でも、パターン②の medium（高い I）と、表面検出で I≈0 に近いセルでは δ への寄与が変わります。

3. **e<sup>−δ</sup> と健全性バロメータ**
   δ を「観測できていない文脈情報の蓄積（ナット単位）」とみなすと、**e<sup>−δ</sup>** は 0〜1 の **残存因子**として解釈しやすく、ダッシュボードの 🟢〜💀 の帯と対応づけています。論文の「指数関数的に効く」というメタファと、表示上の e<sup>−δ</sup> は同じ指数ファミリーです（定義は実装の [info_theory.py](plugins/delta-lint/scripts/info_theory.py) に集約）。

4. **δ_repo（全体）と δ_cal（キャリブレーション分のみ）**
   **δ_repo** は fallback を含む **合計**。**δ_cal**（`delta_repo_calibrated`）は **I_BASE に実測セルがある寄与だけ**の部分和です。
   未校正パターンが多いリポでは、fallback が δ_repo を押し上げ、**外部の事実（例: 開いている bug 系 issue 数など）との相関がぼやける**ことがあります。一方で **δ_cal はそのノイズを隔離**しやすいです。

5. **Phase 1 実証（予備）**
   複数 OSS リポに対し、外部プロキシと Spearman 相関を取った **予備研究（N=12）**では、**δ_cal だけが統計的に有意な正の相関**を示しました（δ_cal: r=0.653, p=0.021）。一方で δ_repo（全体・fallback 含む）は r=0.312, p=0.323、単純な active 件数は r=0.381, p=0.222 で有意差が出ていません（詳細・手順は [docs/phase1-delta-repo-validation.md](docs/phase1-delta-repo-validation.md)）。
   サンプルが小さいので決定打ではありませんが、**「検証・閾値の議論は δ_cal 中心」**という方針と、**「件数ではなく情報量で足す意味」**を裏付ける材料になります。

ダッシュボードでは **総 δ・δ_cal・未校正寄与の目安（割合）**を併記し、数値の読み分けができるようにしています。

## License

Apache License 2.0 — 詳細は [LICENSE](LICENSE) を参照。
