# aoi_doc2dmn 引き継ぎ資料（第26回）

**作成日**: 2026年11月2日
**引き継ぎ元**: Kazuhiroさんのサーバ環境上のClaude Code
**引き継ぎ先**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）

**位置づけ**: 第25回（`HANDOFF_20261102_round25_to_claudecode.md`）で依頼された、設計書 v1.6 の実装（パートA）、レビュー結果に沿った正解 IR の手直し（パートB）、承認の記録（パートC）の報告。

- 設計書は `docs/design/aoi_doc2dmn_IRv2_design.md` を v1.6 に差し替えた。
- テストは **187件・skip 0件**（v1.5 時点は169件）。
- 正解IR は **5件すべて通過・シナリオ 26/26 一致・evidence の「根拠なし」は全件0件**。
- 正解IR 5件すべてを **approved** にした（draft で残したものはない）。

---

## 1. 設計書 v1.6 から外れた点

### 逸脱1：比較型の fact の possible_values は、IR に書かせない（タスクA5の「判断に任せる」）

- D59 は「IR の new_facts で比較型の fact に possible_values を書いた場合の扱いは判断に任せる」としていた。**書かせない**ことにした（書いてあれば C4 で停止）。
- 理由：「その他」を書かせないのと同じ理由である（D45・D61）。導出できる値を書かせると「書く／書かない」「どこまで書くか」の揺れが残り、正解IR と前段の出力の比較でぶれる。導いた値との一致を求める形にすると、前段の LLM に「一致／不一致／未提供」を毎回書かせることになり、書き漏らしを検査で埋め合わせるだけになる。
- 直す場所：`ir_schema.py` の `PRIMITIVE_VALUES`・`derived_possible_values`、`wiring_generator._check_one_fact_sources`。設計書5章 C4 の文言に「比較型の new_facts に possible_values が書かれている場合も停止」を足すと一致する。
- 失敗テスト：`test_c4_comparison_fact_must_not_declare_possible_values`。導出されることの確認は `test_comparison_fact_values_are_derived_from_the_primitive`。

### 逸脱2：`disjoint` は中間判定の `add_row` とも組み合わせられない（タスクA4の「判断に任せる」）

- D58 は `add_rows`（最終判定）との組み合わせを止めてよいとしていた。**`intermediate.action = add_row` との組み合わせも止めた**。
- 理由：同じ理屈である。列集合が1つも重ならない表に行を足すには、既存の列をすべて「-」にするしかなく、それはもう「同じ表の行」ではない。
- 直す場所：`wiring_generator._check_c9_level_determination`（条件2・条件3）。設計書 D58 に「`add_row` も同じ」と足すと一致する。
- テスト：`test_c9_disjoint_cannot_be_combined_with_add_rows`、`test_c9_disjoint_is_allowed_with_new_table`。

### 逸脱3：全角数字は警告ではなく停止（タスクA6の「どちらにするかは判断に任せる」）

- **停止**にした。理由：値は実行時に文字列一致で引くので、警告だと残り続け、実行時に一度も当たらない行ができる。D56 を「値の正規化の規則」としたのなら、検査は規則どおり効かせるべきだと判断した。
- 対象は「実行時に文字列一致で引かれる値」に限った（possible_values・決定表のセル・`new_output_values`・シナリオの fact の値）。**条文本文と evidence の引用（quote）は逐語なので対象外**にした（D44）。
- 直す場所：`run_ir_gold._check_halfwidth_digits`。

### 設計書の記述の食い違い（実装は D60 に従った）

- 設計書 **3.8節のゴールデンパスの IR の例**が、まだ `"parent": "システム操作"` になっている。D60・レビュー A-2 と食い違う。実装（`golden_path.py`・正解IR）は `parent = None` にした。設計書の例の差し替えをお願いしたい。

---

## 2. パートA の結果

### A1：承認の分離（D53）

- `run_ir_gold` の確認を次のようにした。
  - `review.status = approved` なら、`reviewed_by`・`reviewed_at`・`decisions` があること。
  - IR の中の `approved_by`（action・new_facts・value_additions・new_output_values）がすべて None であること（これは従来からの確認で、根拠を D53 に書き換えた）。
- `human_review_status` は `pending` のままにした。**違和感はない。** 前段が出力する IR のレビュー状態を表す欄で、「この IR が正解として承認されたか」とは別の軸だからである。ただし、名前が近いので混同されやすい。`gold_review_status` のように正解IR 側の欄名を分けておくほうが安全だと考える（型の変更なので今回はしていない）。
- テスト：`test_gold_file_shape`（5件分）。

### A2：other_value_note（D55）

- `FactDef.other_value_note: str | None` を足した。`to_json` は **passthrough の fact のときだけ**この欄を出す（比較型に `null` を並べない）。層3とバインディング設定の両方に出る。
- **passthrough の new_facts に注記が無ければ C4 で停止する**。
  - 失敗テスト：`test_c4_passthrough_without_other_value_note`。
- 初期登録の `contact_consent` には固定の文 `(初期登録)DMN の表に現れない状態` を入れ、provenance に「DMN に情報が無いため固定の文。語彙の承認（U8）のときに出典付きで埋める必要がある」と書いた（`OTHER_VALUE_NOTE_GAP`）。
- `golden_path.py` の3つの fact にも、正解IR と同じ注記を入れてそろえた。

### A3：既存モデルへの追記（D57・第24回の穴#1）

- `_needs_existing_dmn` を「既存の表に行・列を足している **または** `registry/dmn/<dmn_model>.dmn` がある」に変えた。`patch_existing_dmn` は元の `definitions` をそのまま使うので、namespace は変わらない（D39）。
- テスト（第906条として追加）
  - `test_new_table_only_ir_appends_to_the_existing_model`：あおいのモデルに、既存の表に手を入れない新しい表だけを足す IR を流し、**元の3つの判定が残る**こと・**namespace が `…/dmn/aoi` のまま**であること・既存の表の行が1行も増えないこと（7行のまま）を確かめる。
  - `test_new_table_only_ir_records_the_disjoint_relation`：ルートが新しい判定の1本だけになること。

### A4：disjoint（D58・第24回の穴#2）

- `ColumnSetRelationKind` に `disjoint` を足した。
- C9 での扱い（決めた形）
  | 組み合わせ | 扱い |
  |---|---|
  | `disjoint` + `final.action = new_table` | **通す**（比べたが重ならなかったことの記録。第14条がこの形） |
  | `disjoint` + `final.action = add_rows` | **停止**（条件2） |
  | `disjoint` + `intermediate.action = add_row` | **停止**（条件3。逸脱2） |
  | `disjoint` + `final.action = no_change` | 条件7が `compared_table_id = None` を要求するので、そもそも書けない |

### A5：比較型の fact の possible_values（D59・第24回の穴#4）

**registry/registry.json の差分**（層3の7件のみ。層1・層2・DMN ファイルは無変更）

| fact | primitive | 変更前 | 変更後 |
|---|---|---|---|
| `name_match` | fuzzy_match_name | 一致・不一致 | 一致・不一致・**未提供** |
| `birth_match` | date_match | 一致・未提供・不一致 | 一致・不一致・未提供（**並びをプリミティブの定義順に**） |
| `phone_match` | exact_match | 一致・未提供 | 一致・**不一致**・未提供 |
| `has_medical`・`has_pii` | ontology_class_match | 含む・含まない | 変更なし |
| `identifiable` | identifiability_classifier | 可能・不可能_院内・不可能_公開 | 変更なし |
| `contact_consent` | passthrough | 不同意・同意・その他 | 変更なし（D4 は passthrough にだけ当てる） |

あわせて7件すべてに `other_value_note`（passthrough 以外は `null`）が入り、`contact_consent` の provenance の note が1行増えた。**それ以外の差分はない。**

- 初期登録では、決定表のセルの値がプリミティブの定義の外にあれば**停止**するようにした（黙って狭めない）。あおいの DMN では7件すべて定義の内側だった。
- **修正前後で評価結果が変わらないこと**：受け入れ経路のテスト（第19回の32件に相当。現在は34件）と初期登録のテスト42件が、どちらも全件通る。第901〜905条の DMN 評価シナリオの結果も変わっていない。
- **第24回の回避を元に戻した**：`test_c12_passes_when_the_consumer_gets_a_row_for_the_value` の入力を `name_match = 不一致` から **`未提供` に戻した**（第24回はこの値がエスカレーションになるため変えていた）。これで通る。
- あわせて、第901条・第904条の fixture から `value_additions = {"phone_match": [不一致]}` を外した（導出されるので不要になった）。D14 の「値の追加の記録」を確かめるテストは、passthrough の `contact_consent`（保留）を使う形に移した（`test_value_addition_lands_in_registry_and_bindings`）。**値を足す余地があるのは passthrough の fact だけになった**ので、このほうが筋が通る。
- `test_comparison_fact_values_come_from_the_primitive` を足し、第901条で `phone_match` が「一致・不一致・未提供」になり履歴が増えないことを固定した。

### A6：数字の半角（D56）

- `run_ir_gold._check_halfwidth_digits` を足した（停止。逸脱3）。第21条第3項の「産後１年」を実際に捕まえた。
- テスト：`test_values_use_halfwidth_digits`（5件分）。

### A7：第24回の判断の確認（D61）

| 項目 | 確認 |
|---|---|
| C12（行全体・直接参照） | 実装と設計書 v1.6 の文言が一致。コードの docstring にあった「設計書の文言からの逸脱・要報告」を「v1.6 の D61 で採用された」に書き換えた |
| C4 での「その他」の停止 | 一致（`_check_one_fact_sources`） |
| `scenario_eval` | 一致。`run_ir_gold` と受け入れ経路のテストの評価が経由する |
| `evidence.fact_mapping` | 一致（第14条の連絡先 → phone_match） |
| 層1の history（`output_value_addition`） | 一致 |

### おまけ：`run_ir_gold` の報告の穴を1つ直した

クロス検証で停止した正解IR が**何も印字されないまま**「失敗 n件」だけになっていた（早く return していた）。第24回は5件すべて通っていたので見えていなかった。今回 D55 で4件が止まった時に気付き、停止した件も印字するようにした。

---

## 3. パートB の結果

| gold_id | 手直し | シナリオ | evidence（逐語／正規化／既定の行／根拠なし） |
|---|---|---|---|
| `ipa_secguide_v4_6_7_1` | **action.parent を None に**（A-2・D60）。3つの fact に other_value_note。escalate の値を `判定不能` に。「検証環境」を「その他」のシナリオに移した | 5件（禁止1・その他3・escalate1） | 3／2／1／**0** |
| `mhlw_r7dec_art11_1` | 3つの fact に other_value_note（usage_target には「等」の扱いが当てはめる側の判断になることを明記。B-2）。escalate の値を `判定不能` に。「社員の私物」を「その他」のシナリオに移した | 6件（禁止2・その他3・escalate1） | 5／1／1／**0** |
| `ipa_secguide_v4_personnel_2` | 2つの fact に other_value_note。escalate の値を `判定不能` に。「公開情報」は既にあった「その他」のシナリオの note に移した | 5件（禁止2・その他2・escalate1） | 2／3／1／**0** |
| `mhlw_r7dec_art21_3` | **「産後１年」→「産後1年」**（D56・D-3。値・セル・シナリオ・evidence の basis を normalized／半角化に）。3つの fact に other_value_note。escalate の値を `判定不能` に。「所定労働」を「その他」のシナリオに移した | 6件（禁止2・ワイルドカード列1・その他2・escalate1） | 6／4／1／**0** |
| `aoi_johokanri_art14` | **`compared_table_id = dt_transmission`・`relation = disjoint`**（D58・E-3）。review.notes の該当2件を書き直した | 4件（許可1・既定の行2・escalate1） | 4／1／0／**0** |

### escalate と「その他」の値（D54）

| gold_id | escalate の値 | 「その他」に移した値 |
|---|---|---|
| `ipa_secguide_v4_6_7_1` | `target_environment = 判定不能` | 検証環境 |
| `mhlw_r7dec_art11_1` | `usage_target = 判定不能` | 社員の私物 |
| `ipa_secguide_v4_personnel_2` | `provided_information = 判定不能` | 公開情報 |
| `mhlw_r7dec_art21_3` | `work_type = 判定不能` | 所定労働 |
| `aoi_johokanri_art14` | `birth_match = 未照合`（**そのまま**） | なし（比較型の fact だけなので「その他」が存在しない） |

移した側の note には「当てはめる側が○○を『その他』と判断した場合（D54）」と、第24回まではこの値を知らない値のシナリオに使っていた旨を書いた。

- **expected_gates は5件すべて変わらなかった**（approved_by は None のままなので。D53 のとおり）。
- 第14条の evidence に「既定の行」が0件なのは、この条文の既定の行（禁止）には逐語の根拠があり `verbatim` で書いてあるためである（第24回から変わっていない）。

---

## 4. パートC の結果

- **5件すべてを approved にした。** draft のまま残したものはない。パートB の手直し以外の変更は加えていない。
- 記録した内容
  ```json
  "review": {
    "status": "approved",
    "reviewed_by": "Kazuhiro",
    "reviewed_at": "2026-11-02",
    "decisions": [ { "id": "G-1", "point": "…", "result": "…" }, … ],
    "notes": [ … ]
  }
  ```
- `review.decisions` の形は **`{id, point, result}` の配列**にした。共通の G-1〜G-6 を先頭に置き、その条文の判断点（A-1〜A-3／B-1〜B-4／C-1〜C-4／D-1〜D-4／E-1〜E-5）を続ける。変更になった A-2 は `result` に「**変更。** 親は None にして位置づけを人の判断に回す（D60）」と書いた。
- `human_review_status` は5件すべて `pending` のまま（2節 A1 に書いたとおり、違和感はないが欄名が紛らわしい）。

---

## 5. 新しく見つかった型・実装の穴

1. **`FactDef.possible_values` が、比較型では「書いてはいけない欄」になった。** 型の上では `list[str]` のままで、空であるべきことは C4 でしか分からない。層3とバインディング設定には導出された値が入るので、**同じ欄が「入力では空」「出力では導出値」の二役**になっている。前段の自動化で LLM にこの型を見せるときに混乱しそうなので、`possible_values` を passthrough 専用にして比較型は欄ごと持たない形（または `declared_values` と `effective_values` に分ける形）を検討したい。
2. **`other_value_note` が passthrough 専用の欄なのに、型では全 fact が持つ。** 1 と同じ形の問題である。C4 が「passthrough なら必須・比較型なら None」を見ているだけで、型では表せていない。
3. **`run_ir_gold` の「その他」のシナリオの要求が、fact 単位ではなく IR 単位である。** いまは「passthrough の fact を1つでも使うなら、『その他』のシナリオが1件以上」を求める。第11条のように passthrough の fact が3つある条文で、1つの fact にしか「その他」のシナリオが無くても通る。fact ごとに求める形に強めるべきかは判断をお願いしたい（強めると、値の多い条文でシナリオ数が一気に増える）。
4. **初期登録の `other_value_note` が固定の文で、根拠がない。** `contact_consent` の「その他」が実際に何を指すのか（連絡先の同意が未回答か、そもそも連絡先がないか）は元の DMN から読めない。U11（あおいの fact の取得元）と同じ性質の空欄で、語彙の承認（U8）のときに埋める必要がある。
5. **`ColumnSetRelationKind` の `disjoint` が「相手なし」と併用できない。** `compared_table_id = None` のとき `relation` も None になるので、「比べられる相手がそもそも1枚もない」（初めてのモデル）と「相手はあるが列が重ならない」は区別できるようになったが、**「相手が複数あり、どれとも重ならない」ときに1枚しか書けない**。第14条はあおいの最終判定が1枚だけなので足りているが、モデルが増えると「どれと比べたのか」の記録として弱くなる。
6. **設計書3.8節の例が D60 と食い違っている**（1節に記載）。

---

## 6. push したコミットのハッシュとテストの件数

| 項目 | 値 |
|---|---|
| コミット | `550f5db` |
| テスト | **187件**（`test_cross_validation.py` 57・`test_bootstrap_fixes.py` 42・`test_acceptance_paths.py` 34・`test_ir_gold.py` 32・`test_golden_path.py` 22）。**skip 0件** |
| `run_ir_gold` | 正解IR 5件: 通過 5件／失敗 0件、シナリオ **26/26** 一致、evidence の根拠なし 全件0件 |
| `run_golden_path --use-oracle` | クロス検証通過、シナリオ 4/4 一致 |
| `registry/registry.json` | 層3の7件のみ変更（2節 A5 の表）。層1・層2・`registry/dmn/aoi_transmission.dmn` は無変更 |

---

## 7. 次に相談したいこと

- 正解IR 5件の承認が終わったので、**前段（条文 → IR）の自動化**に入れるか。設計書6.3節は Action 正規化から始めるとしている。
- 5節の穴1・2（`possible_values` と `other_value_note` の二役）は、前段の自動化で LLM にこの型を見せる前に形を決めておきたい。
- 正解IR の型の充足（設計書6.1節）は第24回から変わっていない。**レベル2（`add_rows`）・`no_change`・`value_additions`・中間判定を使う条文は、実在の条文では埋まっていない**（テスト用の第901〜906条でのみ確認している）。

---

## 8. このドキュメントの位置づけ

過去のHANDOFFファイルと同様、これは特定時点のスナップショットです。今後の議論で決定事項が更新された場合も、本ファイルはそのまま残し、更新が必要な場合は新しい日付・ラウンド番号のファイルを追加する運用とする。

---

## 9. claude.aiチャットへの引き継ぎメッセージ

```
aoi_doc2dmnで、第25回（設計書 v1.6 の実装、正解IRの手直しと承認の記録）が完了しました。
HANDOFF_20261102_round26_to_web.md を読んで、結果を把握してください。
コミット 550f5db を push 済みです。前段（条文→IR）の自動化に進めるかを相談したいです。
```
