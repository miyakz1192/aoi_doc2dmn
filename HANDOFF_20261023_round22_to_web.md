# aoi_doc2dmn 引き継ぎ資料（第22回）

**作成日**: 2026年10月23日
**引き継ぎ元**: Kazuhiroさんのサーバ環境上の Claude Code
**引き継ぎ先**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）

**位置づけ**: 第21回（`HANDOFF_20261023_round21_to_claudecode.md`）で依頼されたパートA
（設計書 v1.4 の修正）とパートB（正解 IR の初版）の報告。設計書は
`docs/design/aoi_doc2dmn_IRv2_design.md` を v1.4 に差し替えた。テストは **155件**が通り、
**skip は0件**（`reference/` が無い環境でも全件動く）。

正解 IR は **5件が下書き（`review.status = "draft"`）**で、1件は型で表しきれなかった。

---

## 1. 設計書 v1.4 から外れた点（5件）

設計書の記述と矛盾するものはない。いずれも「設計書に書かれていないことを決めた」ものである。

| # | 外れた点 | 理由 | 直す場所 |
| --- | --- | --- | --- |
| 1 | `depends_on_decisions` の導出を**型ではなく配線ジェネレータ側**に置いた。型には `depends_on_inputs`（名前）と `depends_on_decision_names`（判定の名前）を置いた | 層1の `depends_on_decisions` は決定の **ID**（D16 の逆たどりも初期登録もIDで照合している）だが、`input_columns` のドット参照の接頭辞は FEEL が見る**名前**である。名前→IDの解決には IR と登録簿の両方が必要で、`NewDecisionTableDraft` だけでは閉じない。設計書3.4は「この判定が使う他の判定」と書き、ID か名前かを明記していない | `ir_schema.NewDecisionTableDraft`／`wiring_generator._resolve_decision_ids()`／設計書3.4 |
| 2 | 正解 IR の `review_gates` を JSON から読まず、**導出**した | ゲートは軸1・軸2・Action・fact・レベル判定から機械的に決まる。正解 IR に手で書かせると、他の欄と食い違っても検査されない（D31・D38 と同じ考え方）。期待するゲートは `review.expected_gates` に置き、評価スクリプトが導出結果と突き合わせる | `ir_schema.clause_ir_from_json()`／設計書6.2 |
| 3 | 正解 IR の評価で、登録簿だけでなく **DMN ファイルも持ち回る**ことにした | D43 は登録簿の持ち回りだけを定めている。既存モデルに行を足す正解 IR（第14条）があるため、DMN も持ち回らないと、同じモデルに2件以上の条文が手を入れたときに前の条文の行が消える。`registry/dmn/` を一時ディレクトリに写してから始め、各 IR の出力をそこへ書き戻す。`registry/` は書き換えない | `run_ir_gold.run_all()`／設計書6.2 手順3 |
| 4 | 正解 IR の JSON を読む関数を `ir_schema.clause_ir_from_json()` に置いた。**JSON へ書き出す向きは作っていない** | 設計書に置き場所の規定がない。型を持つモジュールに置くのが自然と判断した。書き出す向きは今回使わないので作っていない（前段の出力を正解 IR と比べる段階（6.3）で必要になる） | `ir_schema.py` |
| 5 | 正解 IR のファイルに `review.expected_gates` と `evidence.not_modeled` を足した | 前者は逸脱#2 のため。後者は「条文に書かれているが決定表の列にしなかった語句」を記録するため。何を表さなかったかが残らないと、レビューで取りこぼしと区別できない | 設計書6.2「正解 IR のファイル」の表 |

---

## 2. パートA の結果

### 実装

| 決定 | 内容 |
| --- | --- |
| D36 | C9 条件3を**双方向の一致**にした。「追加した行の出力値のうち、その表（層2の既存行）がまだ出していない値」の集合と `new_output_values` を出力名ごとに比べ、**足りなくても余っても止める**。比べ方は D28 の `_cell_values()` を通す |
| D37 | `new_output_values` を `dict[str, list[ValueAddition]]` にした。`LevelDetermination.unapproved_output_values()` を足し、`new_fact_unapproved` の条件に加えた |
| D38 | `NewDecisionTableDraft` から `depends_on_decisions`・`depends_on_inputs` を外し、`input_columns` からの導出にした（逸脱#1） |
| D39 | 既存モデルの namespace を変えないことを確認し、テストで固定した（実装は既にそうなっていた。`patch_existing_dmn` が元の `definitions` をそのまま使う） |
| D42 | `tests/fixtures/aoi_transmission_baseline.dmn` をコミットし、初期登録の**既定の入力**にした |

### 追加・変更したテスト

| テスト名 | 確かめること |
| --- | --- |
| **`test_c9_cond3_undeclared_new_output_value`** | **C9条件3の足りない側（今回の主な確認点）**。行が新しい出力値を出しているのに `new_output_values` に書いていない |
| `test_c9_cond3_declares_a_value_the_table_already_produces` | C9条件3の余る側。その表が既に出している値を書いた |
| `test_d37_new_output_value_needs_an_approver` | 承認者のいない出力値でゲートが立ち、承認者を入れると下りる |
| `test_d38_layer1_depends_on_is_derived_from_input_columns` | 層1の `depends_on` が導出値になり、判定名が決定IDに解決される |
| `test_existing_model_keeps_its_namespace` | D39。既存モデルの namespace が変わらない |
| `test_fixture_matches_the_upstream_file_when_available` | D42。コミットしたコピーが元ファイルと一致する（`reference/` がある環境でのみ照合。skip にしない） |

変更: 第901条の `new_output_values` は **`{}`** になった（D36。本人確認判定は「済」も「中」も
id_r2・id_r3 で既に出しているため、足す出力値が無い）。第903条・第904条はご指摘どおり `{}` の
ままである。承認者を入れる必要のある正解・テストは、`seeded_registry`（「高」しか出していない）
を使う C11・D37 のテストだけだった。

### テスト件数と skip

| 環境 | 結果 |
| --- | --- |
| `reference/` がある（Kazuhiroさんのサーバ） | **155件すべて成功、skip 0件** |
| `reference/` を外した（claude.ai 側と同じ条件） | **155件すべて成功、skip 0件**（実際に `reference/` を退避して確認した） |

内訳: `test_cross_validation.py` 48件／`test_golden_path.py` 20件／
`test_acceptance_paths.py` 27件／`test_bootstrap_fixes.py` 42件／`test_ir_gold.py` 18件。

### tests/fixtures/ に入れたファイル

| ファイル | 中身 | 扱い |
| --- | --- | --- |
| `tests/fixtures/aoi_transmission_baseline.dmn` | あおいの元の DMN（`reference/gijinka-extraction-pipeline/dmn/aoi_transmission_baseline.dmn` のバイト単位のコピー） | `bootstrap_aoi.SOURCE_DMN_DEFAULT` をこれに変え、初期登録もそのテストもリポジトリだけで動くようにした。元ファイルとの照合は `reference/` がある環境でだけ行う |

**パートB で `reference/` から引いた資料**（HANDOFF タスクA2 の確認依頼）: あおいの第14条の
**条文本文**を `reference/gijinka-extraction-pipeline/tests/fixtures/article14_clause.json` の
`clause_text` から逐語で引いた。本文は正解 IR のファイル
（`testdata/ir_gold/aoi_johokanri_art14.json`）の中に逐語で持たせ、出典をファイル名で書いて
ある。**`article14_clause.json` 自体はコミットしていない。** コミットすると、条文本文に加えて
あおいの既存実装のオラクル（期待する IR の中身）も repo に入るため、正解 IR を独立に作る建前と
混ざる恐れがあると判断した。レビューで引用を照合したい場合はコミットが必要なので、ご判断を
お願いしたい。

---

## 3. パートB の結果：作れた正解 IR（5件）

すべて `review.status = "draft"`、承認者はすべて `None`、立ったゲートは
**`new_action_unapproved` と `new_fact_unapproved` の2つ**（5件とも同じ）。
`.venv/bin/python3 -m src.stage3_generate.run_ir_gold` で**5件通過・シナリオ 22/22 一致**。

| gold_id | outcome | 新しい fact | 再利用した fact | シナリオ | 非逐語 |
| --- | --- | --- | --- | --- | --- |
| `ipa_secguide_v4_6_7_1`（特権アカウント） | `level1_new_table` | `operator_role`・`target_environment`・`requested_privilege` | なし | 4（うち既定の行3） | 2 |
| `mhlw_r7dec_art11_1`（施設・物品等の私的使用） | `level1_new_table` | `usage_authorization`・`usage_purpose`・`usage_target` | なし | 4（うち既定の行2） | 1 |
| `ipa_secguide_v4_personnel_2`（営業秘密の提供） | `level1_new_table` | `provided_information`・`recipient_authorization` | なし | 4（うち既定の行2） | 1 |
| `mhlw_r7dec_art21_3`（妊産婦等の時間外労働） | `level1_new_table` | `worker_category`・`exemption_request`・`work_type` | なし | 5（うち既定の行2） | 3 |
| `aoi_johokanri_art14`（口頭伝達の制限） | `level1_new_table` | **なし** | **`birth_match`・`phone_match`**（あおいの初期登録分） | 5（うち既定の行3） | 1 |

`option_taken`（導出）:

| gold_id | 導出結果 |
| --- | --- |
| `ipa_secguide_v4_6_7_1` | `{dt_privileged_operation: new_table_row}` |
| `mhlw_r7dec_art11_1` | `{dt_facility_property_use: new_table_row}` |
| `ipa_secguide_v4_personnel_2` | `{dt_trade_secret_provision: new_table_row}` |
| `mhlw_r7dec_art21_3` | `{dt_overtime_assignment: new_table_row}` |
| `aoi_johokanri_art14` | `{dt_verbal_disclosure: new_table_row, dt_identity: existing_table_row_addition}` |

第14条のルート（D16 の作り直し）:

| ルート | required_facts |
| --- | --- |
| あおい × 口頭伝達 → `decision_verbal_disclosure` | `name_match`・`birth_match`・`phone_match`（3件） |
| あおい × 外部送信 → `decision_transmission` | 7件（本人確認判定に行を足したので作り直す） |

**`referenced_facts` が2件なのに `required_facts` が3件**になるのが D25・D41 の実例である。
条文は氏名を問わないので行の条件は「-」で、`referenced_facts` には入らない。一方で本人確認判定
の入力列には `name_match` があるので、実行時には集める必要がある。この差を捕まえるのは C1 である
（D26）。

### manifest の順とその理由

| 順 | gold_id | 理由 |
| --- | --- | --- |
| 1 | `ipa_secguide_v4_6_7_1` | ゴールデンパス。v2 の基本形なので先頭（設計書6.1） |
| 2 | `mhlw_r7dec_art11_1` | 新しい DMN モデルを1枚作るだけで、他の条文に依存しない |
| 3 | `ipa_secguide_v4_personnel_2` | 同じく独立。Action の親（情報開示）が第14条と同じ階層になるので、第14条より前 |
| 4 | `mhlw_r7dec_art21_3` | 同じく独立。値の種類が最も多い |
| 5 | `aoi_johokanri_art14` | 既存の決定表（あおいの本人確認判定）に行を足し、その出力を参照する唯一の条文。初期登録した登録簿と DMN に手を入れるので最後 |

1〜4 は fact も表も共有していないため、適用順を入れ替えても結果は変わらない（依存があるのは
5 だけ）。それでも順を固定したのは、評価が決定的になるようにするためである。

---

## 4. 表しきれなかった条文（1件）

### `mhlw_r7dec_art12`（職場のパワーハラスメントの禁止）

> 職務上の地位や人間関係などの職場内の優越的な関係を背景とした、業務上必要かつ相当な範囲を
> 超えた言動により、他の労働者の就業環境を害するようなことをしてはならない。

**IR のファイルは作っていない。** 理由と型の変更案は
`testdata/ir_gold/NOT_EXPRESSIBLE.md` に書き、`manifest.json` の `not_expressible` に
登録した（テストが両者の整合を確かめる）。要点だけ再掲する。

- 条文の3要件のうち、**「業務上必要かつ相当な範囲を超えた」と「就業環境を害する」は事実の照合
  ではなく評価の判断**である
- `passthrough` で「該当／非該当」を受け取る形にすると、**判定そのものが決定表の外（行動要求の
  値）に移り、表は「3列すべて該当なら禁止」という恒真の表になる**。これは「判定の基準を人が
  読める場所に集める」（1.4節）と、区分の値だけを passthrough にする D3 の両方に反する
- 正直に書くなら `atomic_fact.covered = false` ＋ `new_primitives` だが、**新しいプリミティブは
  ③a の範囲外**（D20）で、その fact を `new_facts` に宣言すると **C4 が「未知の primitive」で
  停止する**。つまり書き切ってもクロス検証を通せない

**提案（3案のうち A を採った）**

| 案 | 内容 | 判断 |
| --- | --- | --- |
| **A（採用）** | ③a の正解 IR から外す。軸1の `alt_labels`（判断ガイド）が示す性質のとおりで、testdata の正解ラベル自体は変えない（②の境界事例として意図的に置かれたもの）。設計書6.1に「評価の判断を含む条文は③a の IR にしない」という選び方の基準を1行足す | 設計変更が最小。推奨 |
| B | `FactDef.primitive` に `human_judgment` を許し、`escalation`（誰がどの基準で判断するか）を持たせ、`human_judgment_required` ゲートで④に回す | ③a の範囲が広がる。判断の基準（事例集）の置き場所の設計が必要で、③b・判断ガイド系統と重なる |
| C | `proportionality_classifier` のような評価用プリミティブを足す | D23（`identifiability_classifier` は初期登録分だけの境界事例）の方針に反し、判断の根拠が実装に隠れる |

---

## 5. 設計書6.1節の型の充足

| 型 | 充足 | どの条文で／なぜ埋まらなかったか |
| --- | --- | --- |
| 新しい表を作る単純な条文 | **○** | 4件（特権アカウント・第11条・人的対策-2・第21条第3項） |
| 既存の fact を再利用する条文 | **○（一部）** | 第14条。`referenced_facts` で `birth_match`・`phone_match` を再利用し、層3に新しい fact を1件も足さない。**ただし `value_additions`（既存 fact への値の追加）を使う条文は無かった**。第14条は既存の値（一致）だけを使うため |
| 既存の表に行を足す条文（レベル2・`add_rows`） | **×** | **該当する条文が無かった。** testdata の28件のうちハード制約×宣言的は5件で、どの2件も同じ actor×action のルートを共有しておらず、既存の最終判定に行を足す形にならない。あおいの第14条も、行為（口頭伝達）が違うので新しい最終判定になる（`final.action = new_table`）。架空の条文は作らなかった。仕組みの確認はテスト用の第902条で済んでいる |
| 中間判定を使う条文 | **○** | 第14条。本人確認判定に行を足し、その出力をドット参照する |
| 値の種類が多い条文 | **○** | 第21条第3項。3つの fact に計6値（`worker_category` 3値・`work_type` 3値） |
| 中間判定だけを直す条文（`no_change`） | **×** | 該当する条文が無かった。第14条は新しい最終判定（口頭伝達可否判定）を作る。テスト用の第904条で確認済み |

---

## 6. evidence で逐語に引けずに迷った値・行（8件）

`run_ir_gold` が件数を数える（`evidence` の各項目に `verbatim` を持たせてある）。

| # | gold_id | 値・行 | 条文の語 | 迷った点 |
| --- | --- | --- | --- | --- |
| 1 | `ipa_secguide_v4_6_7_1` | `operator_role = システム開発担当` | 「システム開発を担当する者」 | **「〜を担当する者」を体言に詰めた形で、逐語ではない。** 逐語にするなら possible_values と決定表のセルを「システム開発を担当する者」にする。v1 からこの値を使っており、実装（`golden_path.py`）とテストも同じ値なので、今回は実装に合わせた。**判断をお願いしたい** |
| 2 | `mhlw_r7dec_art21_3` | `work_type = 休日労働` | 「休日若しくは深夜（午後１０時から午前５時まで）労働」 | 条文は後ろの「労働」を共有している。語の並べ替えだけで外部知識は足していないが、逐語ではない |
| 3 | `mhlw_r7dec_art21_3` | `work_type = 深夜労働` | 同上 | 同上。さらに「午後１０時から午前５時まで」という時間帯の定義を表していない |
| 4 | `aoi_johokanri_art14` | `identity_level = 中(生年月日・連絡先一致)` | 「当該患者の生年月日及び連絡先の一致」 | **値の名前は条文にない。** 既存の「中」を使い回すと、連絡先が未提供の場合（id_r2）まで口頭伝達が許可されてしまうので、新しい値が必要だった。名前は条文の語から付けた。あおいの既存実装の「中(氏名なし・2要素一致)」は「氏名なし」が条文にない語なので使わなかった |
| 5〜8 | 全5件 | 既定の行（`id_r_priv_2`・`id_r_fpu_2`・`id_r_tsp_2`・`id_r_ota_3`） | （なし） | **既定の行（ワイルドカード→許可）に逐語の根拠はない。** 「条文が禁止していない組み合わせは許可」という FIRST の最終行で、禁止規定の条文には必ず現れる。1.4節に反すると見るか、禁止規定の裏として認めるかを決めたい。なお第14条の既定の行（`vd_r2` → 禁止）は、条文が禁止を述べているので逐語の根拠がある |

逐語だが迷った点を2つ挙げる。

- `usage_authorization = 許可なく`（第11条）: 条文の連用修飾をそのまま値にした。区分の値としては
  「許可なし／許可あり」が自然だが、「許可あり」は条文にない
- `worker_category = １８歳未満の者`（第21条第3項）: 条文の**全角数字**をそのまま使った。半角に
  直すと逐語でなくなる。行動要求側の表記と揃うかは要確認

---

## 7. レビューで特に見てほしい点

| # | 箇所 | 見てほしいこと |
| --- | --- | --- |
| 1 | **第14条の新しい確認レベルの値** | 「中(生年月日・連絡先一致)」を新設した判断（6節 #4）。既存の「中」の使い回しが誤りになることは、シナリオ4（連絡先が未提供→禁止）で確かめてある |
| 2 | **実行時の穴（下記 8節の提案）** | 中間判定に新しい出力値を足すと、それを参照する**既存の**最終判定に受け止める行が無く、評価が失敗する。第14条で実際に再現した |
| 3 | `actor` の当てはめ | 5件すべて `あおい` にしたが、根拠の質が違う。第14条は出典資料が明示（`"actor": "あおい"`）。第11条・人的対策-2 は条文の主語が「従業員」。**第21条第3項は主語が使用者側**（「従事させない」）で、あおいを労働の割当を行う立場として扱ってよいかの判断が要る。特権アカウントは条文に行為者の語がない |
| 4 | `action.normalized` と `parent` | 「施設物品使用／会社資源利用」「情報提供／情報開示」「時間外労働等指示／労働時間管理」はいずれも提案である。とくに人的対策-2 の「情報提供」を、あおいの既存階層（情報開示 → 外部送信・口頭伝達）の兄弟に置いてよいか（別の規程の条文である） |
| 5 | 列にしなかった条文の語句 | 各ファイルの `evidence.not_modeled`。人的対策-2 の「組織、人」、第21条第3項の「第２項による」「午後１０時から午前５時まで」、第14条の「検査結果その他の医療情報」「患者又はその代理人」。とくに第14条は、あおいに情報分類判定があるので参照すべきか判断が要る |
| 6 | 「等」の扱い | 第11条の `usage_target = 物品等`。「等」が何を含むかは条文に書かれていないので値を増やしていない。実行時は「等」に当たる値が possible_values 外になり、**既定の行（許可）に落ちる**（U2）。禁止すべきものが許可に落ちうる |
| 7 | 既定の行の扱い | 6節 #5〜8。禁止規定の裏（許可）を逐語の根拠なしに置いてよいか |
| 8 | art12 を外す判断 | 4節の案A。testdata の正解ラベルは変えない前提 |

---

## 8. 正解 IR の作成で見つかった実装・型の穴（パートB では直していない）

HANDOFF 5節に従い、パートB の中では直さず報告する。

### 【最も重い】中間判定に足した出力値を、既存の最終判定が受け止められない

第14条の正解 IR を通したあと、**外部送信のルートが実行時に失敗する**。実測した。

```
外部送信（感度=高・連絡同意=同意）で、本人確認判定が identity_level="中(生年月日・連絡先一致)"
を返す行動要求 → DmnEvalError: no rule matched in decision table dt_transmission
```

- 送信可否判定（`dt_transmission`）は `本人確認判定.identity_level` を参照しているが、
  `tr_r1`〜`tr_r7` に新しい値に当たる行が無く、**ワイルドカードの既定の行も無い**
- **C11 は逆方向しか見ていない**（「参照する値が、参照先が出力する値にあるか」）。
  「参照先が出力する値が、参照する側で受け止められるか」は誰も見ていない
- D16 はルーティング表とバインディング設定を作り直すが、**行は作り直さない**（当然である）
- あおいの既存実装も同じ穴を持っている（`中(氏名なし・2要素一致)` でも同じ）

**提案: クロス検証 C12（仮）を足す。** 中間判定に出力値を足す IR では、その出力をドット参照して
いる既存のすべての決定表について、足した値がどれかの行に当たることを確かめる（当たる行が無く、
かつワイルドカードの既定の行も無ければ停止する）。これは「D16 の洗い出し」で集めた判定に対して
行えるので、すでにある仕組みで実装できる。

止める代わりに「既存の最終判定にも行を足す必要がある」と人に知らせるゲートにする案もある。
どちらにするかは設計の判断をお願いしたい。

### そのほか（軽い順）

| # | 穴 | 影響 |
| --- | --- | --- |
| 1 | `ClauseIR` を JSON に**書き出す**向きが無い | 前段の出力を正解 IR と比べる段階（6.3）で必要になる。読み込み（`clause_ir_from_json`）は今回作った |
| 2 | 層1の `knowledge_sources` に、条文由来でない既存の知識源が混ざる余地がある | 第14条の口頭伝達可否判定は `knowledge_sources` を空にした（入力がドット参照1列だけで、条文に根拠が無い）。一方、既存の本人確認判定には `ks_patient_master` が付いている。「参照先の知識源を引き継ぐか」の規定が無い |
| 3 | 新しい出力値を足したことが、登録簿の `history` に残らない | fact への値の追加（D14）は層3の `history` に `value_addition` として残るが、中間判定の出力値（D37）は層1・層2のどこにも記録されない。承認の対象にした（D37）のに、承認の記録が残らない |
| 4 | `exemption_request` のように「条文に片方の値しかない」fact は、既定の行に落ちて反対の結果になる | 第21条第3項のシナリオ4がこれ（請求していない→許可）。D4 のとおりの挙動だが、条文が「請求した者」だけを挙げている場合に possible_values が1値になり、否定側の表現が決定表から読めない |

---

## 9. push したコミットとテストの件数

| 項目 | 値 |
| --- | --- |
| コミット | `6fcaf03`（パートA）／`aa11654`（パートB） |
| テスト | **155件すべて成功（skip 0件）**。`reference/` を退避した状態でも155件成功 |
| 内訳 | `test_cross_validation.py` 48／`test_golden_path.py` 20／`test_acceptance_paths.py` 27／`test_bootstrap_fixes.py` 42／`test_ir_gold.py` 18 |
| 正解 IR | 5件通過・**シナリオ 22/22 一致**・表しきれなかった条文1件 |
| ゴールデンパス | C1〜C11 通過、DMN 評価 4/4 一致、ゲート2つ（承認後0） |

### 追加・変更したファイル

| ファイル | 変更 |
| --- | --- |
| `docs/design/aoi_doc2dmn_IRv2_design.md` | v1.3 → **v1.4**（差し替え） |
| `src/stage3_generate/ir_schema.py` | D36・D37・D38、`clause_ir_from_json()` を新設 |
| `src/stage3_generate/wiring_generator.py` | C9条件3を双方向に、`_registry_output_values()`・`_resolve_decision_ids()` を新設 |
| `src/stage3_generate/bootstrap_aoi.py` | 初期登録の既定の入力を `tests/fixtures/` に変更（D42） |
| `src/stage3_generate/golden_path.py` | D38（`depends_on_*` を書かない） |
| `src/stage3_generate/run_ir_gold.py` | **新規**。正解 IR を manifest 順に通す評価スクリプト |
| `tests/fixtures/aoi_transmission_baseline.dmn` | **新規**（D42） |
| `testdata/ir_gold/*.json`（5件）・`manifest.json`・`NOT_EXPRESSIBLE.md` | **新規**。正解 IR の下書き |
| `tests/test_ir_gold.py` | **新規**（18件） |
| `tests/test_cross_validation.py`・`test_acceptance_paths.py`・`test_bootstrap_fixes.py`・`conftest.py` | v1.4 の型に追従、失敗テスト追加 |
| `README.md` | v1.4・正解 IR・`tests/fixtures/`・テスト5ファイル構成 |

---

## 10. 範囲外（手を付けていない）

- 正解 IR の `approved` 化（Kazuhiroさんが行う）
- 前段（条文 → IR）の自動化と、正解 IR との比較の仕組み（6.3節）
- U11（あおいの fact の `sources`）、一致プリミティブ（U4）、promotion（U8）、Kogito 実機
- 8節の穴の修正（報告のみ。パートB の中では直していない）

---

## 11. claude.ai チャットへの引き継ぎメッセージ

```
aoi_doc2dmnで、第21回（設計書 v1.4 の修正と正解IRの初版）が完了しました。
HANDOFF_20261023_round22_to_web.md を読んで、結果を把握してください。
コミット aa11654 を push 済みです。正解IRの下書きのレビューを一緒に進めたいです。
```

---

## 12. このドキュメントの位置づけ

過去の HANDOFF ファイルと同様、これは特定時点のスナップショットである。今後の議論で決定事項が
更新された場合も本ファイルはそのまま残し、更新が必要な場合は新しい日付・ラウンド番号のファイルを
追加する運用とする。
