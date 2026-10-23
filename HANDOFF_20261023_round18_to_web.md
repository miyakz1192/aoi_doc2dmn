# aoi_doc2dmn 引き継ぎ資料（第18回／Claude Code → claude.ai）

第17回 HANDOFF（`HANDOFF_20261023_round17_to_claudecode.md`）のタスク1〜5を実装した。
設計書は v1.2（`docs/design/aoi_doc2dmn_IRv2_design.md`）に差し替え済み。
報告は第17回 5節の8項目の順で書く。

---

## 1. 設計書 v1.2 から外れた点

実装を進めるために判断で決めた点を、理由と一緒に挙げる。**6件ある。**

| # | 設計書の記述 | 実装 | 理由 |
| --- | --- | --- | --- |
| 1 | 4.1節「IR が変更した既存の表（rule_row_additions・**intermediate の table_id**・**final の compared_table_id**）の判定を起点にする」 | `intermediate.action = "reuse_value"` の表と、`final.action = "new_table"` のときの `compared_table_id` は起点に含めない | どちらも「変更した表」ではない。既存の出力値をそのまま使う（reuse_value）、既存の表と列集合を比べて新しい表を作ると決めた相手（new_table の compared_table_id）であり、その表には何も足さない。含めると、新しい表を作るだけの IR が既存の DMN ファイルを要求して止まる |
| 2 | 5章「生成前 C3 → C9 → C4 → **C6** → C10 → C11 → C2」 | C6 を「実在」と「網羅」に分け、網羅だけを `required_facts` の導出の直後に動かした | D21 の網羅は `required_facts` を必要とするが、その導出は C9・C10 の後にしか始められない。実在の確認は指定どおりの位置に残した |
| 3 | 5章 C6「IR が使う既存 fact（required_facts のうち new_facts にないもの）がすべて referenced_facts にある」 | 基準を **IR 自身のルート**（`routes[0]`）の `required_facts` にした。D16 で作り直す既存の最終判定の分は含めない | 含めると、この条文が使っていない fact の宣言を要求してしまう。たとえば本人確認判定に行を足す IR に、情報分類判定の `has_medical` などの宣言を強いることになる |
| 4 | 5章 C1「required_facts が全て new_facts か層3にある」 | 検証自体は残したが、**通しで流すと必ず C6 が先に止める**ため、失敗テストは検証関数を直接呼ぶ形にした | D21 で C6 が網羅まで見るようになり、C1 の違反は「宣言が無い→C6網羅」「宣言がある→C6実在」のどちらかに必ず先に当たる。C1 は多重の歯止めとして残置 |
| 5 | 4.5節 層2「出力の値は層2の行の outputs から集める」 | C11 で値を比べる前に、FEEL の表記（`"高"`）を外して素の値（`高`）に揃える | 初期登録した層2は DMN の表記のまま（`"高"`）入るのに対し、IR の行は素の値（`高`）を持つ。揃えないと同じ値が別物に見え、正しい行を C11 が落とす（実装中に実際に踏んだ） |
| 6 | 4.1節 required_facts の手順（層1の input_columns から始める） | 層1から引くときに、その IR が `add_column` で足す列を加える | 加えないと、足した列が `required_facts` から漏れ、実行時に誰もその事実を集めない（第16回の報告 #6 そのもの）。D19 の意図に沿わせた |

このほか、**DMN に無い情報を2か所で扱った**。判断で入れたので、是非を確認したい（7節 #1・#2）。

---

## 2. 初期登録（bootstrap）の結果

- **元にした DMN**：`reference/gijinka-extraction-pipeline/dmn/aoi_transmission_baseline.dmn`
  - 同じディレクトリに `aoi_transmission.dmn` もあるが、そちらは第14条を適用済みで
    `decision_verbal_disclosure` と `dt_identity` の7行目が入っている。中間判定への行追加の
    テストが「足す行が既にある」状態になるため、**baseline を使った**
- **置いた DMN**：`registry/dmn/aoi_transmission.dmn`（**書き換えずにそのままコピー**）
- **登録簿**：`registry/registry.json`（`provenance.origin = bootstrap`）
- **再実行**：`.venv/bin/python3 -m src.stage3_generate.bootstrap_aoi`
  （`src/stage3_generate/bootstrap_aoi.py`。登録時刻を固定値にしてあるので、何度実行しても
  バイト単位で同じ結果になることを確認済み）

### 層1（決定表）3件

| decision_table_id | decision_id | 判定名 | input_columns | output_columns | entry_points |
| --- | --- | --- | --- | --- | --- |
| dt_identity | decision_identity | 本人確認判定 | name_match, birth_match, phone_match | identity_result, identity_level | （空＝中間判定） |
| dt_classification | decision_classification | 情報分類判定 | has_medical, has_pii, identifiable | info_class, sensitivity | （空＝中間判定） |
| dt_transmission | decision_transmission | 送信可否判定 | 情報分類判定.sensitivity, 本人確認判定.identity_level, contact_consent | decision, reason_code | あおい × 外部送信 |

### 層2（ルール行）18件

dt_identity 6行・dt_classification 5行・dt_transmission 7行。セルは **DMN の表記のまま**
持たせた（`"中","未","不一致検出","情報不足"` のような複数値を1つの文字列に落とさないため）。

### 層3（fact）7件

| variable | primitive | possible_values | used_by |
| --- | --- | --- | --- |
| name_match | fuzzy_match_name | 一致, 不一致 | dt_identity |
| birth_match | date_match | 一致, 未提供, 不一致 | dt_identity |
| phone_match | exact_match | 一致, 未提供 | dt_identity |
| has_medical | ontology_class_match | 含む, 含まない | dt_classification |
| has_pii | ontology_class_match | 含む, 含まない | dt_classification |
| identifiable | identifiability_classifier | 可能, 不可能_院内, 不可能_公開 | dt_classification |
| contact_consent | passthrough | 不同意, 同意 | dt_transmission |

`possible_values` は決定表のセルに出てくる値だけ（D4）。`primitive` は **DMN XML に書かれて
いない**ため、設計書3.5節の表（「例」の列があおいの fact を名指ししている）を出典として
付与した。`identifiable` の `identifiability_classifier` は D23 のとおり初期登録分のみ。

### あおいの DMN で見つかった不足・矛盾 5件（補っていない）

`bootstrap_aoi.find_problems()` が機械的に検出し、実行時に一覧表示する。

1. **参照されない inputData**：`name="added by me"`（id `_F5C2B769-67AA-46E7-8F18-05CB2B52DB0D`）が
   どの決定表からも参照されていない。おそらく作業中の残骸
2. **絶対に当たらない値**：`dt_transmission/tr_r3` と `tr_r5` が
   `本人確認判定.identity_level = "未"` を参照しているが、**本人確認判定が identity_level に
   出すのは 高／中／不一致検出／情報不足 だけ**。`未` は `identity_result` の値である
   （列を取り違えたと思われる）。どちらの行も他の値（中・不一致検出・情報不足）で当たるので
   表は動くが、**これはまさに C11 が狙っている「綴り違い・列の取り違えで死ぬ値」の実例**
3. **使われない出力**：`本人確認判定.identity_result` と `情報分類判定.info_class` を
   どの決定表も参照していない。中間判定の出力なので、使わないなら不要
4. **sources が無い**：層3の `sources`（一致プリミティブの引数の取得元）が DMN XML から
   取れない。補うと「どのマスタのどの項目と比べたか」を実装者が創作することになるので、
   **空のまま**にして層3の provenance に理由を書いた（7節 #1）
5. **entry_points が無い**：DMN XML に actor も action も書かれていない（7節 #2）

**2 は実害がありうる**（`未` を直して `識別レベルの不足` を正しく表したいのか、`中` の
扱いを変えたいのかで表の意味が変わる）。設計側の判断を仰ぎたい。

---

## 3. 受け入れ経路の実装状況

| 経路 | 状況 | 実装 |
| --- | --- | --- |
| 既存モデルへの行の挿入（D15） | 実装済み | `patch_existing_dmn()`。`position` の位置に `<rule>` を挿入。同じ表に複数行なら position の昇順。行 ID の重複・入力列に無い条件キーは `SchemaWiringError`（綴り違いの列が「-」として黙って無視されるのを防ぐ） |
| 列の追加（D19） | 実装済み | `_add_input_column()`。`<input>`・`<inputData>`・`<informationRequirement>` を足し、既存の全 `<rule>` に「-」の `<inputEntry>` を追加。層1の `input_columns` と層2の既存行（`conditions` に `"-"`）にも反映し、層1の `history` に `column_addition` を残す |
| 出力値の追加 | 実装済み | `intermediate.new_output_values` を C11 の「参照先が出す値」に足す。DMN XML の構造は変えない |
| D16 の作り直し | 実装済み | `derive_routes()`。変更した既存の表 → 層1の `depends_on_decisions` の逆引き（`Registry.dependents_of()`）→ `entry_points` を持つ判定だけを最終判定として採る。中間判定は `entry_points` が空なので自然に除かれる |
| D17 の停止 | 実装済み | `_check_d17_same_dmn_model()`（ドット参照の参照先・行列を足す既存の表）と `derive_routes()`（作り直す最終判定）の2か所。どちらも `SchemaWiringError` |

### 複数ルートを出力するときの形（判断に任された点）

**どちらも1ファイルにまとめた。**

- **ルーティング表**：`{"routes": [...]}` に複数行を入れる。**先頭は必ず IR 自身のルート**で、
  以降が D16 で洗い出した既存の最終判定。ルーティング表は「actor×action から判定を引く
  1枚の表」なので、分割すると引く側が全ファイルを読む必要が出る
- **バインディング設定**：`{"dmn_model": ..., "facts": [...]}` のまま、全ルートの
  `required_facts` の**和集合**を入れる（設計書4.3のとおり）。順序は先頭のルートから
  現れた順で決定的

ファイル名は `<dmn_model>_routing.json` / `<dmn_model>_bindings.json` のまま変えていない。

---

## 4. C1〜C11 の実装状況と失敗テスト

全68件。`.venv/bin/python3 -m pytest tests/ -v`（Ollama 不要）。

| # | 状況 | 失敗テスト（`tests/test_cross_validation.py`） |
| --- | --- | --- |
| C1 | 実装済み（検証関数を直接呼ぶ。1節 #4） | `test_c1_missing_fact_definition` |
| C2 | 実装済み | `test_c2_unused_new_fact` / `test_c2_allows_fact_used_only_by_row_addition`（弾かれないこと） |
| C3 | 実装済み | `test_c3_rejected_compound_in_primitive` |
| C4 | **D23 を追加** | `test_c4_unknown_primitive` / `test_c4_sources_key_mismatch` / **`test_c4_identifiability_classifier_rejected_in_new_facts`** |
| C5 | 実装済み（`ValueAddition` 対応） | `test_c5_value_not_in_possible_values` / `test_c5_unsupported_feel_cell` / `test_c5_accepts_feel_literal_list` / `test_c5_value_additions_are_allowed` |
| C6 | **D21 の網羅を追加** | `test_c6_referenced_fact_not_in_registry` / `test_c6_value_addition_key_not_in_registry` / **`test_c6_missing_referenced_fact_declaration`** |
| C7 | **add_rows に対応（D15）** | `test_c7_entry_decision_not_generated` / **`test_c7_entry_decision_missing_from_existing_dmn`** |
| C8 | 実装済み（構造的に保証） | `test_c8_is_structurally_guaranteed`（一致を確かめるテスト。5章のとおり） |
| C9 | **条件6 を追加（D19）** | 条件1〜5 の6件 ＋ **`test_c9_condition6_add_column_without_new_input_columns`** / **`test_c9_condition6_added_column_not_used_by_any_row`** |
| C10 | **層1の output_columns に置き換え（D12）** | `test_c10_dot_reference_decision_missing` / `test_c10_dot_reference_output_missing` / **`test_c10_missing_output_columns_in_layer1`** |
| C11 | **新規（D13）** | **`test_c11_dot_reference_value_not_produced`** / **`test_c11_accepts_value_added_by_this_ir`**（この IR が足す値なら通ること） |
| D17 | 新規 | **`test_d17_rejects_dot_reference_to_another_dmn_model`** |

このほか `test_required_facts_cycle_raises_schema_error` / `test_required_facts_unknown_entry_decision` /
`test_fixtures_pass_all_checks` がある（第16回から据え置き）。

---

## 5. ゴールデンパスの実行結果

```
.venv/bin/python3 -m src.stage3_generate.run_golden_path --use-oracle
```

- **C1〜C11**：すべて通過
- **DMN 評価シナリオ**：**4/4 一致**
- **立つゲート**：`new_action_unapproved` と `new_fact_unapproved` の2つだけ
- **承認後**：`action_approved_by` と `facts_approved_by` に名前を入れると
  `gate_names() == []`・`should_review is False` になり、**2つのゲートが下りる**
  （`test_gates_drop_after_approval`）。v1.1 の `new_fact_unapproved` は「新語彙あり」で
  立っていたため承認しても下がらなかった。D14 で解消した
- v1.2 の変更も固定した：`option_taken = new_table_row`（D18）、`knowledge_sources` が
  `["ks_action_request"]` だけ（D22、`ks_privileged_account_registry` を削除）、層1に
  `output_columns = ["decision", "reason_code"]` と
  `entry_points = [{"あおい", "特権操作"}]`（D12・D16）、`new_primitive_required` は立たない（D20）

---

## 6. レベル2・中間判定・add_column のテスト結果

`tests/test_acceptance_paths.py`（11件、すべて通過）。初期登録したあおいの登録簿と
`registry/dmn/aoi_transmission.dmn` を入力にする。IR はテスト用で、`clause_ref` を
`(テスト用) 架空規程 第90x条`、ID を `test_` 始まりにして正解 IR と区別できるようにした。

### 中間判定への行追加（第901条）

本人確認判定に「氏名・生年月日が一致、電話が不一致 → 確認レベル中」の行を position=4 で足し、
その出力を参照する最終判定（テスト口頭伝達可否判定）を新しく作る IR。reference 実装の
第14条と同じ形。`phone_match` の `possible_values` に `不一致` が無いので `value_additions` で足す。

- 挿入：dt_identity が 6行 → **7行**。4行目が追加した行で、元の4行目は5行目に押し出される
- **D16 の作り直し**：ルーティング表が **2行**。1行目が IR 自身（あおい×テスト口頭伝達、
  required_facts = name/birth/phone）、2行目が層1の `entry_points` から作り直した
  **あおい×外部送信**（送信可否判定、required_facts = 7件。情報分類判定の fact も含む）。
  バインディング設定は両者の和集合
- C11：`"高","中"` を参照する行が通る（`中` は層2の既存行とこの IR が足す行の両方から集まる）
- D14：層3の `phone_match` に `不一致` が入り、`history` に
  `{"value": "不一致", "approved_by": "テスト承認者"}` が残る。承認済みなのでゲートは立たない
- **DMN 評価2件**：
  - 追加した行に当たる：電話だけ不一致 → 確認レベル中 → **許可**
  - **既定の行（ワイルドカード）に落ちる**：氏名不一致 → 不一致検出 → **禁止**

### レベル2（第902条）

送信可否判定に「連絡同意が保留なら禁止」の行を position=2（同意なしの行の直後）で足す IR
（`final.action = add_rows`）。`contact_consent` に `保留` を `value_additions` で足す。

- 挿入：dt_transmission が 7行 → **8行**。2行目が追加した行
- C7：挿入後の DMN に起点の判定（decision_transmission）があることを確認
- `level_determination.outcome == "level2_row_addition"`
- ルーティング表は **1行**（既存のルートと同じ あおい×外部送信 なので重複排除される）
- **DMN 評価2件**：
  - 追加した行に当たる：公開情報でも同意保留 → **禁止**。position を守って挿入している
    ことの確認でもある（後ろに入れると感度「低」の行で先に **許可** になってしまう）
  - **既定の行（ワイルドカード）に落ちる**：公開情報・同意 → tr_r7（感度低のみ指定、
    確認レベルと同意は「-」）→ **許可**

### add_column（第903条）

本人確認判定に `address_match`（新しい fact、fuzzy_match_name、承認済み）の列を足し、
「氏名一致・生年月日と電話が未提供でも住所が一致すれば確認レベル中」の行を足す IR。

- **既存の全行が「-」で埋まる**：dt_identity の全7行が4列になり、追加した行以外の
  `address_match` セルは6行すべて `-`。`inputData` も増える
- 層1の `input_columns` が4件になり、`history` に `column_addition` が残る。層2の初期登録分の
  行も `conditions.address_match == "-"` になる
- **required_facts に新しい列が入る**：`[name_match, birth_match, phone_match, address_match]`。
  層1から入力列を引くときに `add_column` の列を足す実装にした（1節 #6）
- 新しく作った最終判定の層1に `output_columns` と `entry_points` が入る
- **DMN 評価2件**：
  - **既定の行（ワイルドカード）に落ちる**：3要素一致・住所不一致 → 既存の1行目の
    `address_match = "-"` に当たる → 確認レベル高 → **許可**
  - 追加した行に当たる：住所だけ一致 → 確認レベル中 → **禁止**

---

## 7. 正解 IR の作成に向けて気づいた懸念

### #1 層3の `sources` を DMN から起こせない（初期登録の穴）

一致プリミティブの引数の取得元（どのマスタのどの項目と比べるか）は DMN XML に無い。
補うと創作になるので**空のまま**にし、層3の provenance に理由を書いた。
`reference/.../tests/test_fact_resolvers.py` には `name_match` と `birth_match` の分だけ
`{"from": "patient_record", "path": "name"}` の形で出てくるが、`from` の語彙が設計書3.3の
`action_request / master / literal` と合わない（`patient_record` は `master` 相当か）。

**確認したいこと**：7件の `sources` をどう埋めるか。設計書に「あおいの fact の取得元」の
表を足すのが素直だと思う。一致プリミティブの実装（U4）に入る前に必要になる。

### #2 `entry_points` を DMN から起こせない（出典を付けて入れた）

D16 の作り直しは `entry_points` が無いと成立しないが、DMN XML には actor も action も無い。
空にすると「送信可否判定の作り直し」自体ができず、第17回タスク5の「参照する最終判定が
すべて出力されること」を確かめられない。そこで次の出典を付けて明示的に付与した
（設計書1.4節「出典付きで明示的に付与した context」）。

```
reference/gijinka-extraction-pipeline/docs/diagrams/sequence_diagram_explained.md:278
> actor_action=("あおい", "口頭伝達") / existing_routes={("あおい","外部送信")}
```

`bootstrap_aoi.ENTRY_POINTS` に出典ごと分離してあるので、差し替えは1か所でできる。
**「あおいの DMN に書かれていることだけ」から外れるので、是非を確認したい。**

### #3 `new_output_values` に出力名が無い

`IntermediateChange.new_output_values` は値のフラットな一覧で、どの出力列の値かが分からない。
中間判定は出力を2つ持つのが普通（本人確認判定は `identity_result` と `identity_level`）なので、
C11 では「その表のすべての出力名に足す」という緩い扱いにした。
`identity_result` に `中` を足したことにもなってしまうので、**`dict[str, list[str]]`
（出力名 → 値）にするのが正しいと思う**。C9 条件3 も同じ問題を持つ。

### #4 `FinalLayer.action` に「最終判定は変えない」の選択肢が無い

中間判定にだけ行を足す条文（第14条型）では、最終判定に何も足さないことがある。
`FinalLayer.action` は `add_rows` / `new_table` の2択なので、テストでは「新しい最終判定を
作る」形（`new_table`）にした。もし「既存の最終判定はそのまま、中間判定だけ直す」条文が
出てきたら表せない（C9 条件1・条件2 のどちらも満たせない）。
`action` に `no_change` を足すかどうかを決めたい。

### #5 `CompoundFactApplication.option_taken` が IR に1つしかない

1つの IR が「新しい表の行」と「既存の表の行」の両方を作ることがある（第901条・第903条の
テストがそう）。選択肢を1つしか持てないので、どちらかを選んで書いた。
正解 IR で前段の出力と比べるとき、ここが揺れる可能性がある。
**表ごと・行ごとに持たせるか、`list` にするか**を決めたい。

### #6 層1の `entry_points` と層2の重複定義がない代わりに、`hit_policy` は層1固定

層1に `hit_policy` があるが、配線ジェネレータは新規決定表を常に `FIRST` で出している
（`NewDecisionTableDraft` に `hit_policy` が無い）。あおいの既存3表も全部 FIRST なので
今は破綻していないが、IR に欄がないことは記録しておく。

### #7 `depends_on_inputs` の値が「inputData の id」か「名前」か

初期登録では `informationRequirement/requiredInput/@href` から id（`#id_name_match`）を引いて
**inputData の名前**（`name_match`）に直して入れた。層1の `input_columns` と揃えたかったため。
設計書4.5には「depends_on_inputs」とだけあるので、名前で合っているかを確認したい。

### #8 送信可否判定の `tr_r3` / `tr_r5` の `"未"`（2節の矛盾2）

正解 IR を作るときに、この表を「正しい既存の表」として参照することになる。
いま直すのか、矛盾を抱えたまま進めるのかを決めたい。C11 を初期登録にも適用すれば
機械的に検出できる（現在は `bootstrap_aoi.find_problems()` が報告するだけで、止めていない）。

---

## 8. push したコミットとテスト件数

- **コミット**：`71d3424`「IR v2 を v1.2 に: 受け入れ経路(レベル2・行追加・列追加)とあおいの初期登録」（push は Kazuhiro さんの手元で実施）
- **テスト件数**：**68件**（すべて通過、Ollama 不要）
  - `tests/test_cross_validation.py` 46件（C1〜C11・D17 の失敗テスト）
  - `tests/test_golden_path.py` 11件（ゴールデンパスの固定・承認後のゲート）
  - `tests/test_acceptance_paths.py` 11件（受け入れ経路。あおいの登録簿を使う）

### 変更したファイル

| ファイル | 変更 |
| --- | --- |
| `docs/design/aoi_doc2dmn_IRv2_design.md` | v1.1 → **v1.2**（D12〜D23・整合条件6・C11・output_columns／entry_points・付録B） |
| `src/stage3_generate/ir_schema.py` | `FactDef.approved_by`・`ValueAddition`・`IntermediateChange.new_input_columns`・`new_table_row`・`new_primitive_required`・`identifiability_classifier`（D14・D18・D19・D20・D23） |
| `src/stage3_generate/registry.py` | 層1の `output_columns`／`entry_points`、`output_values_of()`・`dependents_of()`・`dmn_path()`（D12・D15・D16） |
| `src/stage3_generate/wiring_generator.py` | 受け入れ経路（`patch_existing_dmn`・`derive_routes`）、C11・D17、C4／C6／C7／C9／C10 の拡張 |
| `src/stage3_generate/bootstrap_aoi.py` | **新規**。初期登録（再実行可能）と元の DMN の不足・矛盾の検出 |
| `src/stage3_generate/golden_path.py` | D18・D22・D14（`facts_approved_by`）・D20 |
| `registry/registry.json` | 初期登録の結果（層1=3・層2=18・層3=7） |
| `registry/dmn/aoi_transmission.dmn` | **新規**。あおいの既存 DMN（書き換えずコピー） |
| `tests/test_acceptance_paths.py` | **新規**。受け入れ経路のテスト11件 |
| `tests/conftest.py`／`tests/test_cross_validation.py`／`tests/test_golden_path.py` | v1.2 に追従＋失敗テスト追加 |
| `README.md` | 初期登録の実行方法・テストの構成・受け入れ経路 |

### 範囲外のまま（第17回3節のとおり）

正解 IR の作成（6章）／promotion（U8）／DMN のモデル間 import（U9）／前段の自動化／
一致プリミティブ・オーケストレータ／Kogito 実機（U4）。
