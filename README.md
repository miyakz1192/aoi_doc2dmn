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
│   ├── stage3_generate/  # ③a 内容生成(IR→4点セット)
│   │   ├── ir_schema.py        # ClauseIR v2 / FactDef / ReviewGates
│   │   ├── registry.py         # Scope/Decision登録簿(層1・2・3)の読み書き
│   │   ├── wiring_generator.py # 4点セット同時生成+クロス検証C1〜C10
│   │   ├── golden_path.py      # ゴールデンパス条文のIR構築
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
│   ├── candidates/              # テストデータ拡張時の候補・レビュー記録
│   └── schema.md                # オラクルのスキーマ定義・カバレッジ表・①関連判定の基準
├── registry/
│   └── registry.json     # 永続化した登録簿（③の入力。既存の決定表とfact台帳）
├── tests/                # pytest（クロス検証C1〜C10の失敗テスト・ゴールデンパス固定）
├── docs/design/          # ③aの設計書（IR v2設計書・9e66776時点のAsIs仕様書）
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
  IR v2（`docs/design/aoi_doc2dmn_IRv2_design.md`）で実装済み。fact の定義がIRに入り
  （`facts.new_facts`）、4点すべてをIRと登録簿から生成する。クロス検証はC1〜C10で、
  それぞれの失敗テストが`tests/`にある
- ③aの**前段**（条文 → IR）は未実装。現状は`golden_path.py`がゴールデンパス1件のIRを
  手で組み立てている。これが③の本丸であり次の作業対象

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

条文 → ②2軸分類 → ③a IR構築 → 4点セット生成（クロス検証C1〜C10）→ 生成DMNの評価、までを
一気通貫で実行する。`out/golden_path/`にDMN XML・バインディング設定・ルーティング表・
登録簿が出力される。

入力となる登録簿は`registry/registry.json`（`--registry`で差し替え可能）。更新後の登録簿は
`out/`に書き出すだけで、永続台帳への反映（promotion）は人間レビューを経た別操作とする。

### テスト

```bash
.venv/bin/python3 -m pytest tests/ -v
```

クロス検証C1〜C10の失敗テスト（わざと違反させたIRで`SchemaWiringError`になること）と、
ゴールデンパスがC1〜C10を通過しDMN評価4/4になることを固定している。Ollamaは不要。

空欄（`［　］`）の扱い方針は`testdata/schema.md`「空欄の扱い方針」節を、
①関連判定の基準（解釈A/B）は同ファイル「①関連判定の基準」節を参照。

