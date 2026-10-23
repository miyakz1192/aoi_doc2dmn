# aoi_doc2dmn 引き継ぎ資料（第24回）

**作成日**: 2026年10月23日
**引き継ぎ元**: Kazuhiroさんのサーバ環境上の Claude Code
**引き継ぎ先**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）

**位置づけ**: 第23回（`HANDOFF_20261023_round23_to_claudecode.md`）で依頼されたパートA
（設計書 v1.5 の実装）とパートB（正解 IR 5件の手直し）の報告。設計書は
`docs/design/aoi_doc2dmn_IRv2_design.md` を v1.5 に差し替えた。

- テスト **169件**すべて成功、**skip 0件**（`reference/` を退避した状態でも169件）
- 正解 IR **5件すべて通過、シナリオ 23/23 一致**、evidence の「根拠なし」**0件**
- 第14条は D49 に従って作り直し、ルートは1本・ゲートは `new_action_unapproved` だけになった

---

## 1. 設計書 v1.5 から外れた点（7件）

| # | 外れた点 | 理由 | 直す場所 |
| --- | --- | --- | --- |
| **1** | **C12 の「受け止める」の判定を、列ごとではなく「足した値を含む入力の組み合わせの網羅」にした** | 設計書5章 C12 は「足した値がどれかの行（「-」を含む）に当たる」と書いているが、**その列だけを見る読み方では第14条の形を止められない。** あおいの送信可否判定の `tr_r1` は確認レベルの列が「-」なので、その列だけ見れば当たってしまう（実際には連絡同意が「不同意」の行なので、同意の行動要求は受け止めない）。HANDOFF が求めた失敗テスト（「第22回の第14条の形で C12 が止まること」）を満たすには、行全体を見る必要がある | `wiring_generator._check_c12_one_consumer()`／設計書5章 C12 の文言 |
| 2 | C12 の対象を「足した出力を**直接**ドット参照している既存の判定」に限った | 設計書は「D16 の洗い出しと同じ逆たどりで集めてよい」とするが、逆たどりは間接的に使う判定まで集める。間接的な判定が見るのは参照先の**出力**で、足した値ではないので、直接のドット参照だけを対象にした | 同上 |
| 3 | IR の `possible_values` に「その他」を書いたときの停止を **C4** に入れた | 設計書は「どの検証に入れるかは判断に任せる」。C4 は fact の定義（primitive と sources）を見る検証なので、`possible_values` の検査もここが自然と判断した | `wiring_generator._check_one_fact_sources()` |
| 4 | シナリオ評価の入口を新しいモジュール `scenario_eval.py` に置いた。`run_golden_path.py` には入れていない | 設計書に置き場所の規定がない。HANDOFF が挙げた2か所（`run_ir_gold` と受け入れ経路のテスト）に入れた。`run_golden_path` は「生成した DMN 単体の挙動」を見せる場所として残し、シナリオ名に「D46 ではバインディング層でエスカレーションになる」と注記した | `scenario_eval.py`／`run_golden_path.SCENARIOS` |
| 5 | 第14条の `dmn_model` を**新しいモデル `verbal_disclosure`** にした | HANDOFF 3.3 は `dmn_model` を指定していない。`aoi_transmission` を指定すると、既存の表に手を入れないこの IR は「単体の DMN」として生成され、**あおいのモデルのファイルを上書きしてしまう**（7節の穴 #1）。namespace も規則どおり作り直され、D39 にも反する | `testdata/ir_gold/aoi_johokanri_art14.json`／`wiring_generator._needs_existing_dmn()` |
| 6 | 正解 IR のファイルに `evidence.fact_mapping` を足した | 第14条の「条文の『連絡先』→ 既存 fact の `phone_match`」という**対応付けの解釈**を記録する場所が、6.2節の表にない。`possible_values` でも `rule_rows` でもないため新しいキーにした。`basis` を持つので「逐語／正規化／根拠なし」の数え方に入る | 設計書6.2「正解 IR のファイル」 |
| 7 | 配線ジェネレータが作る層1のエントリに `history: []` を持たせた | D40 で層1が `history` を持つことになり、D50 の記録先として必要。初期登録（第19回）では持たせていたが、配線ジェネレータ側が未対応だった | `wiring_generator._build_updated_registry()` |

---

## 2. パートA の結果

### 予約語「その他」（D45）

| 項目 | 結果 |
| --- | --- |
| 付く範囲 | `passthrough` の fact だけ。`exact_match`・`fuzzy_match_name`・`date_match`・`ontology_class_match`・`identifiability_classifier` には付かない |
| 付ける場所 | 層3（fact 台帳）とバインディング設定の `possible_values`。`ir_schema.effective_possible_values()` 1か所に集約し、C5 の照合もここを通す |
| あおいの初期登録 | **`contact_consent`（passthrough）が `["不同意", "同意", "その他"]` になった。** `registry/registry.json` の差分はこの1行だけ（他6件は比較型なので変わらない） |
| `value_additions` との順 | 「その他」は**必ず末尾**。第902条（連絡同意に「保留」を足す）では `["不同意", "同意", "保留", "その他"]` |
| 決定表のセル | 書いてよい（C5 が通す）。正解 IR では使っていないが、シナリオの入力値として使っている |
| IR に書いた場合 | **C4 で停止**（`test_c4_rejects_the_reserved_value_in_the_ir`） |

### 知らない値のエスカレーション（D46）

- バインディング設定の各 fact に `"on_unknown_value": "escalate"` を出力する
- シナリオ評価の入口は `src/stage3_generate/scenario_eval.py`。`evaluate_scenario()` が
  バインディング設定の `possible_values`（「その他」を含む）と突き合わせ、無い値があれば
  **DMN を呼ばずに** `{"decision": "escalate", "reason_code": "unknown_fact_value",
  "_unknown_facts": [...]}` を返す
- 入口を通すのは `run_ir_gold` と `tests/test_acceptance_paths.py` の `_evaluate()`
- 対象は「シナリオが渡した fact のうち、バインディング設定にある fact」だけ。渡していない
  fact（その判定が使わない事実）は対象にしない

### C12（D48）

- 実装: `_check_c12_added_output_values_are_accepted()`。生成前の C11 の直後（設計書5章の順）
- 判定: 足した値を固定し、**他の列が取りうる値すべてとの組み合わせ**が、層2の行＋この IR が
  同じ表に足す行のどれかに当たるかを見る。他の列の値の範囲は、fact の列なら層3の
  `possible_values`（「その他」を含む）、ドット参照の列なら参照先が出す値から求める。
  範囲が分からない列があれば「確かめられない」として停止する（C10 が層1の
  `output_columns` が無いときに止めるのと同じ考え方）

失敗テスト（4件）と通過テスト（2件）:

| テスト名 | 確かめること |
| --- | --- |
| **`test_c12_stops_the_round22_article14_shape`** | **第22回の第14条の形**（本人確認判定に「中(生年月日・連絡先一致)」を足し、送信可否判定が参照先）で止まる。あおいの登録簿を使った実物の形 |
| `test_c12_passes_when_the_consumer_gets_a_row_for_the_value` | 同じ IR が送信可否判定にも受け止める行を足せば通る。**外部送信の評価が失敗しなくなる**ことまで確かめる（第22回に実測した失敗の裏返し）。層1の `history` に `output_value_addition` が残ることも固定 |
| `test_c12_stops_when_the_consumer_has_no_matching_row` | 受け止める行が無ければ止まる |
| **`test_c12_looks_at_the_whole_row_not_just_the_column`** | **「その列が『-』」だけでは受け止めたことにならない**（逸脱#1 の根拠。tr_r1 と同じ形を小さく作って確かめる） |
| `test_c12_passes_when_a_wildcard_row_accepts_the_value` | ワイルドカードの行があれば通る |

### C12 に当たって直した既存のテスト（1件）

| 直したもの | 内容 |
| --- | --- |
| `tests/test_cross_validation.py` の `_make_no_change_ir()` | この補助関数が置く「既存の最終判定」（`dt_final`）は層1のエントリだけで、**層2の行が1本も無かった。** 行が無い判定は何も受け止められないので C12 が止まる。確認レベルの列が「-」の行を1本、層2に足した。これを使う4つのテスト（C9条件7・条件8・D35・D37）は、C12 より前の検証で止まるので挙動は変わっていない |

これ以外に C12 に当たったテストはなかった（第901条・第903条・第904条は
`new_output_values` が `{}` なので対象外）。

### 出力値の追加の記録（D50）

層1の `history` に、値ごとに1件を残す。

```json
{ "change": "output_value_addition", "output": "identity_level",
  "value": "中(生年月日・連絡先一致)", "approved_by": "テスト承認者",
  "clause_ref": "(テスト用) 架空規程 第905条", "provenance": { ... } }
```

`provenance` を足したのは、fact の値の追加（D14）の記録と同じ形にそろえるため。

### 第14条の本文の fixture（A6）

`tests/fixtures/article14_clause.json` をコミットした。**条文本文・出典・actor だけ**で、
あおいの既存実装のオラクル（期待する IR の中身）は写していない。
`test_article14_quote_matches_the_committed_fixture` が、正解 IR の `clause_text`・
`clause_ref`・`actor` との一致と、**fixture に `oracle` が無いこと**を確かめる。

### D52 の確認（タスクA5）

| 項目 | 状態 |
| --- | --- |
| ゲートの導出（JSON から読まない） | 実装済み（`clause_ir_from_json`） |
| `review.expected_gates` との突き合わせ | 実装済み（`run_ir_gold._check_envelope`） |
| 登録簿と DMN の持ち回り | 実装済み（`run_all` が `registry/dmn/` を一時ディレクトリに写す） |
| `evidence.not_modeled` | 実装済み。4件の正解 IR が持つ |
| `depends_on_decisions` の ID 解決 | 実装済み（`_resolve_decision_ids`）。層1は決定 ID、`depends_on_inputs` は名前 |

設計書の記述と食い違う点はなかった。

---

## 3. パートB の結果（条文ごと）

`python3 -m src.stage3_generate.run_ir_gold` → **5件通過・シナリオ 23/23 一致**。

| gold_id | 手直しした内容 | シナリオ | 立ったゲート | evidence（逐語／正規化／既定の行／根拠なし） |
| --- | --- | --- | --- | --- |
| `ipa_secguide_v4_6_7_1` | evidence を `basis` 方式に。「システム開発担当」を**正規化（体言化）**として記録。「特権」は「特権/管理者権限」の前半を採った正規化として記録。既定の行は `default_row`。シナリオを「その他」2件＋エスカレーション1件に差し替え | 4（その他2・escalate1） | `new_action_unapproved`, `new_fact_unapproved` | 3／2／1／**0** |
| `mhlw_r7dec_art11_1` | `usage_authorization` の値を**「許可なく」→「許可なし」**（体言化）。セルも合わせて変更。「等」の扱いを `review.notes` に明記 | 5（その他2・escalate1） | 同上 | 5／1／1／**0** |
| `ipa_secguide_v4_personnel_2` | `recipient_authorization` を**「許可されていない」→「許可なし」**（第11条と同じ書き方）。「当社が」「その」を外した正規化も記録。**`action.parent` を `None`** に | 5（その他2・escalate1） | 同上 | 2／3／1／**0** |
| `mhlw_r7dec_art21_3` | **「１８歳未満の者」→「18歳未満の者」**（半角化）。`exemption_request` の反対側を「その他」で表すシナリオを2件（18歳未満は請求を問わず禁止／妊産婦は請求がなければ許可）。`review.notes` に U12 を明記 | 5（その他1・ワイルドカード列1・escalate1） | 同上 | 7／3／1／**0** |
| `aoi_johokanri_art14` | **作り直した（下記4節）** | 4（escalate1。「その他」は該当なし） | **`new_action_unapproved` だけ** | 4／1／0／**0** |

- **「根拠なし」は全件 0 件。** 既定の行は `basis = default_row` として別に数えている
  （`run_ir_gold` が「逐語／正規化／既定の行／根拠なし」の4つに分けて表示する）
- 「その他」のシナリオは、**passthrough の fact を持つ条文にだけ**要求する実装にした。
  第14条は比較型（`date_match`・`exact_match`）の fact しか使わないので「その他」が存在せず、
  シナリオを作れない（`run_ir_gold` がこの条件を見て要求を外す）

---

## 4. 第14条の作り直しの結果（D49）

| 項目 | 結果 |
| --- | --- |
| 本人確認判定（`dt_identity`） | **手を入れない。** `rule_row_additions` も `intermediate` も空 |
| 口頭伝達可否判定 | `birth_match`・`phone_match` を**直接の入力列**にした新しい表。行1「生年月日一致 かつ 連絡先一致 → 許可」、行2「既定の行 → 禁止」 |
| **ルート** | **「あおい × 口頭伝達 → `decision_verbal_disclosure`」の1本だけ。** 外部送信のルートは作り直されない（`required_facts` は `birth_match`・`phone_match` の2件） |
| **ゲート** | **`new_action_unapproved` だけ。** 新しい fact・値・出力値がないので `new_fact_unapproved` は立たない |
| C12 | 対象外（`new_output_values` が空） |
| outcome | `level1_new_table` |
| `option_taken` | `{dt_verbal_disclosure: new_table_row}` |
| `referenced_facts` | `birth_match`・`phone_match`（D25 の基準＝入力列そのまま） |
| シナリオ | 4件（一致→許可／生年月日不一致→禁止／連絡先未提供→禁止／「未照合」→escalate）。既定の行に落ちる2件は、**条文が禁止を述べているので逐語の根拠がある** |

### `compared_table_id` の判断：**`None`（相手なし）**

既存の最終判定は送信可否判定だけだが、その入力列（機微度・確認レベル・連絡同意）と
この表の入力列（生年月日・連絡先の照合）は**1つも重ならない**。
`ColumnSetRelationKind` は `exact_match` / `proper_subset` / `intersecting_non_subset` の
3つで、**互いに素な場合を表す値がない**ため、関係を書けない。
`relation` も `None` にし、理由を `review.notes` に書いた（7節の穴 #2）。

### 「連絡先」と `phone_match` の対応付けの書き方

`evidence.fact_mapping` に、条文の語・逐語の引用・当てた fact・`basis` を書いた。

```json
{ "clause_term": "連絡先", "quote": "連絡先", "fact": "phone_match",
  "basis": "normalized", "normalization": "条文の語を既存 fact に当てはめる(解釈)",
  "note": "**ここは解釈である。** あおいの phone_match は DMN のラベルが
           「電話番号/診察券番号」で、条文の「連絡先」と語が一致しない。条文が
           「電話等による問い合わせ」と述べていることから電話番号を指すと読んだが、
           連絡先には住所やメールも含まれうる" }
```

`review.notes` にも同じ内容を書き、確認をお願いしている。`birth_match` の側は
条文の「生年月日」と語がそのまま一致するので `basis = verbatim` である。

---

## 5. 正解 IR の型の充足（設計書6.1節）

| 型 | 充足 | どの条文で／なぜ埋まらなかったか |
| --- | --- | --- |
| 新しい表を作る単純な条文 | **○** | 4件（特権アカウント・第11条・人的対策-2・第21条第3項） |
| 既存の fact を再利用する条文 | **○（一部）** | 第14条。`birth_match`・`phone_match` を再利用し、層3に新しい fact を足さない。**`value_additions` を使う条文は無かった**（第14条は既存の値「一致」だけを使う） |
| 既存の表に行を足す条文（レベル2・`add_rows`） | **×** | 実在の条文では該当がない（第22回と同じ）。架空の条文では埋めない（6.1節）。テスト用の第902条で確認済み |
| **中間判定を使う条文** | **×（v1.5 で変わった）** | **D49 により第14条が中間判定を使わない形になったため、実在の条文では該当がなくなった。** これでよい（第23回 HANDOFF 3.3）。テスト用の第901条と、C12 のテスト（第905条）で確認済み |
| 値の種類が多い条文 | **○** | 第21条第3項。3つの fact に計6値 |
| 中間判定だけを直す条文（`no_change`） | **×** | 実在の条文では該当がない。テスト用の第904条で確認済み |
| 評価の判断を含む条文（③a の対象外。D51） | — | `mhlw_r7dec_art12`。`manifest.json` の `not_expressible` と `NOT_EXPRESSIBLE.md` に記録。D51 で確定した旨を追記した |

---

## 6. Kazuhiroさんのレビューで特に見てほしい点

| # | 箇所 | 見てほしいこと |
| --- | --- | --- |
| 1 | **第14条の「連絡先」→ `phone_match`** | 4節のとおり、対応付けは解釈である。住所やメールを含めるなら fact を増やす必要がある |
| 2 | **第14条を別の DMN モデル（`verbal_disclosure`）にしたこと** | 逸脱#5。あおいの送信可否判定とは判定を共有しないので分けたが、「あおいの判定は1モデルにまとめる」方針なら、7節の穴 #1 を直してから `aoi_transmission` に入れることになる |
| 3 | `compared_table_id = None` | 4節。列が互いに素な場合を表す値が型にないための判断 |
| 4 | **「その他」の意味が fact ごとに違う** | 第11条では「許可を得ている」、人的対策-2 では「許可されている」、第21条第3項では「請求していない」を指す。レビューで読み解けるかどうか（7節の穴 #3） |
| 5 | 値の正規化 | 「システム開発担当」（体言化）・「許可なし」（体言化）・「18歳未満の者」（半角化）・「休日労働／深夜労働」（共有語の展開）。**「産後１年を経過しない女性労働者」の「１年」は全角のまま**にしてある（語全体が条文の語なので）。表記をそろえるかの判断をお願いしたい |
| 6 | `action.parent` | 人的対策-2 は `None` にした（別の規程の語を混ぜない）。第11条「会社資源利用」・第21条第3項「労働時間管理」・特権アカウント「システム操作」は提案のまま |
| 7 | **第21条第3項の actor（U12）** | 主語が使用者側（「従事させない」）で、あおいに当てる根拠が弱い。`review.notes` に明記した |
| 8 | 列にしなかった語句 | 各ファイルの `evidence.not_modeled`。とくに第14条の「検査結果その他の医療情報」（あおいの情報分類判定を参照すべきか） |

---

## 7. 新しく見つかった型・実装の穴（パートB では直していない）

| # | 穴 | 影響・証拠 | 提案 |
| --- | --- | --- | --- |
| **1** | **既存モデルに新しい表だけを足す IR が、既存の DMN ファイルを上書きする** | `_needs_existing_dmn()` は「既存の表に行・列を足したか」で判定するので、新しい表だけを足す IR では False になり、`render_dmn_xml` が**その表だけの単体 DMN** を作る。`target.dmn_model` が既存モデルだと、評価や promotion でそのファイルを置き換えてしまう。namespace も規則どおり作り直され、**D39 に反する**。第14条でこれを踏みかけたので、別モデルにして回避した | `_needs_existing_dmn()` を「`registry/dmn/<dmn_model>.dmn` が存在するか」でも真にして、既存ファイルに追記する形にする。または「既存モデルに表を足す IR は、D17 と同じく既存ファイルを読む」と設計書4.2 に明記する |
| **2** | **`ColumnSetRelationKind` に「列集合が互いに素」を表す値がない** | 第14条の `compared_table_id` を `None` にするしかなかった。「比べる相手がない」と「比べたが重ならない」を区別できない | `disjoint` を足すか、「重ならない場合は相手なしとする」と3.3節に明記する |
| 3 | **「その他」の意味が fact ごとに違い、台帳から読めない** | 第11条の「その他」＝許可を得ている、第21条第3項の「その他」＝請求していない。語は同じでも指す状態が違う。レビューと統制語彙（U1）で読み解けない | 層3の fact に「その他が指す状態」の注記を持たせる（`other_value_note` など）か、evidence に書くことを 6.2節で求める |
| **4** | **初期登録の `possible_values` が、実際に取りうる値を網羅していない** | あおいの `name_match` は `["一致", "不一致"]`（DMN のセルに出てくる値だけ。D4）。しかし参考実装のシナリオは `未提供` を使っており、**D46 では知らない値としてエスカレーションになる。** C12 の通過テストで実際に踏んだ（`未提供` → `escalate` になり、テストの入力を `不一致` に変えた） | U11（あおいの fact の取得元）と合わせて、**あおいの fact の値の一覧を出典付きで整える**。比較型の fact は「一致／不一致／未提供」が設計書3.5 で決まっているので、初期登録でこの3値を入れる案もある（DMN に出てこない値を足すことになるので、D4 との兼ね合いの判断が必要） |
| 5 | D46 のエスカレーションが、生成物のどこにも「どう回すか」を持たない | バインディング設定に `on_unknown_value = escalate` を出すだけで、誰に回すか・何を伝えるかは未定義。シナリオ評価の入口は `scenario_eval.py` にあるが、実行時（U4）とは別の実装である | U4 でオーケストレータを作るときに、エスカレーションの宛先と伝える内容を決める |

---

## 8. push したコミットとテストの件数

| 項目 | 値 |
| --- | --- |
| コミット | `COMMIT_A`（パートA）／`COMMIT_B`（パートB） |
| テスト | **169件すべて成功、skip 0件**。`reference/` を退避した状態でも169件成功 |
| 内訳 | `test_cross_validation.py` 52／`test_bootstrap_fixes.py` 42／`test_acceptance_paths.py` 31／`test_golden_path.py` 22／`test_ir_gold.py` 22 |
| 正解 IR | **5件通過・シナリオ 23/23 一致**・evidence の根拠なし 0件・表しきれない条文1件（art12） |
| ゴールデンパス | C1〜C12 通過、DMN 評価 4/4 一致、ゲート2つ（承認後0） |
| 初期登録 | 層1=3・層2=18・層3=7。`contact_consent` に「その他」が付いた（差分はこの1行だけ） |

### 変更したファイル

| ファイル | 変更 |
| --- | --- |
| `docs/design/aoi_doc2dmn_IRv2_design.md` | v1.4 → **v1.5**（差し替え） |
| `src/stage3_generate/ir_schema.py` | D45（`OTHER_VALUE`・`effective_possible_values`・`has_other_value`）・D46（`ON_UNKNOWN_VALUE`） |
| `src/stage3_generate/wiring_generator.py` | **C12**（D48）・C4 の「その他」禁止・C5 の照合・バインディング設定（D45・D46）・層1の `history` と `output_value_addition`（D40・D50） |
| `src/stage3_generate/scenario_eval.py` | **新規**。シナリオ評価の入口（D46） |
| `src/stage3_generate/bootstrap_aoi.py` | 初期登録の層3に「その他」（D45） |
| `src/stage3_generate/run_ir_gold.py` | evidence の根拠を4つに分けて数える・シナリオの種類の確認（その他・escalate）・入口経由の評価 |
| `src/stage3_generate/run_golden_path.py` | シナリオ名の注記（U2 の方針が決まったため） |
| `registry/registry.json` | `contact_consent` に「その他」 |
| `testdata/ir_gold/*.json`（5件） | パートB の手直し。第14条は作り直し |
| `testdata/ir_gold/manifest.json`・`NOT_EXPRESSIBLE.md` | 第14条の理由・D51 の確定を追記 |
| `tests/fixtures/article14_clause.json` | **新規**（A6。本文・出典・actor だけ） |
| `tests/test_cross_validation.py` | C4・C12 の失敗テスト5件追加、`_make_no_change_ir` に層2の行 |
| `tests/test_acceptance_paths.py` | 第905条（C12）・D45・D46 のテスト4件追加、`_evaluate` を入口経由に、第904条に `value_additions` |
| `tests/test_golden_path.py` | D45・D46 のテスト追加 |
| `tests/test_ir_gold.py` | fixture の照合・D49 の確認・evidence の内訳の確認を追加 |
| `README.md` | v1.5・「その他」とエスカレーション・C12 |

---

## 9. 範囲外（手を付けていない）

- 正解 IR の `approved` 化（Kazuhiroさんが行う）
- 実行時の仕組み（一致プリミティブ・オーケストレータ。U4）。D46 はバインディング設定への
  宣言とシナリオ評価の入口までとした
- 前段の自動化と、正解 IR との比較（6.3節）。`ClauseIR` を JSON に書き出す向きも未実装
- U11・U12 の解決、promotion（U8）、Kogito 実機
- 7節の穴の修正（報告のみ）

---

## 10. claude.ai チャットへの引き継ぎメッセージ

```
aoi_doc2dmnで、第23回（設計書 v1.5 の実装と正解IRの手直し）が完了しました。
HANDOFF_20261023_round24_to_web.md を読んで、結果を把握してください。
コミット COMMIT_B を push 済みです。正解IRのレビューと承認に進みたいです。
```

---

## 11. このドキュメントの位置づけ

過去の HANDOFF ファイルと同様、これは特定時点のスナップショットである。今後の議論で決定事項が
更新された場合も本ファイルはそのまま残し、更新が必要な場合は新しい日付・ラウンド番号のファイルを
追加する運用とする。
