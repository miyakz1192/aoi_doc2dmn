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
│   │   ├── modality.py          # 様相の語の一覧と検出(D71・P4)、括弧の除去(P24)
│   │   ├── action_normalizer.py # 逐語の抜き出し→閉じた選択→検査→ゲート(k=1)
│   │   ├── run_action_eval.py   # 正解を台帳を育てながら評価する(P13。dev / holdout)
│   │   └── recompute_metrics.py # 済んだ評価の生データから指標を計算し直す(LLM 不要)
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
│   ├── ir_gold/                 # 承認済みの正解IR（5件＋manifest.json＋action_ledger.json
│   │                             #   （評価で使うActionの段と親。D73）。review.status=approved）
│   ├── action_gold/             # Action 正規化の正解（18件＋台帳の出発点。P13。v1とv2）
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
- v1.9 では、**Action台帳を2段にした**（D73。`kind` = concrete / abstract と`parent`）。
  親のActionの判定の禁止・条件付き許可は子に及び、許可は及ばない（D74。U13の決着）。
  ルーティング表の各行は祖先の判定を`inherited_decisions`に持ち、`required_facts`と
  バインディング設定に継承する判定の事実も入る（D75）。抽象的なActionを target にした
  IRは自分の行を作らず、子孫の具体的な行為の行を作り直す（D76）。合成の規則
  （禁止＞条件付き許可＞許可）は`scenario_eval.compose_decisions`にあり、
  `"composition": true`のシナリオが正解IRで確かめる
- v1.10 では、合成の厳しさの順序を**禁止 ＞ エスカレーション ＞ 条件付き許可 ＞ 許可**に
  した（D77）。禁止が確定しているなら人に尋ねることはないためで、知らない結論は最上位では
  なくエスカレーションと同じ厳しさにする。`ClauseIR.action.parent`は**廃止**し、Actionの
  親子関係はAction台帳だけが持つ（D78。古いIRのparentは無視して`ir_load_warnings()`に
  警告を残す）。v1.9の実装判断5件を設計として追認した（D79）
- v1.11 では、**Actionの名前を行為の種類（動詞を体言にした語）だけにした**（D80）。対象・
  権限・場所・宛先・副詞の手段（「口頭で」）は名前に入れず、決定表の列にする。同義語
  （送信と転送、共有と共用、使用と利用）は1つにまとめ、使役は別の行為として残す。
  既存の名前を改めた（D82：外部送信・データ送信→送信、特権操作→操作、施設物品使用→使用、
  情報提供→提供、口頭伝達→伝達、アカウント共用→共有、データ取り出し→取り出し）。
  変わるのはルーティングのキーだけで、DMNのモデル名・判定ID・判定名・reason_codeは
  変えない。1つのactor × actionに**判定が複数付いてよい**（D81）。実行時は付いた判定
  すべてと継承する判定を評価し、D77の順序で合成する（`scenario_eval.evaluate_request`、
  行は`Registry.routes_for_request`が層1から作る）
- ③aの**前段**（条文 → IR）は`docs/design/aoi_doc2dmn_frontstage_design.md`（前段設計書
  v0.7）で設計し、4つの要素（Action正規化 → 事実の抽出 → 行の構成 → レベル判定）に分けて
  1つずつ自動化する（P1）。**第1要素のAction正規化を`src/stage3_frontstage/`に実装済み**。
  残り3要素は未実装で、`golden_path.py`がゴールデンパス1件のIRを手で組み立てている
- Action正規化は、Action台帳（`registry.json`の`action_ledger`。エージェントの職務記述書に
  当たる。P2・P3）の中から1つ選ぶか「該当なし」を選ぶ**閉じた選択**にする（P5）。
  台帳は2段（具体的な行為／抽象的な行為）で、条文は動詞の抽象度に合った段に寄せる（P14）
- v0.4（第31回）で、正規化は**1回だけ実行する**（k=1。P7）。初回評価で、誤り7件が
  すべてk=3回とも同じ答えだったため、自己一貫性を廃止した。代わりにプログラムで
  観測できる検査を足した。
  - 末尾の様相の語は**プログラムが削る**（P4。`modality.strip_modality`）。削った語句が
    条文の部分文字列でなければ`action_surface_unverified`
  - 行為の語句の候補が2つ出たら`action_surface_ambiguous`（P17）
  - 既存に寄せたとき、語句の**中心の動詞**が、その Action の過去の語句の動詞に無ければ
    `action_verb_mismatch`（P15）。抽象的な段を選んだら`action_abstract_selected`
- v0.5（第33回）で、ゲートを**疑い**（surface_unverified・surface_ambiguous・
  verb_mismatch・possible_duplicate・name_rule）と**設計上の確認**
  （new_action_unapproved・action_abstract_selected）に分けた（P21・P22。一覧は
  `ir_schema.SUSPICION_GATES` / `DESIGN_CHECK_GATES`）。誤報は疑いのゲートについてだけ
  数え、合格の条件と並べて**人に回る割合**と**疑いのゲートの適合率**を報告する。
  部分文字列の照合の前に、条文と語句の両方から丸括弧とその中身を取り除く
  （P24。`modality.strip_parentheses`）。1文に複数の行為がある条文は正解を並べ、
  どれを出力しても正しいとする（P25。正解ファイルの`expected_alternatives`）
- v0.6 で、Action正規化は**動詞が同じ（または同義の）行為を、対象が違っても同じActionに
  寄せる**（P26）。選択と新規候補のプロンプトにこの原則を書いた。難しさはActionの選択から、
  対象を正しく列に出せるか（前段の2番目の要素、事実の抽出）へ移る（PU11）
  - 新規候補の名前・定義に規範の様相があれば`action_name_rule`（P16）
- v0.7（第38回）で、**寄せ方を動詞の規則にした**（P27〜P30）。第37回の保留評価では、
  定義を読んで「一番近い既存」を選んだ結果、誤統合8件のうち5件が抽象の「提供」への寄せ
  だった。手順は次の4つになり、LLMは**抜き出しと同義語の判定だけ**を行う。
  - **3a 動詞を比べる**（プログラム）：中心の動詞が台帳の過去の動詞
    （`surfaces[].core_verb`）と一致するときだけ既存に寄せる。一致が1つならその Action、
    2つ以上なら`action_surface_ambiguous`で人に回す
  - **3b 抜き出しを確かめる**（プログラム。P29）：抜き出した動詞のほかに台帳の動詞が
    条文に現れたら`action_surface_ambiguous`
  - **3c 同義語を判定する**（LLM）：3aで一致が無いときだけ呼ぶ。中心の動詞と、台帳の
    名前・過去の動詞だけを渡し、**定義も条文本文も渡さない**（W1）。同義語で寄せたら
    `action_synonym_merge`（設計上の確認。誤報に数えない。W2）
  - **4 新規候補**：3cでも無ければ名前・定義・段の案（従来の手順5）
  - **使役の有無が違う動詞は一致としない**（P28）。「就業」と「就業させる」は別の行為。
    台帳の過去の動詞は使役を付けたまま記録する
  - `action_verb_mismatch`は**廃止**（P30）。動詞が合わないまま寄せるのは同義語の判定で
    寄せた場合だけなので、`action_synonym_merge`に置き換えた。廃止したゲートの名前と
    当時の種類は`ir_schema.RETIRED_GATES`に残し、過去の評価の記録を読み直すのに使う
  - 評価に**新規の取り違え**（`new_mismatch`・重さは中）を足した。正解も出力も新規の
    ときは、出力の名前の動詞が正解の名前か中心の動詞と一致するかを見る（第37回 穴1）
- 合格の条件は「**ゲートをすり抜けた重大な誤り（誤統合・適用漏れ）が0件**」（P9）。
  14Bのローカルモデルに誤りを0件にさせるのではなく、疑わしいものを必ず人に回す

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

Action台帳の段と親は`testdata/ir_gold/action_ledger.json`から読み、初期登録の登録簿に
マージしてから始める（D73）。情報提供（抽象）を target にした人的対策-2 は自分の行を作らず、
子の外部送信（あおい）の行に`inherited_decisions`として入る（D76）。`"composition": true`の
シナリオが、継承した判定との合成（禁止＞条件付き許可＞許可）を確かめる（D74・D75）。

### Action 正規化の評価（前段の第1要素）

```bash
.venv/bin/python3 -m src.stage3_frontstage.run_action_eval              # Ollamaで実際に流す
.venv/bin/python3 -m src.stage3_frontstage.run_action_eval --use-oracle # Ollama不要。評価の筋だけ
```

`testdata/action_gold/action_gold_v3.json`の18件（承認済みの正解IR 5件＋候補13件。
v0.6の名前）を、Action台帳を育てながら順に正規化して正解と比べる。台帳の出発点は初期登録の
登録簿にあるAction 1件（あおいの送信。旧：外部送信）だけで、1件を正規化したら**前段の出力ではなく正解の
Action**を台帳に足してから次へ進む（教師あり。P13）。前段が1件誤ったとき、その誤りが後の
条文の選択肢に持ち越されると後の誤りの原因が分からなくなるためである。

測るのは、誤りの4分類（**誤統合**・**適用漏れ**＝重大、過剰適用＝中、取りこぼし＝軽）と、
**そのうちゲートが1つも立たなかった件数（すり抜け。0件が合格の条件。P9）**、
ゲートの誤報（疑いのゲートだけ）、既存か新規かの正解率、surfaceの3条件、そしてv0.5で
足した**人に回る割合**・**疑いのゲートの適合率**・**新規候補の段の一致率**・
**新規候補の一覧**（P21・P23）である。結果は
`out/action_eval/result.json`に出る（1件ごとに`partial.json`も書くので、途中で落ちても
結果は残る）。LLM呼び出しは1件あたり2〜3回（k=1。抜き出し＋選択＋「該当なし」なら候補の案）。

初回（2026年11月7日・qwen2.5:14b-instruct・k=3・前段v0.3）の結果は
`docs/eval/action_eval_20261107.json`に残してある。**誤統合6件で不合格**だった。読み方と
誤りの分析は`HANDOFF_20261108_round30_to_web.md`の6節・7節。

2回目（2026年11月8日・同じモデル・k=1・前段v0.4・正解はaction_gold_v2）の結果は
`docs/eval/action_eval_20261108.json`で、**すり抜け0件で合格**（正しい13件／誤統合2件・
過剰適用2件・取りこぼし1件、いずれもゲートあり）。様相をプログラムで削ったことで
surfaceの3条件が8/18→16/18に上がり、LLM呼び出しは114回→44回に減った。分析は
`HANDOFF_20261108_round32_to_web.md`の5節・6節。どちらのファイルにも18件それぞれの
Trace（抜き出し・選択・理由・選ばなかったActionとその理由・新規候補・重複の判定）が入っている。

#### 開発用と保留用（P20）

評価に使う条文は**開発用**（設計を直すときに見てよい）と**保留用**（設計を固めてから
1回だけ流し、結果を見て直さない）に分ける。正解ファイルの`_meta.split`が`dev`か`holdout`で、
評価の出力の`_meta.split`にも書く。

| 正解ファイル | split | 件数 | 台帳の出発点 |
|---|---|---|---|
| `testdata/action_gold/action_gold_v3.json` | dev | 18 | 初期登録の1件（あおいの送信） |
| `testdata/action_gold/action_gold_v4.json` | dev | 18 | 同じ（v3の中心の動詞に使役を付けた3件だけが違う。P28） |
| `testdata/action_gold/action_gold_holdout_v1.json` | holdout | 14 | 開発用18件を足し終えた状態（11件） |
| `testdata/action_gold/action_gold_dev2_v1.json` | dev | 14 | 同じ（`_meta.ledger_source`が`seed`。v4の18件を足し終えた状態） |

v0.7（第38回）からは**v4**（18件）と**dev2**（14件）が開発用である。dev2は第37回で
1回使った保留用を写したもので、もう保留用には使えない（P20・W3）。元の
`action_gold_holdout_v1.json`は第37回の記録としてそのまま残す。新しい保留用の候補は
`testdata/candidates/candidates_round4.json`（30件）にあり、**前段を流しておらず、
正解の案も②の分類器の結果も付けていない**（第38回パートE）。

`action_gold_v1.json`（第30回）と`action_gold_v2.json`（第32回）は、過去の評価の記録として
旧い名前のまま残してある。v3はv2の名前・段・親・定義をD82の対応表で改めたもので、
外部送信とデータ送信を「送信」にまとめたため、**項目9（転送）の正解が既存の送信になった**。

保留用の正解は第33回追補 4章の表のとおりで、14件すべてKazuhiroさんが承認済み（2026-11-20）。
番号は第33回のまま（9は欠番）で、外した10件は`_meta.excluded`に理由とともにある。
**正解の値が入っていない項目か、`review.approved_by`が入っていない項目が1件でもあれば
`run_action_eval`が止まる**（第33回4.3節。誤って未確定の正解で流すと、その条文は二度と
保留用に使えない）。保留用の出発点の台帳は`ledger_seed`に固定してあり、`action_gold_v3`から
作り直して一致することを`tests/test_action_gold_holdout.py`が確かめる。

**保留評価は1回だけ流す**（運用メモの止まる条件 S2。実行の直前で止まり、Kazuhiroさんの
「はい」を待つ）。進め方の約束は`docs/design/aoi_doc2dmn_operating_rules.md`にある。

保留評価の結果（2026年11月21日・qwen2.5:14b-instruct・k=1・前段v0.6・実行コミットは
`23b70ec`と同一内容の`72b2a26`）は`docs/eval/action_eval_holdout_20261121.json`で、
**すり抜け0件で合格**。ただし正しいのは6/14（中身では5/14。下の穴1）で、誤統合8件のうち5件は
抽象の「提供」への寄せだった。疑いのゲートの適合率は8/12（67%）で、同義語の表が無くても
verb_mismatchの誤報は11件中3件だけだった。所要時間は55分（1件236秒・LLM 29回）。
分析は`HANDOFF_20261121_round37_to_web.md`。11/20に11/14で中断した分は
`docs/eval/action_eval_holdout_20261120_interrupted.json`に残してあり、通し直した14件と
判定が完全に一致した（temperature 0.0の再現性）。

保留評価で見つかった穴（第37回 5節。直し方は要判断・一目確認に出してある）：

| # | 穴 | 今の状態 |
|---|---|---|
| 穴1 | `_matches`は「出力が新規なら、正解も新規であれば一致」とするため（名前の付け方には幅があるという5.1節の考え方）、**別概念の新規でも「正しい」と数える**。項目10は正解が送信／受信なのに出力は暗号化で「正しい」になった | 「正しい」の数え方の問題。すり抜けの判定には影響しない |
| 穴2 | **抽象の「提供」が吸い込む**。誤統合8件中5件（発行・取り出し・保存・撮影・取得）。プロンプトに「既存の抽象より具体の新規を優先」の指示が無い。項目5は正解の既存（取り出し）が台帳にあったのに提供を選んだ | 5件すべて`action_abstract_selected`（設計上の確認）が立つので人に回る |
| 穴3 | 台帳に**向き・反意の考えが無い**。持込み→持出し、離席→立入り、同伴→立入り。項目13は理由の文で「『立去り』に該当するが既存では『立入り』が最も近い」と自覚したうえで寄せている | 3件とも`action_verb_mismatch`が立つ |
| 穴4 | 出力のsurfaceが**条文を書き換えた**。項目1の条文は「1名につき1つを発行する」だが、surfaceは「2つ以上を発行する」だった | `action_surface_unverified`が立つ（P14の検査が効いた） |
| 穴5 | surfaceの「様相なし」は**打ち消しを含む語句で構造的に立つ**。項目14（「関係のない情報」）に加え、項目15（「望まない者」）も同じ理由で落ちた | 合否には影響しない |

誤りの型が開発用（過剰適用・取りこぼし中心）から保留用（誤統合中心）へ移ったのは、
**台帳が育つほど「既存で間に合わせる」方へ寄る**ことを示している。

### v0.7（第38回）の開発用32件の結果

前段を動詞の規則（P27〜P30）に作り替えてから、開発用のv4（18件）とdev2（14件・第37回の
旧保留用）を流した（2026年11月21日・qwen2.5:14b-instruct・k=1・教師あり）。結果は
`docs/eval/action_eval_v4_20261121_v07.json`と`docs/eval/action_eval_dev2_20261121_v07.json`。

| | 件数 | 正しい | 誤統合 | 適用漏れ | 過剰適用 | 取りこぼし | 新規の取り違え | すり抜け |
|---|---|---|---|---|---|---|---|---|
| 第32回（開発用・v0.4） | 18 | 13 | 2 | 0 | 2 | 1 | 0 | 0 |
| v4（開発用・v0.7） | 18 | 12 | 2 | 1 | 2 | 1 | 0 | **0** |
| 第37回（旧保留用・v0.6） | 14 | 5 | 8 | 0 | 0 | 0 | 1 | 0 |
| dev2（旧保留用・v0.7） | 14 | **10** | **1** | 0 | 0 | 0 | 3 | **0** |

読み取れること（分析は`HANDOFF_20261122_round39_to_web.md`）。

- **旧保留用の誤統合が8件→1件に減った。** 抽象の「提供」が吸い込む穴2 は、動詞が一致しない
  かぎり寄せない形にしたことで大きく減った。
- **自動で寄る経路（手順3a）で誤ったのは1件だけで、止めたのはP29（手順3b）だけだった。**
  v4項目6は動詞が台帳の「使用」と一致してゲート無しで確定する経路に入ったが、条文に台帳の
  別の動詞「操作」が残っているのを3bが見つけた。P29が無ければすり抜けの1件目だった。
- **残る誤りはほぼ全部が手順3c（同義語の判定）**で、11件中6件が誤り。理由はどれも
  「同じ種類の行為だから」「〜を含むから」だった（PU12）。
- プロンプトの文面を直す試み（規則の言い直し・含む関係の禁止）は**効かなかった**。dev2は
  判定が1件も変わらず、v4は正しいが12→11に減った。LLMが足した規則を名指しで否定してから
  同じ寄せをするようになったため、文面は直す前に戻した。記録は
  `docs/eval/action_eval_{v4,dev2}_20261122_fix1.json`。

```bash
# 済んだ評価の生データから、v0.5 の指標を計算し直す（LLM は回さない）
.venv/bin/python3 -m src.stage3_frontstage.recompute_metrics
```

`--reclassify-v3 testdata/action_gold/action_gold_v3.json`を付けると、第32回の出力の名前を
D82で読み替えてv3の正解で分類し直す（結果は`docs/eval/action_eval_20261108_reclassified_v3.json`。
4分類は1件も変わらない）。

第32回の生データに当てた結果は`docs/eval/action_eval_20261108_metrics_v05.json`にある
（人に回ったのは15/18件・自動で通ったのは3件、疑いのゲートの適合率5/9、新規候補の段の
一致5/7、P24を当てると`action_surface_unverified`が1件→0件）。

### テスト

```bash
.venv/bin/python3 -m pytest tests/ -v
```

Ollamaは不要。9つのファイルに分かれている。

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
  （様相の削り落とし・逐語の確認・候補2つ・動詞の一致の検査・7つのゲート・名前と定義の
  規則・教師ありの台帳の更新・誤りの4分類）
- `test_action_hierarchy.py`：Action台帳の2段化と禁止の継承（D73〜D79）。抽象的なActionが
  行を作らないこと、継承する判定の事実がrequired_factsに入ること、合成で継承側の許可が
  無視されること、厳しさの順序が禁止＞エスカレーション＞条件付き許可＞許可であること（D77）
- `test_action_gold_holdout.py`：保留評価セットの仕組み（P20・5.4節）。出発点の台帳が
  開発用18件（v3）を足し終えた状態と一致すること、正解の値が無い・承認されていない項目が
  あるかぎり評価が止まること、複数の行為を含む条文の判定（P25。合成の項目で確かめる）
- `test_recompute_metrics.py`：済んだ評価の生データからv0.5の指標を計算し直す処理（B5）

元のDMNと第14条の条文本文は`tests/fixtures/`にコミットしてあるため（D42）、
`reference/`が無い環境でも全件が動く（skipは0件）。

空欄（`［　］`）の扱い方針は`testdata/schema.md`「空欄の扱い方針」節を、
①関連判定の基準（解釈A/B）は同ファイル「①関連判定の基準」節を参照。

