# aoi_doc2dmn 引き継ぎ資料（第28回）

**作成日**: 2026年11月3日
**引き継ぎ元**: Kazuhiroさんのサーバ環境上のClaude Code
**引き継ぎ先**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）

**位置づけ**: 第27回（`HANDOFF_20261103_round27_to_claudecode.md`）のパートA（設計書 v1.7 の実装）と
パートB（テストデータの候補の抜き出し）の結果報告。設計書は v1.7 に差し替えた。

---

## 1. 設計書 v1.7 から外れた点（3件）と、設計書側の食い違い（1件）

### 逸脱1：比較型の possible_values の歯止めは C4 ではなく「読み込み時」に置いた

設計書5章 C4 は「比較型の new_facts に possible_values がある場合も停止」を
「JSON を読み込むときの歯止めとして残す」と書いている。しかし D62 で型を分けた結果、
`ComparisonFact` には `possible_values` の欄が無いので、**読み込んだ後の C4 では見られない**
（欄が無いものは検査できない）。そこで `fact_def_from_json` が読み込みの時点で
`IRTypeError` を投げる形にした。黙って読み捨てると、その値を参照する行が C5 をすり抜ける。

- 止める対象：IR の `new_facts`（`origin="ir"`。既定）
- 止めない対象：登録簿の層3（`origin="registry"`）。層3には導出した値が**入っているのが正しい**
  （設計書4.5の出力の形）ため、その欄は読み捨てる
- 直す場所：設計書5章 C4 の文を「比較型の possible_values は読み込みの時点で止める」に変えるか、
  「C4 は読み込みを含む」と読み替えられる書き方にしてほしい

### 逸脱2：other_value_note は型の上では必須だが、読み込みでは None を通す

設計書3.6節の `PassthroughFact.other_value_note` は既定値の無い `str` である。実装も同じにしたが、
`fact_def_from_json` は欄が無い JSON を読んだとき **None を入れて読み込み、C4 で止める**。
読み込みで投げると、停止の理由が「キーがない」になり、「『その他』が指す状態を書いてください」
という C4 のメッセージが出せないからである（Python の dataclass は型注釈を実行時に検査しない）。

- 結果として、`PassthroughFact` の `other_value_note` は「型では必須・実行時は C4 が保証」になる
- 直す場所：なし（設計書の意図どおりに止まる）。ただし下の「新しい穴」1に挙げた

### 逸脱3：D63 の「既定の行に落ちる」を kind の文字列ではなく出力の一致で見た

D63 は「『その他』が既定の行に落ちる場合を fact ごとに1件以上」と定める。シナリオの `kind` の
文字列で見ると、**条文の禁止の行にワイルドカードの列があるシナリオを数えてしまう**
（第21条第3項の「ワイルドカードの列に当たる(請求の有無を問わない)」が実例で、これは
`exemption_request = その他` だが禁止の行に当たる）。そこで次の順で見る。

1. IR の中に既定の行（条件がすべて「-」）があれば、シナリオの期待する出力がその行の出力と
   一致するかで見る（構造で見る）
2. IR の中に既定の行が無い場合（既存の表に行を足す IR。既定の行は IR の外にある）だけ、
   `kind` が「その他」と「既定の行／ワイルドカード」の両方を含むかで見る

- 直す場所：設計書6.2 手順1 に「既定の行に落ちることは、期待する出力が既定の行の出力と
  一致することで確かめる」と足してほしい

### 設計書側の食い違い：3.8節の第14条の例が、承認済みの正解IR と合っていない

3.8節の後半（中間判定を使う第14条のレベル判定の表）は、次のようになっている。

| 層 | 設計書3.8節 | 承認済みの正解IR（`aoi_johokanri_art14`） |
|---|---|---|
| ② 中間判定 | table_id = dt_identity、action = add_row、new_output_values = {identity_level: [中（氏名なし・2要素一致）]} | **空**（D49 により、この条文のために共有の中間判定は変えない） |
| ③ 最終判定 | compared_table_id = dt_transmission、**relation = proper_subset** | compared_table_id = dt_transmission、**relation = disjoint**（D58・第25回レビュー E-3） |
| 読み方 | 「列の上では送信可否判定に行を足せるが、行為が違うので新しい表を作る」 | 送信可否判定の列（機微度・確認レベル・連絡同意）とこの表の列（生年月日・連絡先の照合）は**1つも重ならない** |

D49（第23回）と E-3（第25回）で正解IR を作り直したときに、3.8節の例が追従していない。
前段に例として見せる文なので、差し替えをお願いしたい。前半（特権アカウントの IR）は
v1.7 で直っており、`golden_path.py` と正解IR と一致していることを確かめた。

---

## 2. パートA の結果

### A1：FactDef を2つの型に分けた（D62）

`src/stage3_generate/ir_schema.py`

| 型 | 欄 |
|---|---|
| `PassthroughFact` | variable・possible_values・other_value_note（必須）・sources・approved_by・primitive（`Literal["passthrough"]`） |
| `ComparisonFact` | variable・primitive・sources・comparison_kind・approved_by |
| `FactDef` | `Union[PassthroughFact, ComparisonFact]`（型の別名） |

- **JSON の形は変えていない。** `primitive` が `passthrough` なら `PassthroughFact`、
  それ以外は `ComparisonFact` として読む（`fact_def_from_json(payload, *, origin="ir")`）
- **層3とバインディング設定の出力も変えていない。** `registry/registry.json` を初期登録から
  作り直して**バイト単位で差分なし**を確認した。比較型の `to_json()` はプリミティブの定義から
  導いた値を `possible_values` として出す（D62「どちらの型でも、実際に使う値の一覧を出力する」）
- 新しい補助関数：`effective_values_of(fact)`（型に応じて「その他」付きか導出値かを返す）・
  `is_passthrough_fact(fact)`・`IRTypeError`

**整理した分岐（報告依頼事項）**

| 場所 | 前 | 後 |
|---|---|---|
| `wiring_generator._check_one_fact_sources` | `has_other_value(fact.primitive)` と `derived_possible_values(fact.primitive) is not None` の2分岐 | `isinstance(fact, PassthroughFact)` の1分岐（比較型の検査は読み込みへ移動） |
| `wiring_generator._collect_values_for_c12`・`_check_c5_*` | `effective_possible_values(fact.primitive, fact.possible_values)` | `effective_values_of(fact)` |
| `wiring_generator._merge_registry`（層3） | 同上 | `effective_values_of(fact)` |
| `run_ir_gold._check_envelope`・`_check_halfwidth_digits` | `has_other_value(fact.primitive)` | `isinstance(fact, PassthroughFact)` |
| `run_ir_gold._has_passthrough_fact`（bool） | 条文単位の有無 | `_passthrough_facts_in_tables`（fact の一覧。D63 で必要になった） |
| `bootstrap_aoi`（層3の組み立て） | `primitive == "passthrough"` の比較が3箇所 | `is_passthrough` を1度求めて使う |
| `tests/conftest.passthrough_fact` | `FactDef(primitive="passthrough", …)` | `PassthroughFact(…)` |

`bootstrap_aoi` だけは型を使わず dict を組む形を残した。層3のエントリは `defined_by`・`used_by`・
`provenance`・`history` を持ち、元の DMN XML から直接組むため、型にすると結局 dict に戻す必要があり、
かつ出力を1バイトも変えない要求（D62）を満たしにくい。**要判断**：ここを型に寄せるべきか。

### A1 続き：C4 の扱い

C4 に残したもの（JSON を読み込むときの歯止め、および読み込み後に欄が空になった場合の歯止め）。

| 検査 | どこで止めるか |
|---|---|
| passthrough の `new_facts` に `other_value_note` がない（D55） | C4（型では必須だが、読み込みは None を通す。逸脱2） |
| `possible_values` に予約語「その他」を書いた（D45） | C4（passthrough のときだけ見る） |
| `new_facts` で `identifiability_classifier` を使った（D23） | C4 |
| `sources` のキーがプリミティブの引数名と一致しない・未知のプリミティブ（3.5節） | C4 |
| 比較型に `possible_values` がある（D59・D62） | **読み込み時**（`IRTypeError`。逸脱1） |
| 比較型に `other_value_note` がある（D55・D62） | **読み込み時**（`IRTypeError`。新設） |

### A2：「その他」のシナリオを fact ごとに求めた（D63）

`run_ir_gold` の確認を fact 単位にした。**足りなかったのは1件だけだった。**

| 正解IR | 決定表で使う passthrough の fact | 足したシナリオ |
|---|---|---|
| `ipa_secguide_v4_6_7_1` | 3件 | 0件（3件とも揃っていた） |
| `mhlw_r7dec_art11_1` | 3件 | 0件 |
| `ipa_secguide_v4_personnel_2` | 2件 | 0件 |
| `mhlw_r7dec_art21_3` | 3件 | **1件**（`worker_category = その他` → 許可） |
| `aoi_johokanri_art14` | 0件（比較型のみ） | 0件（要求されない） |

第21条第3項に足したシナリオ：

> 名前：労働者の区分が条文の挙げる区分でない → 許可
> facts：worker_category = その他、exemption_request = 請求した者、work_type = 時間外労働
> 期待：許可／not_prohibited_combination

`worker_category` が「その他」になるシナリオは1件あったが（第3項の2行目に当たる
「ワイルドカードの列」のケース）、それは**禁止の行に当たる**ので D63 を満たしていない。
これが D63 が実際に拾った穴である。

**承認との関係**：IR の本体（`ir`）は変えていないので `review.status` は `approved` のまま。
`review.history` を新設して記録した（第27回 2節 A2 の形）。

```json
{"change": "scenario_added", "reason": "D63", "at": "2026-11-03", "by": "claude_code",
 "scenarios": ["労働者の区分が条文の挙げる区分でない → 許可"],
 "note": "…IR の本体(ir)は変えていないので review.status は approved のまま"}
```

draft に戻した正解IR は**ない**。`ir` を変えた正解IR も**ない**（差分は scenarios と
review.history の26行だけ）。

### A3：第26回の判断と3.8節の確認（D64）

- `disjoint` × `final.action = add_rows` → C9条件2 で停止。`disjoint` × `intermediate.action = add_row`
  → C9条件3 で停止。**設計書 D64 の記述と一致**（コメントの「要報告」を D64 の採用に書き換えた）
- 値の全角数字 → `run_ir_gold` で停止。対象は possible_values・決定表のセル・中間判定に足す出力値・
  シナリオの値。条文本文と evidence の引用は対象外。**D64 の記述と一致**
- 3.8節の例（特権アカウントの IR）と `golden_path.py`・`ipa_secguide_v4_6_7_1` の一致を確認した
  （`parent` が null、3つの fact に `other_value_note` あり、possible_values は条文の値だけ）。
  3.8節の後半（第14条）は食い違っている（1節に書いた）

---

## 3. パートB の結果（D65）

### 3.1 出力

- `testdata/candidates/candidates_round3.json`（候補 37件・話題 7件）
- `testdata/candidates/REVIEW_round3.md`（話題ごとのレビュー用一覧）
- `testdata/clauses/clauses_v1.json` は**変更していない**

**正解ラベルは付けていない。** `classifier_result` は②の分類器（`local_llm.py`・
qwen2.5:14b-instruct）にかけた参考値を入れる欄で、正解ラベルではない。
**この実行は時間がかかっており（1件あたり2回の LLM 呼び出しで約5分）、別のコミットで埋める。**
候補の抜き出しと仮説は分類器の結果に依存していない（かけない場合は空でよい、という
第27回 3.2節の扱い）。

### 3.2 話題の一覧

| 優先 | 話題 | 件数 | 出典 |
|---|---|---|---|
| 1 | 特権操作・特権アカウント | 5 | IPA 6.1・6.7・6.10 |
| 1 | 妊産婦・年少者の就業制限 | 5 | 厚労省 第21条・第25条・第58条 |
| 2 | 機密性区分に応じた情報の社外共有・送信 | 5 | IPA 5.6・7.3 |
| 2 | セキュリティ領域と室内での注意 | 6 | IPA 物理的対策 1.2・3 |
| 3 | 私有機器・私用アドレスの業務利用 | 6 | IPA 人的 6.1.2・技術 2.3 |
| 3 | 就業規則の遵守事項と機密情報の取扱い | 5 | 厚労省 第11条・第16条 |
| 4 | 利用者アカウントの発行と共有 | 5 | IPA 6.2.1・6.3・6.4・6.6 |

### 3.3 優先する話題とその理由

- **特権操作（優先1）**：ゴールデンパスと同じ Action「特権操作」を扱う条文がある
  （`ipa_secguide_v4_6_1_5`「特権操作は多要素認証を用い、社有機器及び指定経路（VPN等）から
  のみ実施し、私有機器を使用しないこと。」）。**既存の Action に寄せる判断**を直接試せる唯一の話題。
  同じ節（6.7）の他の箇条が4件あり、既存の最終判定 `dt_privileged_operation` との列の比べ方も出る
- **妊産婦・年少者（優先1）**：第21条**第4項**が、既存の正解IR（第3項）と同じ fact
  （`worker_category`・`exemption_request`・`work_type`）を使い、`work_type` に
  「所定労働時間外労働」を足して同じ表 `dt_overtime_assignment` に行を足す形になる見込み。
  **レベル2・値の追加・既存 fact の再利用が1件でそろう**候補で、第26回まで実在の条文で
  埋まっていなかった型をここで埋められる
- **機密性区分（優先2）**：あおいの既存の最終判定 `dt_transmission`（送信可否判定）と同じ話題。
  機密性区分（公開／社外秘／極秘）＋相手の合意という形で、既存 fact `contact_consent` に
  近い fact と既存の表への行追加が見込める
- **セキュリティ領域（優先2）**：領域の区分（レベル1／2／3）という**1つの fact に値が3つ**付き、
  同じ表に行が増える形になりそう。既存 fact への値の追加を試せる。あおいの第14条（口頭伝達）と
  同じ話題の条文（`ipa_secguide_v4_phys_3_phone`「外線受話時の際に相手が不審な場合は、
  従業員の個人情報を伝えてはならない。」）もこの話題にある

### 3.4 既存の Action・fact・表との関係が見込める候補

| candidate_id | 見込み |
|---|---|
| `ipa_secguide_v4_6_1_5` | 既存の Action「特権操作」に寄せる。`dt_privileged_operation` とのレベル判定 |
| `mhlw_r7dec_art21_4` | 既存 fact 3件の再利用＋`work_type` への値の追加＋`dt_overtime_assignment` への行追加（レベル2） |
| `mhlw_r7dec_art25_2` | `worker_category` への値の追加（産後8週間を経過していない女性労働者） |
| `mhlw_r7dec_art58_3_4` | 年齢の区分の fact。第21条第3項の「18歳未満の者」と境界が違う値を同じ fact に足すか |
| `ipa_secguide_v4_5_6_share` | `dt_transmission`（送信可否判定）と同じ話題。`contact_consent` に近い fact |
| `ipa_secguide_v4_phys_3_phone` | 既存の Action「口頭伝達」に寄せる（あおいの第14条と同じ話題） |
| `mhlw_r7dec_art11_5`・`mhlw_r7dec_art16_1` | 既存の Action「情報提供」に寄せる（IPA の営業秘密の条文と同じ話題。**文書をまたぐ**） |
| `mhlw_r7dec_art11_2`・`_3`・`_5`・`_6` | 既存の表 `dt_facility_property_use`（第11条第1項）に行を足すか |
| `ipa_secguide_v4_phys_1_2_level1`・`level2`・`level3_carry_in` | 1つの fact（領域の区分）に値が3つ。既存 fact への値の追加と行追加 |
| `ipa_secguide_v4_2_3_3_forward`・`_send` | 同じ fact（宛先の区分）で行為だけが違う。Action 正規化で同じ Action に寄せるか |

### 3.5 抜き出しで迷った点

1. **1文に複数の規範が入っている条文がある**（`6_1_5`・`6_7_item2`・`art11_3`・`art21_4`・
   `art16_1`）。「1つの規範を1件」という単位と衝突するが、原典が1文・1項にしているので
   **文を切らずに1件**にした。切るべきなら次回に分ける
2. **表のセルから取った候補は、区分がセルの外にある**（`6_10_priv_name`・
   `phys_1_2_level1`・`level2`・`level3_carry_in`）。「どの領域の制限か」「特権アカウントの
   条件か」が行・列の見出しにしかない。正解IR を作るときは**出典付きの context** として
   渡す必要がある（設計書1.4節）
3. **原典に「青字の選択肢」が残っている条文がある**（`2_3_1`「システム管理者の許可を得る/
   利用することを禁止する。」・`7_3_attach`・`2_3_3_wifi`）。どちらを採るかで軸1のラベルが
   変わる。**選ばずに逐語で写した**
4. **評価の判断を含むおそれのある語**：「必要最低限」（`6_7_item1`・`item2`）・「原則禁止」
   （`personnel_6_1_2`）・「不審な」（`phys_3_phone`）・「十分注意を払う」（`art16_1`）。
   D51 で正解IR から外す対象になりうるが、**境界を試せるので候補に残した**。
   明らかなもの（第11条④「名誉や信用を損なう」・⑦「ふさわしくない」）は入れていない
5. **同じ文が2か所にある**：「情報機器・設備の無断操作禁止・無断持出し禁止」はレベル2領域と
   レベル3領域の両方にある。ここでは**レベル2の側だけ**を候補にした。1つの規範が2つの値に
   当たる形にするか、2件に分けるかは判断が要る
6. **candidate_id の命名が既存と合わない**：既存の `ipa_secguide_v4_6_7_1` は 6.7 の**第3項**、
   `ipa_secguide_v4_6_7_5` は**第5項**で、id の数字が項番号と一致していない。新しい候補は
   `item<項番号>` にしたが、既存の2件をそろえるかは判断をお願いしたい
7. **主語の向きが混ざる**：厚労省側は使用者が主語の条文（`art21_4`・`art25_2`。U12）と
   労働者が主語の条文（`art11_*`・`art58_3_*`）が同じ話題に並ぶ
8. **箇条書きの親文に対象が書かれている条文がある**（`2_3_3_store`・`6_2_1_item1`・
   `phys_3_photo`）。本文だけでは「何に対する禁止か」が決まらない

---

## 4. 新しく見つかった型・実装の穴

1. **`other_value_note` は型では必須だが、実行時には検査されない。** dataclass は型注釈を
   実行時に見ないので、JSON から None が入る（逸脱2）。C4 が止めるが、型の約束と実装の間に
   すき間が残る。前段の LLM にスキーマを見せるときは「必須」と伝わるので実害は小さいが、
   「型で表した」とは言い切れない
2. **層3のエントリ（dict）には型がない。** `fact_def_from_json(origin="registry")` は
   比較型の `possible_values` を読み捨てる。初期登録のときは定義の外の値があれば止めている
   （D59）が、**IR 経由で層3に入った比較型の値が定義とズレても気付けない**
3. **D63 の「既定の行に落ちる」の判定は、既存の表に行を足す IR では弱い**（逸脱3の2段目）。
   既定の行が IR の外にあるため `kind` の文字列に頼る。正解IR がこの形になったときに
   はっきりする
4. **`ComparisonFact.comparison_kind` を誰も書いていない。** 初期登録は null、正解IR でも
   使っていない。「語彙登録時に人が付ける注釈」(3.3節)なので U8 で使うのだと思われるが、
   いつ・誰が書くかが決まっていない
5. **`disjoint` × `intermediate.action = add_column` は止めていない。** D64 は `add_rows` と
   `add_row` だけを挙げている。列が1つも重ならない表に列を足すことに意味があるかは決まっていない
6. **U14（比べた相手が複数ある場合）は未解決のまま。** 第14条は `dt_transmission` 1つだけを
   書いているが、あおいには最終判定が他にもある

---

## 5. コミットとテスト

| 項目 | 値 |
|---|---|
| コミット | （このファイルの最後に追記） |
| テスト | pytest 196件・skip 0件 |
| 正解IR | 5件すべて通過・シナリオ 27/27 一致・evidence の「根拠なし」0件 |
| ゴールデンパス | `run_golden_path --use-oracle` 4/4 一致 |
| 登録簿 | `registry/registry.json` は初期登録から作り直して差分なし |

テストの内訳：`test_cross_validation.py` 60件／`test_bootstrap_fixes.py` 42件／
`test_acceptance_paths.py` 34件／`test_ir_gold.py` 38件／`test_golden_path.py` 22件。

v1.7 で足したテスト：

- `test_fact_json_dispatches_on_the_primitive`（D62。JSON の形は変えずに型が決まる）
- `test_comparison_fact_cannot_declare_possible_values`（D62。読み込みで止まる・層3では止めない）
- `test_comparison_fact_cannot_declare_an_other_value_note`（D62）
- `test_effective_values_follow_the_fact_type`（D45・D59・D62）
- `test_other_value_scenario_exists_for_every_passthrough_fact`（D63。正解IR 5件）
- `test_the_d63_check_catches_a_missing_other_value_scenario`（D63 の確認そのものの失敗テスト）

---

## 6. 次に相談したいこと

1. **設計書3.8節の第14条の例の差し替え**（1節）。前段に見せる例なので早めに直したい
2. **候補のレビューの進め方**（パートB）。37件の軸1・軸2のラベルを Kazuhiroさんが確認する
   手順をどう回すか。優先する話題（特権操作・妊産婦）の10件だけ先に確認して正解IR に進む、
   という順でもよいか
3. **候補の単位**（3.5節の1）。1文に複数の規範が入る条文を切るかどうか
4. **`bootstrap_aoi` を型に寄せるか**（2節 A1）
5. 前段（条文 → IR）の設計書の進み具合

---

## 7. このドキュメントの位置づけ

過去のHANDOFFファイルと同様、これは特定時点のスナップショットです。今後の議論で決定事項が
更新された場合も、本ファイルはそのまま残し、更新が必要な場合は新しい日付・ラウンド番号の
ファイルを追加する運用とする。
