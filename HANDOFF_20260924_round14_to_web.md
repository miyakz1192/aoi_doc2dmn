# aoi_doc2dmn 引き継ぎ資料（第14回）

**作成日**: 2026年9月24日
**引き継ぎ元**: Kazuhiroさんのサーバ環境上のClaude Code
**引き継ぎ先**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）

**位置づけ**: 第13回（`HANDOFF_20260924_round13_to_claudecode.md`）で指示された③a着手
（単一条文でのゴールデンパス実装）の完了報告。Claude Code→claude.aiチャットの方向。

---

## 1. 結論：条文→②分類→IR→4点セット→DMN評価が一気通貫で動作

実分類器（Ollama `qwen2.5:14b-instruct`）を通した本番相当の実行で、以下がすべて成功した。

- ②2軸分類：軸1=ハード制約（gold一致）、軸2=宣言的（gold一致）、両方とも
  `quote_verified=True`・`vocab_flagged=False`
- ③a IR構築：`level_outcome=level1_new_table`、`compound_fact=existing_table_row_addition`
- 4点セット生成：DMN XML・バインディング設定・ルーティング表・Scope/Decision登録簿
- 生成DMNの評価：**シナリオ4/4一致**

再現コマンド：
```bash
.venv/bin/python3 -m src.stage3_generate.run_golden_path              # ②も実際に呼ぶ
.venv/bin/python3 -m src.stage3_generate.run_golden_path --use-oracle # Ollama不要
```

---

## 2. ゴールデンパス対象条文の選定

**選定**：`ipa_secguide_v4_6_7_1`（IPA情報セキュリティ関連規程 6.7 特権アカウント 第3項）

> システム開発を担当する者が本番環境において特権/管理者権限で操作を行うことを禁止する。

### 選定理由

- `hard_declarative`セル、`is_ambiguous=false`、②分類器が軸1・軸2とも正解している
  （分類段階の不確実性を持ち込まない、という第13回の条件を満たす）
- 判定に必要な事実が「操作者の役割」「対象環境」「要求権限」の3つの独立した属性のみで、
  **いずれも条文本文から直接読み取れる**。外部知識・contextを一切必要としないため、
  第11回で確定した「gold labelは本文＋明示的contextのみに基づく」という方針の論点に
  触れない（第13回5節の要望を満たす）
- 他条文・既存決定表を参照しないため、レベル1/レベル2判定（複数DMNファイル問題）が発生しない
- 3属性はいずれも単一比較（exact_match）で判定でき、複合fact禁止原則に自然に適合する

### 次点候補（不採用）

`mhlw_r7dec_art11_1`（第11条 遵守事項：「許可なく職務以外の目的で会社の施設、物品等を
使用しないこと」）も条件を満たすが、「許可」の有無という単一factのみで決定表が
1入力1列になり、複合fact禁止原則やFIRSTヒットポリシーの意味が検証できないため次点とした。
選定した6.7は3列あり、「危険な条件を上位行に置く」設計まで含めて確認できる。

---

## 3. IRスキーマ設計：踏襲した点と調整した点

`reference/gijinka-extraction-pipeline`（Kogito実機18/18一致の検証済み設計）を土台とし、
ゼロから設計し直してはいない。

### 踏襲した点

- 4点セットを**同一IRから1回の呼び出しで同時生成**する（別々の関数・タイミングで作らない）
- 複合fact禁止原則（一致モジュールは単一比較のみ。複合判定は決定表の行として表現）。
  `_enforce_no_compound_in_primitive()`で`rejected_compound_in_primitive`を実際に拒否する
- FIRSTヒットポリシー、危険な条件（禁止に該当するケース）を上位行に置く設計
- `knowledgeSource`（来歴専用、計算に使わない）と`inputData`/`informationRequirement`の区別。
  `authorityRequirement`はDMN標準の制約により`inputData`に付けられないため、
  `decision`側に紐づける実装にした
- `ActionRef`の設計時2段階ルックアップ（NL表層→候補、階層への位置づけは人間が承認、
  実行時は静的文字列一致）
- `RuleRowDraft.outputs`を出力名→値の汎用マップとして持ち、`{decision, reason_code}`に
  一致する場合のみ登録簿でトップレベル展開する設計判断
- 必須のクロス検証（登録簿/決定表が要求するフラットfactキー ⇔ バインディング設定のキー集合）

### aoi_doc2dmn向けに調整した点

1. **`human_review_status`を単一statusから、独立した複数ゲートのOR条件に変更**（`ReviewGates`）。
   reference側は`confidence < 0.7`のみを条件にしていたが、本リポジトリの実測でconfidenceは
   常に0.7超を返し較正情報を持たないことが判明済み。②で実装した`quote_verified`・
   `vocab_flagged`を独立したゲートとして併置した（詳細は4節）
2. **DMN XMLを「既存ファイルへのパッチ（差分）」ではなく「完結した新規DMNファイル」として
   生成する経路を追加**（`render_dmn_xml()`）。③の初回スコープが「独立した単純な条文1件」で
   あり、追加先となる既存DMNファイルが存在しないため。既存DMNへの行追加（レベル2）は
   次スコープで扱う
3. クロス検証に**逆方向のチェックを追加**：どの決定表からも参照されないバインディングが
   含まれていた場合も失敗させる（reference側は「不足」のみ検出していた）
4. `AxisResult`に`quote_verified`/`vocab_flagged`を追加し、②の`AxisClassificationResult`と
   対応させた。`strategy_id`は本リポジトリの②に無いためオプショナル化

**上記以外で設計原則から逸脱した点はありません。**

---

## 4. `human_review_status`ゲートの実装

`ReviewGates`（`src/stage3_generate/ir_schema.py`）として、以下4つを**独立した真偽値のまま**
保持し、`should_review`はそのOR。1つの不透明なスコアに混ぜていない（複合fact禁止原則と同じ思想）。

| ゲート | 条件 |
|---|---|
| `confidence_below_threshold` | 軸1または軸2のconfidence < 0.7（機能しないことは分かっているが形式的に残す） |
| `quote_unverified` | 軸1または軸2の`quote_verified=False`（ハルシネーション型） |
| `vocab_flagged` | 軸1または軸2の`vocab_flagged=True`（語彙的盲点型） |
| `new_action_unapproved` | **③固有の新ゲート**。`ActionRef.is_new=True`かつ`approved_by=None` |

`reasons()`で立っているゲートを列挙でき、登録簿の`review`ブロックにそのまま出力される。

今回のゴールデンパスでは`new_action_unapproved`が立ち（action「特権操作」が新規・未承認）、
`should_review=True`・`human_review_status=pending`となった。**これは設計通りの挙動**で、
新規Actionを階層に位置づける判断は人間が行う（設計時2段階ルックアップ）という原則が
実際に機能していることの確認になっている。

---

## 5. 生成された4点セット

`out/golden_path/`に出力（gitignore対象。`run_golden_path.py`で再生成可能）。

1. **DMN XML**：`decision_privileged_operation`（1決定表、FIRSTヒットポリシー、3入力列、
   2出力`decision`/`reason_code`、2ルール行）。`inputData`3件、`knowledgeSource`2件
   （`ks_privileged_account_registry`・`ks_action_request`）を`authorityRequirement`で
   decisionに紐づけ
2. **バインディング設定**：3fact分。各factに`primitive`（exact_match）・`possible_values`明示・
   名前付き`sources`キーを持たせた
3. **ルーティング表**：`{actor: あおい, action: 特権操作} → input_columns 3件`
4. **Scope/Decision登録簿**：層1（決定表メタ）・層2（ルール行台帳）＋`review`ブロック。
   全エントリに`provenance`（origin/clause_ref/registered_by/registered_at）と空の`history`

### 決定表の中身

| position | operator_role | target_environment | requested_privilege | decision | reason_code |
|---|---|---|---|---|---|
| 1 | システム開発担当 | 本番環境 | 特権 | 禁止 | dev_privileged_op_on_production |
| 2 | - | - | - | 許可 | not_prohibited_combination |

「開発担当者 かつ 本番環境 かつ 特権権限」というANDを、一致プリミティブ側ではなく
決定表の1行の3列として表現している（複合fact禁止原則）。

---

## 6. 検証結果

### 自作エンジンでのDMN評価：4/4一致

| シナリオ | 期待 | 結果 |
|---|---|---|
| 開発担当者が本番環境で特権操作 | 禁止 | OK（id_r_priv_1） |
| 開発担当者だが検証環境 | 許可 | OK（id_r_priv_2） |
| 開発担当者が本番環境だが一般権限 | 許可 | OK（id_r_priv_2） |
| 運用担当者が本番環境で特権操作 | 許可 | OK（id_r_priv_2） |

検証エンジンは`reference/`の`dmn_engine.py`をロジック変更なしで移植したもの。reference側で
このエンジンとKogito実機の結果が18/18一致することを確認済みのため、一次検証として採用した。

### ガード類の動作確認（意図的な失敗テスト）

| ケース | 結果 |
|---|---|
| バインディング不足（3factのうち2つしか設定しない） | 正しく`SchemaWiringError` |
| 余分なバインディング（どの決定表も参照しないfact） | 正しく`SchemaWiringError` |
| 複合fact禁止原則違反（`rejected_compound_in_primitive`） | 正しく`SchemaWiringError` |

### Kogito実機検証：未実施

第13回の指示通り、**別タスクとして切り出す**。理由は、reference側のKogito環境
（`/home/miyakz/aoi_kie/aoi-dmn-service/`、Quarkus dev mode）が現在も起動可能かの確認と、
生成したDMNを新規プロジェクトとして配置する作業が必要で、4点セット生成の検証とは
独立した準備コストがかかるため。今回の自作エンジンでの4/4一致をもって一次検証とし、
実機確認は次スコープ（複雑な条文への拡張）とあわせて実施するのが効率的と判断した。

---

## 7. 次に複雑な条文へ広げる際の懸念点

単純な1件を通してみて見えてきた点を、率直に記録する。

1. **IR構築が現状ハードコードである**：`golden_path.py`の`build_golden_path_ir()`は、
   「どのfactを入力列にするか」「決定表に何行書くか」「各行の条件と出力値は何か」を
   Claude Codeが条文を読んで手で決めている。これは第4回で`reference/`の分析メモ
   （`llm_vs_hardcoded_in_tests.md`）が指摘していた「③は完全にハードコードされており
   自動化されていない」という状態と**同じ地点**にいることを意味する。配線（IR→4点セット）
   は自動化できたが、**条文→IRの自動導出は未着手**。ここが③の本丸であり、次の最大の論点
2. **`possible_values`の決定に外部知識が入り込む余地がある**：今回
   `operator_role: ["システム開発担当", "運用担当", "その他"]`としたが、「運用担当」は
   条文本文に無く、Claude Codeが業務文脈から補った。第11回で確定した「外部知識は`context`と
   して構造化し出典を記録する」という方針に照らすと、**現状これは方針違反に近い**。
   条文→IR自動導出を実装する際は、`possible_values`の導出経路をどう扱うかを明示的に
   設計する必要がある（今回は単純な条文のため実害は無かったが、複雑な条文では
   この曖昧さが効いてくる可能性が高い）
3. **レベル1/レベル2判定は今回実質的に検証できていない**：既存DMNファイルが存在しないため、
   `final_layer`の判定は形式的なものにとどまった。次スコープで既存決定表への行追加
   （レベル2）を扱う際に、この判定ロジックを初めて実装・検証することになる
4. **`escalation_*`セルの条文は③aの枠組みに乗らない可能性**：今回スコープ外だが、
   「人間の承認を得る」「期限超過でフォールバック」という構造は、単発の決定表評価では
   表現しきれず、③b（ObligationManager向けIR）の領域に入る。③aを`hard_declarative`以外に
   広げる際の境界をどこに引くかは、早めに整理しておくと良さそう

---

## 8. 変更したファイル

- `src/stage3_generate/ir_schema.py`：新規。`ClauseIR`・`ReviewGates`等
- `src/stage3_generate/wiring_generator.py`：新規。4点セット同時生成＋クロス検証＋DMN XML生成
- `src/stage3_generate/golden_path.py`：新規。ゴールデンパス条文のIR構築
- `src/stage3_generate/dmn_engine.py`：`reference/`から移植（ロジック変更なし）
- `src/stage3_generate/run_golden_path.py`：新規。一気通貫実行スクリプト
- `README.md`：③aの構成・実行方法を追記
- `.gitignore`：`out/`（生成物）を追加

---

## 9. claude.aiチャットへの引き継ぎメッセージ例

```
aoi_doc2dmnで、③aのゴールデンパス（単一条文→4点セット）が一気通貫で動きました。
HANDOFF_20260924_round14_to_web.md を読んで、結果を把握してください。

特に7節「次に複雑な条文へ広げる際の懸念点」の1（条文→IRの自動導出が未着手で、
現状reference/が指摘していた"③は完全にハードコード"と同じ地点にいる）と
2（possible_valuesの決定に外部知識が入り込んでおり、第11回の方針に照らすと
方針違反に近い）について、次の一手を相談したいです。
```

---

## 10. このドキュメントの位置づけ

過去のHANDOFFファイルと同様、これは特定時点のスナップショットです。今後の議論で決定事項が
更新された場合も、本ファイルはそのまま残し、更新が必要な場合は新しい日付・ラウンド番号の
ファイルを追加する運用とする。
