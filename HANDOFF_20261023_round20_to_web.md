# aoi_doc2dmn 引き継ぎ資料（第20回）

**作成日**: 2026年10月23日
**引き継ぎ元**: Kazuhiroさんのサーバ環境上の Claude Code
**引き継ぎ先**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）

**位置づけ**: 第19回（`HANDOFF_20261023_round19_to_claudecode.md`）で依頼された
IR v2 設計書 v1.3（D24〜D35）の実装報告。タスク1〜5をすべて実装し、テスト131件が通る。
設計書は `docs/design/aoi_doc2dmn_IRv2_design.md` を v1.3 に差し替えた。

---

## 1. 設計書 v1.3 から外れた点（5件）

いずれも「設計書に書かれていないことを決めた」もので、設計書の記述と矛盾するものはない。

| # | 外れた点 | 理由 | 直す場所 |
| --- | --- | --- | --- |
| 1 | モデル全体に関わる修正（参照されない inputData の削除）の `history` を、そのモデルの**層1エントリ全3件**に同じ記録で残した | D34 は「層1・層2の history に残す」と言うが、登録簿にモデル単位のエントリが無く、この修正は特定の決定表にも行にも属さない。どの表から見ても「なぜ registry/ の DMN が reference/ と違うのか」が分かる形を選んだ。重複して持つのが不本意なら、層1と並ぶ `dmn_models` の層を足すか、`review` と同じ位置にモデル単位の記録を置く案がある | `bootstrap_aoi._record_fixes()`（`scope="dmn_model"` の分岐） |
| 2 | 層1のエントリに `history` を新設した | 設計書4.5の層1の項目に `history` が無い。D34 が「層1・層2の history に残す」と言うので足した（v1.2 でも `add_column` の履歴を層1に書いていたが、初期登録では空リストすら持っていなかった） | `bootstrap_aoi.build_registry()` の層1、設計書4.5 |
| 3 | `registry/dmn/aoi_transmission.dmn` を ElementTree で**再直列化**した結果、`&quot;` が `"` になり、使われていない `xmlns:feel` の宣言が落ちた | 修正（D34）を機械的に当てるため XML として読み書きした。文字列置換なら書式は保てるが、セルの値の足し引きを文字列操作で行うことになり、D28（FEEL の読み方を1か所に集める）と相反する。**XML としては等価**で、評価結果も32シナリオで一致を確認した | `bootstrap_aoi.bootstrap()`（`_serialize()` の呼び出し） |
| 4 | inputData を消すとき、DMNDI の図形（`dmnElementRef` がその id を指す `DMNShape`）も一緒に消した | D34 は inputData の削除だけを書いているが、図形を残すと存在しない要素を指す参照になる。承認された修正の範囲内（同じ1件の後始末）と判断した | `bootstrap_aoi._remove_input_data()` |
| 5 | 登録簿への C5・C11（D34）を、初期登録側ではなく**配線ジェネレータ側**に置いた（`wiring_generator.check_registry_cell_values()`） | クロス検証の実装を1か所に集め、D28 の変換関数を共有するため。初期登録はこれを呼ぶだけ | `wiring_generator.py` |

**ほかに直したほうがよい点**（HANDOFF 4節に従い、直さずに報告）

- `registry/dmn/aoi_transmission.dmn` の `namespace` は `http://gijinka.example.com/dmn/aoi` で、
  配線ジェネレータが新規モデルに付ける規則（`…/dmn/<dmn_model>` = `…/dmn/aoi_transmission`）と
  食い違う。既存モデルへの挿入では元の namespace をそのまま使うので動くが、モデル名と
  namespace の対応が1か所で壊れている
- `decision_identity` の `authorityRequirement` は `ks_patient_master` と `ks_action_request` の
  2件だが、層3の `sources` が空なので、どの fact がどちらの知識源から来るのかは登録簿から
  辿れない（U11 で埋めるときに対応付けが必要）

---

## 2. C6 の新しい基準（D25）での referenced_facts

`existing_facts_used_by_ir()`（= D25 の手順）の結果と、各 IR の `referenced_facts` は一致する
（C6 は足りなくても余っても停止する）。

| 条文 | referenced_facts | なぜそうなるか |
| --- | --- | --- |
| ゴールデンパス（特権アカウント） | `[]` | 入力列3件はすべて `new_facts` |
| 第901条（中間判定への行追加） | `name_match`, `birth_match`, `phone_match` | 新しい表の入力列はドット参照1列だけ。3件は本人確認判定へ足した行の条件から |
| **第902条（レベル2）** | **`contact_consent` だけ** | 足した行の条件のうち「-」でない列は連絡同意の1列。ドット参照2列は除外。**7件の列挙なしでクロス検証を通ることを確認した（D25 の主な確認点）** |
| 第903条（列の追加） | `name_match`, `birth_match`, `phone_match` | 足した列 `address_match` は `new_facts` なので除外 |
| 第904条（no_change） | `name_match`, `birth_match` | 足した行の `phone_match` は「-」なので除外 |

第902条が1件になったことで、第18回のレベル2のテストが抱えていた「条文が言及していない
fact まで宣言させられる」問題は解消した。

**注意点**: 第901条のような「ドット参照だけを入力列に持つ新しい最終判定」単体では
`referenced_facts` が空になる。参照先の中間判定が使う fact は「IR が使う既存 fact」では
ないからである（`tests/conftest.py` の `intermediate_ir` がこの形で、`referenced_facts=[]`）。
この不足を捕まえるのは C1 だけになった（D26）。

---

## 3. 初期登録の修正の結果（D34）

### 修正の定数（`bootstrap_aoi.DMN_FIXES`。1か所にまとめてある）

| fix_id | kind | 内容 | 理由 | 承認者 |
| --- | --- | --- | --- | --- |
| `drop_identity_result_value_from_identity_level_reference` | `remove_cell_value` | 送信可否判定の `tr_r3`・`tr_r5` の「確認レベル」のセルから `"未"` を削除 | 「未」は `identity_result`（済／未）の値で `identity_level` には来ない。残すと参照するたびに C11 に当たる | Kazuhiro |
| `drop_unreferenced_input_data` | `remove_input_data` | `inputData "added by me"`（と対応する DMNDI の図形）を削除 | どの決定表も参照していない。実行時に集める事実が1つ増えたように見えるだけ | Kazuhiro |

修正は `reference/` の元ファイルには当てていない（`test_source_dmn_is_not_modified` で確認）。
修正が実態と合わなくなった場合（値がもう無いなど）は、黙って飛ばさず停止する。

### 修正後のセル

| 行 | 修正前 | 修正後 |
| --- | --- | --- |
| `tr_r3` | `"中","未","不一致検出","情報不足"` | `"中","不一致検出","情報不足"` |
| `tr_r5` | `"未","不一致検出","情報不足"` | `"不一致検出","情報不足"` |

本人確認判定の `identity_result` 側の「未」（`id_r4`・`id_r5`・`id_r6`）はそのまま残る。

### history の記録

- 層1 `dt_transmission`: 2件（セルの修正 + モデル全体の修正）
- 層1 `dt_identity`・`dt_classification`: 各1件（モデル全体の修正。逸脱#1）
- 層2 `tr_r3`・`tr_r5`: 各1件（セルの修正）。`tr_r4` など他の行には付かない
- 記録の形: `{change: "dmn_fix", fix_id, description, reason, approved_by, scope, decision_table_id, row_ids}`

### 修正の前後で評価結果が一致したシナリオ: **32件**

本人確認判定が出しうる確認レベルの全4値（高／中／不一致検出／情報不足）×
送信可否判定の残りの入力（機微度レベル4値 × 連絡同意2値）の全組み合わせ。
各シナリオで、まず狙った中間判定の値になっていることを確かめてから、修正前の DMN
（`reference/` の元ファイル）と修正後の DMN の `decision_transmission` の結果を比べている。
全件一致した（「未」は `identity_level` に来ないので、消しても結果は変わらない）。

### find_problems() の残り: **2件**（修正した2件は消えた）

```
* 本人確認判定.identity_result をどの決定表も参照していない（中間判定の出力なので、使われないなら不要）
* 情報分類判定.info_class をどの決定表も参照していない（中間判定の出力なので、使われないなら不要）
```

HANDOFF の指示どおり、この2件は削除していない（あおいの実行時に説明やログで使っている
可能性がある）。

### そのほか

- **D28**: FEEL のセル → 値の集合 の変換は `wiring_generator._cell_values()` **1つだけ**で、
  C5・C11・登録簿の検査・初期登録の `possible_values` 収集・修正の適用がすべてこれを通る。
  v1.2 の時点で既に1つだったので、**実装の変更はない**（docstring に D28 の根拠を書いた）
- **D35 の depends_on_inputs**: 名前（`input_columns` と同じ語）で入っていることを確認した。
  3枚とも `hit_policy = FIRST`（`test_layer1_uses_input_names_and_first_hit_policy`）
- **再実行**: 登録簿も DMN XML もバイト単位で同じになる（2回実行して diff が空。
  `test_bootstrap_is_reproducible` でも、コミット済みのファイルと一致することを確かめている）

---

## 4. 追加・変更した失敗テスト

### 新しく足したもの（9件）

| テスト名 | 確かめる検証 |
| --- | --- |
| `test_c1_missing_intermediate_fact` | **C1（通しで止まる）**。参照先の中間判定が使う fact が層3に無い（D26 で C1 に戻った役割） |
| `test_c6_extra_referenced_fact` | **C6 の余り**（D25）。IR のどの行・表も使っていない fact を宣言している |
| `test_c9_cond3_output_name_not_in_output_columns` | **C9 条件3**（D29）。`new_output_values` の出力名が `output_columns` に無い |
| `test_c9_cond7_no_change_without_intermediate_change` | **C9 条件7**（D30）。no_change なのに add_row も add_column も無い |
| `test_c9_cond7_no_change_pointing_at_an_intermediate_decision` | **C9 条件7**（D30）。no_change の起点が中間判定（`entry_points` が空） |
| `test_c9_cond8_action_not_in_entry_points` | **C9 条件8**（D30）。既存のルートに足す条文が別の行為を名乗っている |
| `test_c11_value_added_to_another_output_is_rejected` | **C11 の出力名違い**（D29）。`identity_result` に足した値を `identity_level` の参照で使う。**「未」の取り違えそのものの形** |
| `test_d35_rejects_row_insertion_into_a_non_first_table` | **D35**。挿入先の `hit_policy` が FIRST でない |
| `test_required_facts_include_a_column_added_to_a_dot_referenced_table` | **D27**（正常系）。ドット参照で置き換える先の表の `add_column` の列も `required_facts` に入る |

### 変えたもの

| テスト名 | 変更 |
| --- | --- |
| `test_c1_missing_fact_definition` | 残した（関数を直接呼ぶ版。IR が自分で使う fact の宣言漏れは、通しでは C6 が先に止める） |
| `test_c3_rejected_compound_in_primitive` | `option_taken` → `rejected_in_primitive=True`（D31） |
| `test_c9_cond3_add_row_without_matching_output_value` | `new_output_values` を dict に（D29） |
| `test_c7_entry_decision_missing_from_existing_dmn` | 幽霊判定に `entry_points` を持たせた（C9 条件8 が先に止まるため） |
| `test_c11_accepts_value_added_by_this_ir` | dict 化 + `referenced_facts` を D25 の基準に |
| `test_compound_fact_option_is_derived_as_new_table_row` | 導出（`ir.option_taken`）を見る形に。`option_taken` が dataclass のフィールドでないことも固定 |
| `tests/conftest.py` の `intermediate_ir` | `referenced_facts=[]`（D25。入力列がドット参照1列だけ） |

### 初期登録の修正のテスト（新しいファイル `tests/test_bootstrap_fixes.py`）

| テスト名 | 確かめること |
| --- | --- |
| `test_fixes_are_declared_in_one_place_with_reason_and_approver` | 修正が2件だけで、内容・理由・承認者（Kazuhiro）を持つ |
| `test_fixes_are_recorded_in_layer1_and_layer2_history` | 層1・層2の history に残る |
| `test_fixed_values_are_gone_from_the_registry_and_the_dmn` | 「未」と `added by me` が消え、`identity_result` の「未」は残る |
| **`test_without_fixes_the_registry_fails_c11`** | **修正を外すと登録簿への C11 で止まる（D34 の要点）** |
| `test_with_fixes_the_registry_passes` | 修正を当てれば C5・C11 を通る |
| `test_fix_does_not_change_evaluation`（32件） | 修正前後で評価結果が一致する |
| `test_bootstrap_is_reproducible` | 再実行でバイト単位で同じ |
| `test_source_dmn_is_not_modified` | `reference/` の元ファイルは変わらない |
| `test_remaining_problems_are_only_the_unused_outputs` | find_problems() の残りが使われない出力の2件だけ |
| `test_layer1_uses_input_names_and_first_hit_policy` | D35 の確認（名前・FIRST） |

**注意**: `reference/` はリポジトリに入っていない（.gitignore 対象）ため、元の DMN を使う
テスト（上の表の4〜9行目、37件）は `reference/` が無い環境では**飛ばされる**。修正の履歴や
結果を確かめるテストは、コミット済みの `registry/` から確かめるので、どこでも動く。

---

## 5. 第904条（no_change）の結果

**条文（テスト用の架空）**: 「氏名と生年月日が一致すれば、電話番号が一致しなくても確認レベルを
中とする」。本人確認判定に行を1本（`position=3`）足すだけで、最終判定は作らない。

| 項目 | 結果 |
| --- | --- |
| `outcome` | `level2_intermediate_only`（D30） |
| `option_taken`（導出） | `{dt_identity: existing_table_row_addition}` |
| `referenced_facts` | `name_match`, `birth_match`（`phone_match` は「-」なので入らない） |
| **ルーティング表** | **1行だけ**。`あおい × 外部送信 → decision_transmission`。IR 自身のルートと D16 で洗い出した既存のルートが同じなのでまとまる |
| そのルートの required_facts | 7件（`name_match`, `birth_match`, `phone_match`, `has_medical`, `has_pii`, `identifiable`, `contact_consent`） |
| 層1 | 増えない（新しい決定表を作らない） |
| DMN | 本人確認判定が6行→7行、送信可否判定は7行のまま |

### DMN 評価（送信可否判定まで通した）

| シナリオ | 入力 | 結果 |
| --- | --- | --- |
| 追加した行に当たる | 氏名一致・生年月日一致・**電話不一致**、感度「高」、同意 | **許可 / 本人確認充足** |
| 既定の行（ワイルドカード）に落ちる | **氏名不一致**・生年月日一致・電話一致、感度「高」、同意 | 禁止 / 本人未確認（`id_r5` → `tr_r5`） |

1件目は、**行を足さなければ** `id_r6`（情報不足）に落ちて `tr_r5` の「禁止 / 本人未確認」に
なる（実測で確認した）。足した行が効いて禁止→許可に変わっており、no_change でも中間判定の
変更が最終判定の結果に届いていることが見える。

---

## 6. option_taken の導出結果（D31）

| 条文 | 導出結果 |
| --- | --- |
| ゴールデンパス | `{dt_privileged_operation: new_table_row}` |
| 第901条 | `{test_dt_verbal: new_table_row, dt_identity: existing_table_row_addition}` |
| 第902条 | `{dt_transmission: existing_table_row_addition}` |
| 第903条 | `{test_dt_address: new_table_row, dt_identity: existing_table_row_addition}` |
| 第904条 | `{dt_identity: existing_table_row_addition}` |

置き場所は `ClauseIR.option_taken`（プロパティ）にした。導出に IR 全体が要るうえ、
`CompoundFactApplication` は自分以外の欄を知らないため。`new_intermediate_table` は
**今回のどの条文でも出ていない**（1つの IR の中で、新しく作った表を別の新しい表が参照する
形がまだ無い。導出規則の実装は入っている）。

---

## 7. 正解 IR の作成に向けて気づいた懸念

| # | 懸念 | なぜ困るか | 提案 |
| --- | --- | --- | --- |
| 1 | **`new_output_values` に「既にある値」を書くかの規則が無い** | 第903条・第904条では、足す行の確認レベル「中」が本人確認判定に既にある値なので `{}` にした。「足す値」の意味を取れば `{}` が正しいが、「その行が出す値」と取れば `{identity_level: [中]}` になる。C9 条件3 はどちらも通すので、前段の出力と正解 IR の比較で揺れる | 「`new_output_values` は、その表が**まだ出していない**値だけを書く」と設計書3.3に1行足す（C9 に「既にある値を書いたら停止」を足すかは別途） |
| 2 | **条文が言及した fact と `referenced_facts` がずれる場合がある** | 第904条の条文は「電話番号が一致しなくても」と電話番号に明示的に言及しているが、行の条件は「-」なので D25 の基準では `referenced_facts` に入らない。正解 IR を人が書くとき、条文の語を拾って `phone_match` を書くと C6 の余りで止まる | D25 の基準は維持したうえで、6.2節（正解 IR の作り方）に「`referenced_facts` は条文の語ではなく、書いた行の条件から決める」と注記する |
| 3 | **`option_taken` の導出が時点依存** | 「他の判定から参照されれば `new_intermediate_table`」を IR の中だけで判定している。ある条文が中間判定を新しく作り、**別の条文が後からそれを参照**した場合、最初の条文の導出は `new_table_row` のままになる（その時点では参照者がいない） | 導出を「IR の中の参照 + 登録簿の `depends_on_decisions`」に広げるか、「IR を作った時点での判断」と割り切ることを3.4節に明記する |
| 4 | **`depends_on_inputs` を正解 IR でどう書くかが未定義** | D35 で「inputData の名前」と決まったが、`input_columns` からドット参照を除いたものと同じにするのか、別に書けるのかが決まっていない。配線ジェネレータは IR の値をそのまま層1に写すだけで、正規化も検査もしていない（初期登録側は DMN から機械的に起こすので一致している） | `depends_on_inputs` を導出（`input_columns` のうちドットを含まない列）にして IR から外すか、C9 に一致の条件を足す |
| 5 | **層3の `sources` が空のままである（U11）** | あおいの7件すべてに `sources_undefined: true` が付く。正解 IR が既存 fact を参照するときは問題ないが、`value_additions` で値を足すときに「その値をどこから取るか」が決まらない | U4（一致プリミティブの実装）まで現状どおり。バインディング設定に印が出ているので、埋め忘れは見える |
| 6 | **`registry/dmn/` の DMN の namespace がモデル名と合っていない** | `…/dmn/aoi`（モデル名は `aoi_transmission`）。新規モデルは `…/dmn/<dmn_model>` で作るので、規則が2通りになる。Kogito 実機で複数モデルを読む段階で問題になりうる | registry/ 側で namespace も直す（D34 の3件目の修正として承認が必要）か、「既存モデルの namespace は触らない」を設計書4.2に明記する |
| 7 | **再直列化で元の DMN との差分が大きく見える**（逸脱#3） | `&quot;` → `"`、`xmlns:feel` の欠落、属性の並び替えが全体に出る。Kogito 実機確認のときに「どこを直したのか」が diff から読み取りにくい | 差分を小さくしたいなら、修正を XML の木ではなく文字列置換で当てる方式に変える（ただし D28 と相反する） |
| 8 | **C8 の失敗テストが依然として作れない** | ルーティング表とバインディング設定を同じ `required_facts` から導くため、IR の側から違反を作れない（設計書5章も同じ扱い）。`test_c8_is_structurally_guaranteed` で一致を固定している | 現状どおり |

---

## 8. push したコミットとテストの件数

| 項目 | 値 |
| --- | --- |
| コミット | `e6e3ee6` |
| テスト | **131件すべて成功**（`reference/` がある環境。無い環境では37件が skip） |
| 内訳 | `test_cross_validation.py` 44件 / `test_golden_path.py` 20件 / `test_acceptance_paths.py` 26件 / `test_bootstrap_fixes.py` 41件 |
| ゴールデンパス | C1〜C11 通過、DMN 評価 **4/4 一致**、ゲートは `new_action_unapproved`・`new_fact_unapproved` の2つ（承認後は0） |
| 初期登録 | 層1=3件・層2=18件・層3=7件、修正2件、find_problems() の残り2件、再実行でバイト単位同一 |

### 変更したファイル

| ファイル | 変更 |
| --- | --- |
| `docs/design/aoi_doc2dmn_IRv2_design.md` | v1.2 → **v1.3**（差し替え） |
| `src/stage3_generate/ir_schema.py` | D29（dict 化）・D30（no_change・outcome）・D31（`rejected_in_primitive` と `ClauseIR.option_taken`） |
| `src/stage3_generate/wiring_generator.py` | D24・D25・D26（C6 を1つに）・D27・D28（docstring）・D29（C9条件3・C11）・D30（条件7・8、ルートのまとめ方）・D31（C3）・D33（`sources_undefined`）・D35（hit_policy）・`check_registry_cell_values()` を新設 |
| `src/stage3_generate/bootstrap_aoi.py` | D34（`DMN_FIXES`・修正の適用・history・修正後の検証）、層1に `history`、遅延 import の解消、`BootstrapResult` |
| `src/stage3_generate/golden_path.py` | D31（`rejected_in_primitive`） |
| `src/stage3_generate/run_golden_path.py` | 導出した `option_taken` を表示 |
| `registry/dmn/aoi_transmission.dmn` | D34 の修正2件を当てたもの（再直列化込み） |
| `registry/registry.json` | 初期登録をやり直したもの（修正と history 入り） |
| `tests/conftest.py` | D25・D31 に合わせたフィクスチャ |
| `tests/test_cross_validation.py` | 失敗テスト9件追加・6件変更（35→44件） |
| `tests/test_golden_path.py` | `option_taken` の導出・D33（18→20件） |
| `tests/test_acceptance_paths.py` | 第901〜903条を v1.3 に、**第904条（no_change）を追加**、D33（11→26件） |
| `tests/test_bootstrap_fixes.py` | **新規**。D34 のテスト（41件） |
| `README.md` | v1.3・初期登録の修正・テストの4ファイル構成 |

---

## 9. 範囲外（手を付けていない）

- 正解 IR の作成（設計書6章）。**次に扱う**
- あおいの fact の `sources` を埋めること（U11）、一致プリミティブの実装（U4）
- promotion（U8）、DMN のモデル間 import（U9）、前段の自動化、Kogito 実機での評価

---

## 10. claude.ai チャットへの引き継ぎメッセージ

```
aoi_doc2dmnで、第19回（IR v2 設計書 v1.3）の実装が完了しました。
HANDOFF_20261023_round20_to_web.md を読んで、結果を把握してください。
コミット e6e3ee6 を push 済みです。正解IRの作成に進めるかを相談したいです。
```

---

## 11. このドキュメントの位置づけ

過去の HANDOFF ファイルと同様、これは特定時点のスナップショットである。今後の議論で決定事項が
更新された場合も本ファイルはそのまま残し、更新が必要な場合は新しい日付・ラウンド番号の
ファイルを追加する運用とする。
