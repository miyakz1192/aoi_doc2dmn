# aoi_doc2dmn

擬人化アーキテクチャの直近ゴール「ドキュメントから決定表（DMN）を作る」の
うち、①条文抽出・関連フィルタ、②2軸分類（機械判定可能性の分類）、
③a内容生成（宣言的×ハード制約の決定表生成）を実装したリポジトリ。
③b（手続き的×ハード制約／ObligationManager向けIR）は対象外。

## ディレクトリ構成

```
aoi_doc2dmn/
├── run_eval.sh           # 評価ハーネスの起動スクリプト(Ollama疎通確認込み)
├── src/
│   ├── stage1_filter/    # ①条文抽出・関連フィルタ
│   │   ├── rule_based.py    # RuleBasedFilter(キーワード一次判定)
│   │   ├── llm_fallback.py  # LlmFallbackFilter(Ollama判定)
│   │   └── composite.py     # CompositeFilter(上記2つを合成)
│   ├── stage2_classify/  # ②軸1・軸2分類器
│   │   ├── local_llm.py     # LocalLlmAxis1Classifier / LocalLlmAxis2Classifier(Ollama)
│   │   └── vocab_flags.py   # 既知の語彙的盲点への安全網フラグ
│   ├── stage3_frontstage/ # ③a 前段(条文→IR)。第1要素: Action 正規化
│   │   ├── action_ledger.py     # Action 台帳(登録簿の action_ledger)と近さの判定
│   │   ├── modality.py          # 規範の様相の語の一覧と検出(D71・P4)
│   │   ├── action_normalizer.py # 逐語の抜き出し→閉じた選択→k回の多数決→ゲート
│   │   └── run_action_eval.py   # 正解18件を台帳を育てながら評価する(P13)
│   ├── stage3_generate/  # ③a 内容生成(IR→4点セット)
│   │   ├── ir_schema.py        # ClauseIR v2 / FactDef / ReviewGates
│   │   ├── registry.py         # Scope/Decision登録簿(層1・2・3)の読み書き
│   │   ├── wiring_generator.py # 4点セット同時生成+クロス検証C1〜C11
│   │   ├── bootstrap_aoi.py    # あおいの送信可否判定DMNを登録簿へ初期登録
│   │   ├── golden_path.py      # ゴールデンパス条文のIR構築
│   │   ├── run_ir_gold.py      # 正解IRをmanifest順に通して確かめる
│   │   ├── scenario_eval.py    # シナリオ評価の入口（知らない値はエスカレーション）
│   │   ├── dmn_engine.py       # 軽量DMN検証エンジン(reference/から移植)
│   │   └── run_golden_path.py  # 一気通貫実行スクリプト
│   └── common/
│       ├── clause_io.py     # testdata読み込み
│       └── ollama_client.py # Ollama呼び出し共通ヘルパー
├── eval/
│   ├── run_eval.py      # オラクルとの一致率を測る評価ハーネス
│   └── metrics.py       # 精度・混同行列・confidence較正
├── testdata/
│   ├── clauses/clauses_v1.json  # オラクル付きテストデータ（28件。厚労省モデル就業規則16件＋
│   │                             IPA情報セキュリティ関連規程等12件）
│   ├── clauses/clauses_frontstage_v1.json # 前段の評価用の13件（ハード制約×宣言的。
│   │                             #   ②の評価の分布を変えないため clauses_v1.json と分けた）
│   ├── ir_gold/                 # 承認済みの正解IR（5件＋manifest.json。review.status=approved）
│   ├── action_gold/             # Action 正規化の正解（18件＋台帳の出発点。P13）
│   ├── candidates/              # テストデータ拡張時の候補・レビュー記録（round3は37件・話題7件。
│   │                             #   ②の分類器の参考値つき。正解ラベルは13件だけ付与）
│   └── schema.md                # オラクルのスキーマ定義・カバレッジ表・①関連判定の基準
├── registry/
│   ├── registry.json     # 永続化した登録簿（③の入力。既存の決定表・fact台帳・Action台帳）
│   └── dmn/              # 既存のDMNモデル（<dmn_model>.dmn。行・列の追加先）
├── tests/                # pytest（クロス検証C1〜C12の失敗テスト・ゴールデンパス固定・
│   │                     #         受け入れ経路（レベル2/行追加/列追加/no_change）・
│   │                     #         初期登録した DMN の修正（D34）・正解IRのテスト）
│   └── fixtures/         # あおいの元のDMN（初期登録の入力。D42）
├── docs/design/          # ③aの設計書（IR v2設計書・前段設計書・9e66776時点のAsIs仕様書）
├── out/                  # ③の4点セット生成物（gitignore対象。再生成可能）
└── reference/
    └── README.md         # 既存実装（Claude Code生成コード）を参考にする際の置き場・方針
```

## 現状

- `testdata/clauses/clauses_v1.json`: 厚生労働省「モデル就業規則」＋IPA情報セキュリティ関連規程等
  から実条文を抽出し、①関連判定・②軸1・②軸2のオラクルラベルを付与済み（28件）
- `src/stage1_filter/`, `src/stage2_classify/`: Ollama（`qwen2.5:14b-instruct`）を使った実装済み。
  `eval/run_eval.py`の`build_filter()` / `build_axis1_classifier()` / `build_axis2_classifier()`は
  実装に接続済み
- 軸1・軸2の判定には、rationaleの根拠となる原文からの逐語引用（`evidence_quote`）を必須化し、
  実際にclause_text中に存在するかをプログラム側で検証する仕組み（`quote_verified`）がある。
  confidenceは実測で常に0.7を超え較正情報を持たないため、これを補う独立したハルシネーション検出手段
- さらに、`quote_verified`では検出できない「実在する文言を正しく引用した上での解釈ミス」型の
  既知の誤判定パターン（「認められる」「申出」「許可を得る」「承認を受ける」）には、
  `vocab_flagged`（`src/stage2_classify/vocab_flags.py`）というルールベースの第三のゲートがある
- 直近の評価結果（`--variant both`、2026年9月18日）：Stage1関連フィルタ accuracy=0.96（27/28）、
  Stage2軸1 strict_accuracy=0.87／lenient_accuracy=0.91（n=23）、軸2 strict/lenient_accuracy=0.96（n=23）。
  raw/filled間の差分は0件。strict/lenientの意味は下記「評価指標の見方」を参照
- 既知の残存課題：軸1で第60条（面接指導）・5.6（情報資産の社外持ち出し）の2件が誤判定される。
  プロンプト調整・軸1の2段階再構造化など複数回試みたが解消せず、これ以上の狙い撃ちは過学習リスクが
  高いため保留。詳細な経緯は`HANDOFF_20260818_round6_to_web.md`を参照
- 軸1・軸2分類の精度改善フェーズ（第4回〜第9回）はひと段落し、③段階（内容生成・IRスキーマ
  パイプライン）への移行フェーズに入った。詳細は`testdata/schema.md`「軸1・軸2分類の精度改善
  フェーズの区切り」節、「gold label判定の方針」節を参照
- `src/stage3_generate/`: ③a（宣言的×ハード制約）の**後半**（IR → 4点セット）は
  IR v2（`docs/design/aoi_doc2dmn_IRv2_design.md` v1.8）で実装済み。fact の定義がIRに入り
  （`facts.new_facts`）、4点すべてをIRと登録簿から生成する。クロス検証はC1〜C12で、
  それぞれの失敗テストが`tests/`にある
- v1.2 で**受け入れ経路**を実装した。新しい表を作るだけ（レベル1）でなく、既存のDMN
  モデルへの行の挿入（レベル2・中間判定への行追加）と列の追加ができる。既存の表に
  手を入れた場合は、その表を使うすべての最終判定のルーティング表とバインディング設定を
  作り直す（D16）
- v1.3 では、中間判定だけを直す条文（`final.action = no_change`）、決定表ごとの
  `option_taken`の導出（D31）、`new_output_values`の出力名付き化（D29）を実装した。
  C6 は「IRが書いた行と表で使う既存fact」と`referenced_facts`の一致を見る（D25）
- `registry/registry.json`には、あおいの送信可否判定（本人確認判定・情報分類判定・
  送信可否判定）を初期登録済み（`provenance.origin = bootstrap`）。層1が3件・層2が18件・
  層3が7件。DMNは`registry/dmn/aoi_transmission.dmn`（元のDMNに承認済みの修正2件を
  当てたもの。D34）。比較型のfactの`possible_values`はプリミティブの定義から導く
  （D59。`name_match`に「未提供」、`phone_match`に「不一致」が入る）
- v1.4 では、中間判定の新しい出力値を承認の対象にし（D37）、C9条件3を双方向の一致に
  した（D36）。`depends_on_decisions`／`depends_on_inputs`はIRから外し、`input_columns`
  からの導出にした（D38）
- v1.5 で**既定の行の扱い（U2）を決めた**。`passthrough`のfactは予約語「その他」を
  自動で持ち（D45）、`possible_values`にない値はDMNに渡さず人の確認に回す（D46。
  バインディング設定の`on_unknown_value`と`scenario_eval.py`）。あわせて**C12**を
  足した（D48）。中間判定に足した出力値を、それを参照する既存の判定が受け止められるか
  を確かめる（C11の逆向き）
- v1.6 では、Kazuhiroさんのレビュー（2026-11-02）の結果を反映した。`passthrough`のfactは
  「その他」が指す状態の注記を持ち（D55。`other_value_note`。無ければC4で止める）、
  比較型のfactの`possible_values`はプリミティブの定義から導く（D59）。既存のDMNモデルを
  指す IR は、新しい表だけを足す場合も既存ファイルに追記する（D57）。`ColumnSetRelationKind`
  に`disjoint`を足し（D58）、値の数字は半角に統一する（D56）
- v1.7 では、factの型を2つに分けた（D62。`PassthroughFact`は区分の値と「その他」の注記を
  持ち、`ComparisonFact`はどちらも持たない。JSONの形は変えていない）。「その他」が既定の行に
  落ちるシナリオは、条文単位ではなく**passthroughのfactごと**に1件以上要る（D63）
- `testdata/ir_gold/`に**承認済みの正解IR**が5件ある（`review.status = "approved"`・
  `reviewed_by`・`reviewed_at`・`decisions`。D53。IRの中の`approved_by`はNoneのまま）。
  `run_ir_gold.py`が`manifest.json`の順に配線ジェネレータへ通し、C1〜C12とDMN評価
  シナリオを確かめる（登録簿はメモリ上で持ち回り、`registry/`には反映しない。D43）。
  型で表しきれなかった条文は`testdata/ir_gold/NOT_EXPRESSIBLE.md`に記録してある
- v1.8 では、`disjoint`と`add_column`の組み合わせをC9で止め（D69）、登録簿の層3の比較型の
  factの値がプリミティブの定義から導いた値と一致するかを読み込み時に確かめる（D70）。
  `action.surface`は規範の様相（してはならない・しないこと・禁止する、「させない」の
  「ない」）を含めない（D71）。承認済みの正解IR 4件のsurfaceをこの規則で直した
- ③aの**前段**（条文 → IR）は`docs/design/aoi_doc2dmn_frontstage_design.md`（前段設計書
  v0.3）で設計し、4つの要素（Action正規化 → 事実の抽出 → 行の構成 → レベル判定）に分けて
  1つずつ自動化する（P1）。**第1要素のAction正規化を`src/stage3_frontstage/`に実装済み**。
  残り3要素は未実装で、`golden_path.py`がゴールデンパス1件のIRを手で組み立てている
- Action正規化は、Action台帳（`registry.json`の`action_ledger`。エージェントの職務記述書に
  当たる。P2・P3）の中から1つ選ぶか「該当なし」を選ぶ**閉じた選択**にする（P5）。
  不確かさは確信度の自己申告ではなく、同じ入力をk=3回実行して選択が一致するかで測る
  （P7。`action_mapping_unstable`）。抜き出した語句は条文の部分文字列か・様相の語を
  含まないかをプログラムで確かめる（P4。`action_surface_unverified`）

## 評価指標の見方

`eval/run_eval.py`が出力する`strict_accuracy` / `lenient_accuracy`（`eval/metrics.py`の
`compute_axis_metrics`）は、軸1・軸2それぞれについて以下を意味する。

- **strict_accuracy**：予測ラベルが、オラクルの主ラベル（`oracle.axis1` / `oracle.axis2`）と
  完全一致した割合。本番相当の厳密な精度指標
- **lenient_accuracy**：strictの一致に加えて、`is_ambiguous=true`の境界事例（軸1/軸2の判定が
  割れうると意図的に設計された条文）については、`alt_labels`に記録された代替ラベルと予測が
  一致した場合も正解として数えた割合。`is_ambiguous=false`の条文には代替ラベルが無いため、
  strictと同じ挙動になる

境界事例でない条文が多いデータセットではstrict/lenientの差はほぼ0になる。差が出るのは、
境界事例（現行15件中1件、第12条パワハラ）で予測が主ラベルではなく代替ラベル側に振れた場合のみ。

## 実行方法

事前条件：Ollamaが起動していて`qwen2.5:14b-instruct`がロード済みであること
（`ollama serve` / `ollama pull qwen2.5:14b-instruct`）。Python側の依存関係は
`run_eval.sh`が`.venv/`を自動作成して`requirements.txt`をインストールするため、
手動セットアップは不要（このマシンのシステムPythonはPEP 668で保護されており
pipのシステム直接インストールができないため、プロジェクトごとに`.venv/`を
作る構成にしている）。

```bash
./run_eval.sh check                # Ollama疎通確認のみ(実行しない)
./run_eval.sh                      # --variant both で実行(デフォルト。LLM呼び出し約50回、30分程度)
./run_eval.sh --variant raw        # text_raw（空欄あり）のみ
./run_eval.sh --variant filled     # text_filled（空欄埋め済み）のみ
```

スクリプトを使わず直接実行する場合は `python3 -m eval.run_eval --variant both` でも同様に動く
（`run_eval.sh`は疎通確認を追加しているだけ）。

### ③a 4点セット生成（ゴールデンパス）

```bash
.venv/bin/python3 -m src.stage3_generate.run_golden_path              # ②分類器も実際に呼ぶ
.venv/bin/python3 -m src.stage3_generate.run_golden_path --use-oracle # Ollama不要。配線部分のみ検証
```

条文 → ②2軸分類 → ③a IR構築 → 4点セット生成（クロス検証C1〜C11）→ 生成DMNの評価、までを
一気通貫で実行する。`out/golden_path/`にDMN XML・バインディング設定・ルーティング表・
登録簿が出力される。

入力となる登録簿は`registry/registry.json`（`--registry`で差し替え可能）。更新後の登録簿は
`out/`に書き出すだけで、永続台帳への反映（promotion）は人間レビューを経た別操作とする。

### あおいの送信可否判定の初期登録（再実行可能）

```bash
.venv/bin/python3 -m src.stage3_generate.bootstrap_aoi
```

`reference/`以下の既存DMNに承認済みの修正（`bootstrap_aoi.DMN_FIXES`）を当てて
`registry/dmn/aoi_transmission.dmn`に置き、そこから層1・層2・層3を機械的に起こして
`registry/registry.json`に保存する。同じ入力なら何度実行してもバイト単位で同じ結果になる。

- **元のDMNの修正（D34）**：`reference/`の元ファイルは書き換えず、`registry/`の側だけを
  直す。内容・理由・承認者（Kazuhiro）は`DMN_FIXES`の1か所にまとめ、層1・層2の`history`に
  残す。修正後の登録簿にはC5・C11を当て、違反があれば止める
- 修正していない不足・矛盾（どの決定表も参照しない中間判定の出力など）は、補わずに
  一覧で表示する
- `reference/`はリポジトリに入っていないため、初期登録を流し直すには別途配置が必要
  （生成済みの`registry/`はコミットしてある）

### 正解IRの確認（Ollama不要）

```bash
.venv/bin/python3 -m src.stage3_generate.run_ir_gold
```

`testdata/ir_gold/manifest.json`の順に正解IRを配線ジェネレータへ通し、各件が
C1〜C12を通過すること・DMN評価シナリオが一致することを確かめる。出力された登録簿を
次のIRの入力にするので、既存のfactや決定表を使う条文も確かめられる（D43）。
`registry/`は書き換えない。

### Action 正規化の評価（前段の第1要素）

```bash
.venv/bin/python3 -m src.stage3_frontstage.run_action_eval              # Ollamaで実際に流す
.venv/bin/python3 -m src.stage3_frontstage.run_action_eval --use-oracle # Ollama不要。評価の筋だけ
```

`testdata/action_gold/action_gold_v1.json`の18件（承認済みの正解IR 5件＋候補13件）を、
Action台帳を育てながら順に正規化して正解と比べる。台帳の出発点は初期登録の登録簿にある
Action 1件（あおいの外部送信）だけで、1件を正規化したら**前段の出力ではなく正解の
Action**を台帳に足してから次へ進む（教師あり。P13）。前段が1件誤ったとき、その誤りが後の
条文の選択肢に持ち越されると後の誤りの原因が分からなくなるためである。

測るのは、**誤った既存のActionへの寄せ（誤統合。0件が合格の条件。P9）**・取りこぼし・
既存か新規かの正解率・surfaceの3条件・`action_mapping_unstable`が立った件数。結果は
`out/action_eval/result.json`に出る（1件ごとに`partial.json`も書くので、途中で落ちても
結果は残る）。LLM呼び出しは1件あたり6〜7回（k=3回×2呼び出し＋「該当なし」なら候補の案）で、
1件あたり3〜10分（CPU実行。その時の負荷で3倍ほど変わる）。

初回（2026年11月7日・qwen2.5:14b-instruct）の結果は`docs/eval/action_eval_20261107.json`に
残してある。**誤統合6件で不合格**で、18件それぞれのTrace（k回ごとの抜き出し・選択・理由・
選ばなかったActionとその理由・新規候補・重複の判定）が入っている。読み方と誤りの分析は
`HANDOFF_20261108_round30_to_web.md`の6節・7節。

### テスト

```bash
.venv/bin/python3 -m pytest tests/ -v
```

Ollamaは不要。6つのファイルに分かれている。

- `test_cross_validation.py`：クロス検証C1〜C12の失敗テスト（わざと違反させたIRで
  `SchemaWiringError`になること）
- `test_golden_path.py`：ゴールデンパスがC1〜C12を通過しDMN評価4/4になること、承認者を
  入れるとレビューゲートが下りること
- `test_acceptance_paths.py`：初期登録したあおいの登録簿を使い、レベル2・中間判定への
  行追加・列の追加・no_change（第901〜906条）が通ること（挿入後のDMNの評価シナリオ込み。
  既定の行に落ちるケース・既存モデルへの追記（D57）を含む）
- `test_bootstrap_fixes.py`：初期登録したDMNの修正（D34）。修正を外すとC11で止まること、
  修正の前後で評価結果が変わらないこと
- `test_ir_gold.py`：正解IRが全件C1〜C12を通りシナリオが一致すること、evidenceの根拠が
  逐語か正規化であること、値の数字が半角であること（D56）、承認がreviewに記録され
  IRの中の`approved_by`はNoneのままであること（D53）、「その他」が既定の行に落ちる
  シナリオがpassthroughのfactごとにあること（D63）、`registry/`を書き換えないこと、
  `action.surface`が条文の部分文字列で様相を含まないこと（D71）
- `test_action_normalizer.py`：前段のAction正規化。**LLMは固定の応答を返す偽物に差し替える**
  （逐語の確認・様相の検出・k回の多数決・4つのゲート・教師ありの台帳の更新・誤統合と
  取りこぼしの集計）

元のDMNと第14条の条文本文は`tests/fixtures/`にコミットしてあるため（D42）、
`reference/`が無い環境でも全件が動く（skipは0件）。

空欄（`［　］`）の扱い方針は`testdata/schema.md`「空欄の扱い方針」節を、
①関連判定の基準（解釈A/B）は同ファイル「①関連判定の基準」節を参照。

