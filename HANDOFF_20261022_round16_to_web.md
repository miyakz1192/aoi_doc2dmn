# aoi_doc2dmn 引き継ぎ資料（第16回）

**作成日**: 2026年10月22日
**引き継ぎ元**: Kazuhiroさんのサーバ環境上のClaude Code
**引き継ぎ先**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）

**位置づけ**: 第15回（`HANDOFF_20261023_round15_to_claudecode.md`）で依頼された IR v2 の実装
（タスク1〜5）の完了報告。Claude Code→claude.aiチャットの方向。

**コミット**: `10fc38ba9aa24328bc9847062e17290c9488f765`（14ファイル、+3555/-293行）

---

## 0. 結果の要約

| 受け入れ条件 | 結果 |
|---|---|
| クロス検証 C1〜C10 の実装 | 10項目すべて実装 |
| C1〜C10 の失敗テストをコミット | `tests/` に39件（うち失敗テスト22件）。全件パス |
| ゴールデンパス v2 が C1〜C10 を通過 | 通過 |
| DMN 評価シナリオ | **4/4 一致** |
| 立つゲート | `new_action_unapproved` と `new_fact_unapproved` の2つ（期待どおり） |
| 設計書の2ファイルをリポジトリへ配置 | `docs/design/` に配置済み |

実装中に**v1 のバグを2件**見つけて直した（3節）。設計書自体に判断を要する箇所が**12件**
あり、実装は判断で進めたうえで報告する（7節）。

---

## 1. 設計書から外れた点

HANDOFF 4節「設計書から外れる必要が出たら黙って変えずに報告」に従って列挙する。
いずれも設計書に規定が無いか、規定どおりでは動かない箇所である。

| # | 箇所 | 設計書 | 実装 | 理由 |
|---|---|---|---|---|
| 1 | クロス検証の実行順序 | 5章「4点セットを作った**後**に確かめる」 | **2段構え**にした。生成前（IRと登録簿だけで判定できる）: C3→C9→C4→C6→C10→C2、生成後（成果物を突合）: C1→C5→C7→C8 | C9条件1が破れていると `required_facts` の導出自体が始められず、設計書が要求する失敗テストが `SchemaWiringError` ではなく `KeyError` になってしまう |
| 2 | バインディング設定のトップレベル形 | 4.3 が出力項目に `dmn_model` を挙げる。一方 v1 は裸の配列（AsIs 6.2） | `{"dmn_model": ..., "facts": [...]}` の**エンベロープ**に変更 | 裸の配列では `dmn_model` を置く場所が無い。旧設計（AsIs 6.3）も `dmn_file` を持つエンベロープだった |
| 3 | DMN `definitions` の namespace | 4.2 は id・name の導出元（`target.dmn_model`）のみ規定。namespace の規則が無い | `DMN_NAMESPACE_BASE = "http://gijinka.example.com/dmn"` を定め `<base>/<dmn_model>` とした | v1 のゴールデンパスと**同じ値**になるので、未実施の Kogito 実機確認に変数を増やさない |
| 4 | DMN `definitions` の name | 4.2「id・name は `target.dmn_model` から」 | name = `dmn_model`（= `"privileged_operation"`） | v1 は name に決定名 `"特権操作可否判定"` を入れていたが、これは v2 が解消しようとしているモデル名と決定名の混同そのもの。設計書どおり `dmn_model` にした（**出力の見た目が v1 から変わる**） |
| 5 | 層1の依存フィールド | 4.5 は `depends_on`（単数） | v1 の `depends_on_decisions` と `depends_on_inputs` の**2つを維持** | 3.4 の `NewDecisionTableDraft` は2つ持つ。4.5 の単数表記は略記と判断した |
| 6 | C10 の出力名の取得元 | 「その出力名を持つ」とあるが、4.5 の層1に出力列の一覧が無い | IR の新規決定表なら行の `outputs` のキー、登録簿のみに存在する判定なら**層2の行から集める** | 層1に出力列が無いため他に方法がない。層1に `output_columns` を足す提案は7節#1 |
| 7 | C4 の未知プリミティブ | 規定なし | `SchemaWiringError` で停止 | `AtomicFactLayer.covered=False` が新しいプリミティブを宣言しうるが、引数名の定義が無いと `sources` を検証できない。新プリミティブの追加は③aの範囲外なので止める方を選んだ |
| 8 | `PRIMITIVE_ARGS` の収録範囲 | 3.5 の5種（passthrough・exact_match・fuzzy_match_name・date_match・ontology_class_match） | 3.5 の5種のみ。AsIs 6.4 にある `identifiability_classifier` は**入れない** | 設計書の前書きが「旧文書と食い違う点は本書を優先」としているため。2入力の分類器は複合fact禁止原則とも折り合いが悪い |
| 9 | C5 の対象範囲 | 「決定表の各条件の値」 | `new_decision_tables` の行と `rule_row_additions` の**両方**。ドット参照の列は対象外 | ドット参照は fact ではなく他の判定の出力なので `possible_values` を持たない。その値の検証は設計書に無い（7節#5の対になる欠落） |
| 10 | `SourceRef` のフィールド名 | 3.6 が `from_` | `from_`（JSON では `"from"`） | `from` は Python の予約語。JSON との相互変換は**双方向**の関数で持つ（次回 `testdata/ir_gold/` の JSON を読むため） |

---

## 2. 登録簿の置き場所とファイル形式

| 項目 | 内容 |
|---|---|
| 入力（永続台帳） | `registry/registry.json`（リポジトリにコミット。現在は空） |
| 出力（更新後） | `out/golden_path/privileged_operation_registry.json`（gitignore 対象） |
| 形式 | JSON。`decision_tables`（層1）・`rule_rows`（層2）・`fact_registry`（層3）・`review` |
| 切り替え | `run_golden_path.py --registry <path>` |

**promotion（出力を永続台帳へ反映する操作）は実装していない。** 人間レビューを経た別操作と
位置づけた。今回のゴールデンパスは空の台帳から始まり、実行後も `registry/registry.json` は
空のまま（出力は `out/` にだけ書かれる）。

返す登録簿は**入力にマージした更新後の全体**にした（設計書4章「registry（更新後）」）。
v1 はこの条文から導いた分だけを返していたが、それでは C7（`entry_decision_id` が層1に実在
するか）が空の台帳では原理的に満たせない。

---

## 3. 実装中に見つけた v1 のバグ（2件とも修正済み）

### 3.1 ドット参照の `href` が decision 名を ID として使っていた

v1 の `render_dmn_xml()` は「判定名.出力名」の接頭辞をそのまま `href="#<接頭辞>"` にしていた。
しかし FEEL 式が参照する接頭辞は decision の **name**（`dmn_engine.py` は `ctx[decision.name]`
でネストする）で、`href` に必要なのは decision の **id** である。IR と登録簿から name→id を
解決するよう直した。`inputExpression/text` と input の id はドット付きの名前のまま残している
（`dmn_engine.py` が名前で引くため）。

ゴールデンパスはドット参照を使わないので v1 では実害が出ていなかったが、C10 とレベル2で必要になる。

### 3.2 ドット参照が循環すると `required_facts` が静かに空になっていた

最初の実装（幅優先＋展開済み判定の重複排除）では、A→B→A の循環があったときに
「Bを展開 → Aに戻る → Aは展開済みなので飛ばす」となり、**エラーにならず `required_facts` が
空のまま返る**という挙動になっていた。空になるとバインディング設定もルーティング表も空に
なり、C8（両者の一致）も通ってしまう。

展開経路（path）を追跡する深さ優先に直し、経路上に同じ判定が再び現れたら
`SchemaWiringError` にした。あわせて「参照先の判定に入力列が無い」場合もエラーにした
（これも `required_facts` が静かに縮む経路だった）。`tests/test_cross_validation.py::
test_required_facts_cycle_raises_schema_error` で固定している。

---

## 4. C1〜C10 の実装状況と失敗テスト一覧

全39件パス（`.venv/bin/python3 -m pytest tests/ -v`）。Ollama 不要。

| # | 検証 | 失敗テスト |
|---|---|---|
| C1 | fact の不足 | `test_c1_missing_fact_definition` |
| C2 | fact の余剰 | `test_c2_unused_new_fact` ／ 誤検知しないことの確認 `test_c2_allows_fact_used_only_by_row_addition` |
| C3 | 複合 fact の拒否 | `test_c3_rejected_compound_in_primitive` |
| C4 | sources と primitive の引数名 | `test_c4_sources_key_mismatch`・`test_c4_unknown_primitive` |
| C5 | 決定表の値と possible_values | `test_c5_value_not_in_possible_values`・`test_c5_unsupported_feel_cell`・`test_c5_accepts_feel_literal_list`・`test_c5_value_additions_are_allowed` |
| C6 | 参照 fact の実在 | `test_c6_referenced_fact_not_in_registry`・`test_c6_value_addition_key_not_in_registry` |
| C7 | 判定先の実在 | `test_c7_entry_decision_not_generated` |
| C8 | 集める事実の一致 | **失敗テストを作れない**（下記） |
| C9 | レベル判定の整合（3.7の5条件） | 条件1 `test_c9_cond1_new_table_without_entry_decision`、条件2 `test_c9_cond2_add_rows_without_compared_table`・`test_c9_cond2_add_rows_without_row_additions`、条件3 `test_c9_cond3_add_row_without_matching_output_value`、条件4 `test_c9_cond4_intermediate_table_not_in_registry`・`test_c9_cond4_compared_table_not_in_registry`、条件5 `test_c9_cond5_uncovered_without_new_primitives` |
| C10 | ドット参照の解決 | `test_c10_dot_reference_decision_missing`・`test_c10_dot_reference_output_missing` |

**C8 だけは違反させる失敗テストが作れない。** ルーティング表の `required_facts` と
バインディング設定の fact 集合は同じ `required_facts` から導出しているため、IR の側から
不一致を作る手段が無い（構造的に保証される）。代わりに両者が実際に一致することを固定し、
将来どちらかの導出だけを変えた場合に気付けるようにした（`test_c8_is_structurally_guaranteed`
にその旨をコメントで書いてある）。

### テストのフィクスチャを2種類用意した理由

ゴールデンパスの IR は登録簿が空・ドット参照なし・`value_additions` なし・
`final.action=new_table` のため、**C6・C9条件3・C9条件4・C10 を破る材料が無い**。そこで
`tests/conftest.py` に中間判定（本人確認判定）を1枚持つ登録簿（`seeded_registry`）と、その
出力をドット参照する最終判定の IR（`intermediate_ir`）を用意した。あおいの送信可否判定を
**永続登録簿へ初期登録することは第15回の範囲外**なので、これはテスト内だけで組み立てており
`registry/registry.json` には書き込んでいない。

---

## 5. ゴールデンパス v2 の実行結果

```
$ .venv/bin/python3 -m src.stage3_generate.run_golden_path --use-oracle

[② 2軸分類]
  軸1: pred=ハード制約 gold=ハード制約 ... OK
  軸2: pred=宣言的 gold=宣言的 ... OK

[③a IR]
  level_outcome=level1_new_table
  human_review_status=pending should_review=True
    - gate: 新規Actionが未承認
    - gate: 新規fact・既存factへの新規値が未承認

[登録簿] registry/registry.json
  層1=0件 層2=0件 層3=0件

[4点セット生成] クロス検証 C1〜C10 通過
  required_facts: ['operator_role', 'target_environment', 'requested_privilege']

[DMN評価(自作エンジン)]  シナリオ: 4/4 一致
```

②分類器を実際に呼ぶ版（Ollama 使用）は、前回（第14回）と同じく軸1・軸2とも正解する。

---

## 6. 4点セットのうち v1 から変わった部分

### バインディング設定（最も変わった）

```json
// v1: 裸の配列。IRの外(BINDINGS定数)から渡していた
[ { "variable": "operator_role", "primitive": "exact_match",
    "possible_values": ["システム開発担当", "運用担当", "その他"],
    "sources": { "subject": "action_request.operator.role",
                 "registry": "privileged_account_registry.role" } }, ... ]

// v2: エンベロープ。IRのfacts.new_factsから生成
{ "dmn_model": "privileged_operation",
  "facts": [ { "variable": "operator_role", "primitive": "passthrough",
               "possible_values": ["システム開発担当"],
               "sources": { "value": { "from": "action_request",
                                       "path": "operator.role" } } }, ... ] }
```

変わった点は4つ。(a) エンベロープになり `dmn_model` を持つ、(b) `primitive` が実態どおり
`passthrough` になった（D3）、(c) `possible_values` が条文の値だけになった（D4。
「運用担当」「その他」「検証環境」「開発環境」「一般」が消えた）、(d) `sources` が
`{from, path}` 形式になり、引数名も passthrough の `value` 1つになった（D5）。

### ルーティング表

```json
// v1: 呼ぶ判定が分からない
{ "routes": [ { "actor_action": [{"actor": "あおい", "action": "特権操作"}],
                "input_columns": ["operator_role", ...] } ] }

// v2: dmn_model と decision_id で判定を一意に特定できる(D8)
{ "routes": [ { "actor": "あおい", "action": "特権操作",
                "dmn_model": "privileged_operation",
                "decision_id": "decision_privileged_operation",
                "required_facts": ["operator_role", "target_environment",
                                   "requested_privilege"] } ] }
```

`actor_action` の入れ子と `input_columns` は廃止した。`required_facts` は依存関係を
たどって求めた値である（ゴールデンパスは中間判定が無いので入力列と同じになる）。

### 登録簿

層1に `dmn_model` が入り、**層3（fact 台帳）が新設**された。層3の各エントリは `FactDef` の
フィールドに加えて `defined_by`（最初に定義した条文）・`used_by`（使っている決定表）・
`provenance`・`history` を持つ。`review` には `gates`（ゲート名の一覧、集計用）が
`gate_reasons`（日本語の説明文）に加えて入る。

### DMN XML

`definitions` の `name` が `"特権操作可否判定"` から `"privileged_operation"` に変わった
（1節#4）。`namespace` と決定表の中身は v1 と同じ。

---

## 7. 設計書で判断が必要だった論点（12件）

実装は判断で進めた。設計書側の確認・改訂が必要なものを挙げる。#1〜#4 は次回
（正解 IR の作成、レベル2・中間判定）に直接効く。

| # | 論点 | 今回の扱い |
|---|---|---|
| 1 | 4.5 の層1に**出力列の一覧が無い**ため、C10 の「その出力名を持つ」を登録簿のみに存在する判定について検証できない | 層2の行から集めた。層1に `output_columns` を足すことを提案したい |
| 2 | 識別子が**3空間**ある（decision id／decision table id／decision name）が対応規則が未定義。3.7条件2 は `compared_table_id`（表ID）から `entry_decision_id`（決定ID）への解決を暗黙に要求する | 層1を3つのキーで引けるようにした |
| 3 | `new_fact_unapproved` に**ゲートを解除する手段が無い**。`ActionRef.approved_by` に相当するフィールドが `FactDef` に無いため、新しい fact がある限りゲートは立ち続ける | ゲートは立てたまま。`FactDef` に承認フィールドを足すか、ゲートの意味を「新語彙あり」と割り切るかの判断をお願いしたい |
| 4 | D4 と U2 の相互作用。`possible_values` を条文の値だけにすると、**実運用の値はすべてワイルドカード行の「許可」に落ちる**。4点セットが宣言として不完全になった | ゴールデンパスのシナリオ名に「possible_values外の値が既定行に落ちる・U2」と明記して可視化した。6.2 が正解 IR ごとに DMN シナリオを要求しているが、シナリオ入力を `possible_values` 内に限るかは次回の判断事項 |
| 5 | C5 の**対になる検証が無い**。ドット参照の列の条件値（例 `本人確認判定.identity_level` に `"中"`）が、その判定の出力値や `new_output_values` に含まれるかを確かめる項目が設計書に無い | C5 はドット参照を対象外にした。C11 として追加するかの判断をお願いしたい |
| 6 | `intermediate.action = "add_column"` に**追加する列を指すフィールドが無い**。追加列は `rule_row_additions` の条件キーにしか現れず、層1の `input_columns` には無いので 4.1 の置換から漏れる。3.7 にも `add_column` を制約する条件が無い | 実装では特別扱いしていない（`add_column` でも C9条件3・条件4 だけがかかる） |
| 7 | `AtomicFactLayer.covered=False` に対応する **ReviewGates が無い**（3.7条件5 は人間レビュー送りと言っているが、立てるゲートが定義されていない） | ゲートは追加していない。C9条件5（`new_primitives` が空でないこと）だけを確認 |
| 8 | `facts.referenced_facts` と `required_facts` の**関係が未定義**。層3から引ける fact は列挙しなくても C1 を通り、列挙しても使われていなくても C2 は何も言わない（C2 は `new_facts` だけを見る）。規範なのか記録なのかが不明 | C6（実在確認）の対象としてのみ扱っている |
| 9 | バインディング設定のトップレベル形（1節#2） | エンベロープを採用 |
| 10 | `CompoundFactApplication.option_taken` に「**新規**表の行として表す」の選択肢が無い。ゴールデンパスは新規表を作りながら `existing_table_row_addition` と自称している（v1 からの誤ラベル） | ラベルは維持し、`rationale` に事情を書いた。選択肢の追加を提案したい |
| 11 | 4.5 の `depends_on`（単数）と 3.4 の2フィールドの不一致（1節#5） | 2フィールドを維持 |
| 12 | 3.8 が来歴を v1 据え置きとしたため `ks_privileged_account_registry` が残っているが、これを参照していた `exact_match` の `registered` 引数は D3 で消えた。1.4「IR に入れてよい情報は条文と出典付き context だけ」に照らして残置の是非を確認したい | 残置した |

---

## 8. 次回（レベル2・中間判定・正解IR）に向けた懸念

1. **レベル2の受け入れ経路が今回は閉じている。** 既存 DMN モデルへの行挿入が範囲外なので、
   `final.action = add_rows` の IR は C7 の DMN XML 側の確認を通せない（実装では
   `new_table` のときだけ DMN XML を見るようにして回避した）。レベル2 の正解 IR を作る前に、
   既存 DMN への `rule` 挿入（4.2 最終段落）を実装する必要がある
2. **`required_facts` は `rule_row_additions` の fact を含まない。** エントリ判定から
   たどるので、到達しない中間表への行追加で使う fact はバインディング設定にも
   ルーティング表にも載らない。実行時にその行を評価するには誰かがその fact を集める必要が
   あるはずで、設計の意図（中間表の行追加は別の最終判定から呼ばれる前提か）を確認したい
3. **前段（条文 → IR）は依然として手組み。** IR の形は固まったが、`golden_path.py` が条文を
   読んで決めた内容を組み立てている状態は変わっていない。第14回で挙げた「③の本丸」はここ
4. **`possible_values` への業務知識の混入は構造的には防げていない。** D4 で今回は消えたが、
   前段を自動化したときに LLM が条文外の値を提案するのを止める仕組みは無い。
   `new_fact_unapproved` ゲート（人間が見る）だけが歯止めである

---

## 9. 報告事項の対応表（第15回 5節）

| # | 依頼 | 本書の該当箇所 |
|---|---|---|
| 1 | 設計書から外れた点と理由 | 1節（10件）＋7節（12件の論点） |
| 2 | 登録簿の置き場所とファイル形式 | 2節 |
| 3 | C1〜C10 の実装状況と失敗テストの一覧 | 4節 |
| 4 | ゴールデンパス v2 の実行結果 | 5節 |
| 5 | 4点セットのうち v1 から変わった部分 | 6節 |
| 6 | レベル2・中間判定を扱う次回に向けた懸念 | 8節 |
| 7 | push したコミットのハッシュ | `10fc38ba9aa24328bc9847062e17290c9488f765` |

---

## 10. claude.aiチャットへの引き継ぎメッセージ例

```
aoi_doc2dmnで、第15回の IR v2 実装が完了しました。
HANDOFF_20261022_round16_to_web.md を読んで、結果を把握してください。
コミット 10fc38b です。

特に7節の12件（層1に出力列が無くC10が検証できない、new_fact_unapprovedを解除する
手段が無い、D4とU2の相互作用など）と、8節の懸念1・2（レベル2の受け入れ経路が
閉じている、required_factsがrule_row_additionsのfactを含まない）を相談したいです。
```

---

## 11. このドキュメントの位置づけ

過去のHANDOFFファイルと同様、これは特定時点のスナップショットです。今後の議論で決定事項が
更新された場合も、本ファイルはそのまま残し、更新が必要な場合は新しい日付・ラウンド番号の
ファイルを追加する運用とする。
