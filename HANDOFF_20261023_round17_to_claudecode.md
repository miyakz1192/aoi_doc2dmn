# aoi_doc2dmn 引き継ぎ資料（第17回）

**作成日**: 2026年10月23日
**引き継ぎ元**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）
**引き継ぎ先**: Kazuhiroさんのサーバ環境上のClaude Code

**位置づけ**: 第16回（`HANDOFF_20261022_round16_to_web.md`）で報告された IR v2 の初回実装（コミット 10fc38b）を、claude.ai チャット側で照合した。39テストが通ること、ゴールデンパスが C1〜C10 を通過しシナリオ 4/4 で一致すること、ゲートが2つ立つことを確認した。報告の7節の論点12件と1節の実装上の判断10件を Kazuhiroさんと検討し、**IR v2 設計書を v1.2 に改訂した**（決定事項 D12〜D23 を追加）。本 HANDOFF では、v1.2 の型の変更と、**レベル2・中間判定の受け入れ経路**の実装を依頼する。

---

## 0. 最初にやること：設計書を差し替える

この HANDOFF と一緒に、次のファイルを渡す。

| ファイル | 置き場所 | 内容 |
|---|---|---|
| `aoi_doc2dmn_IRv2_design.md` | `docs/design/aoi_doc2dmn_IRv2_design.md`（**v1.1 を上書き**） | IR v2 設計書 v1.2（2026-10-23）の Markdown 版。**本作業の仕様書** |

AsIs 仕様書（`docs/design/aoi_doc2dmn_AsIs_spec_9e66776.md`）は変更なし。

**設計の中身は本 HANDOFF には書き写さない。必ず設計書 v1.2 を読んでから作業してほしい。** 特に次の箇所が本作業の仕様になる。

- **付録B**：第16回の論点12件・実装判断10件を v1.2 でどう扱ったか（最初に読むと全体がつかみやすい）
- 2章：D12〜D23（v1.2 で追加した決定事項）
- 3.3〜3.7節：型の変更（approved_by・ValueAddition・new_input_columns・new_table_row・new_primitive_required）と整合条件6
- 4章：登録簿と DMN ファイルの置き場所、4.1節「影響を受ける最終判定の洗い出し（D16）」、4.2節の既存モデルへの挿入、4.5節の層1（output_columns・entry_points）
- 5章：C11 の追加、C6・C7 の拡張、2段階の検証順、C8 の扱い

---

## 1. 背景：第16回の3つの懸念への回答（要約）

第16回で Claude Code が特に気にしていた3点には、次のように答えた。

1. **層1に出力列の一覧がない** → 層1に `output_columns`（**出力列の名前だけ**）を持たせる（D12）。出力の値（「高」「中」など）は層1に書かず、これまでどおり層2の行から集める。行がまだない判定でも出力列が分かるようにするのが目的で、値を2か所に持たないため
2. **new_fact_unapproved を解除する手段がない** → `FactDef.approved_by` を足し、`value_additions` を値ごとに承認者を持つ `ValueAddition` の一覧にする（D14）。ゲートは「承認者のいない fact・値がある」ときだけ立つ
3. **レベル2の受け入れ経路が閉じている** → 正解 IR の作成より先に、今回これを開ける（D15〜D17）。正解 IR 5〜10件の大半はレベル2か中間判定への行追加になる見込みで、経路がないと正解 IR を配線まで通して確かめられないため

その他の論点（識別子が3つある問題、depends_on の単複、ks_privileged_account_registry など）の扱いは付録B.1 を参照。

---

## 2. 依頼する作業

### タスク1：v1.2 の型の変更を実装する（ir_schema.py）

- `FactDef` に `approved_by: str | None = None` を足す（D14）
- `ValueAddition(value, approved_by)` を新設し、`FactUsage.value_additions` を `dict[str, list[ValueAddition]]` にする（D14）
- `IntermediateChange` に `new_input_columns: list[str]` を足す（D19）
- `CompoundFactApplication.option_taken` に `new_table_row` を足す（D18）
- `ReviewGates` に `new_primitive_required`（`atomic_fact.covered = False`）を足す（D20）。`new_fact_unapproved` は D14 の定義（approved_by が None の FactDef または ValueAddition がある）に変える
- 3.7節の整合条件6（add_column なら new_input_columns が空でなく、その列が rule_row_additions の条件に現れる）を C9 に足す

### タスク2：登録簿の層1を拡張し、あおいの送信可否判定を初期登録する

- 層1に `output_columns`（D12）と `entry_points`（その最終判定を呼ぶ actor・action の組の一覧。中間判定は空。D16）を足す
- 層1は decision_table_id・decision_id・decision_name のどれでも引けるようにする（4.5節、付録B.1 #2）
- DMN ファイルの置き場所を `registry/dmn/<dmn_model>.dmn` とし、層1の `dmn_model` から引けるようにする（D15）
- **初期登録（bootstrap）**：サーバの `reference/` 以下にある あおいの送信可否判定の DMN（本人確認判定・情報分類判定・送信可否判定。リポジトリには入っていない）を `registry/dmn/aoi_transmission.dmn` として置き、層1・層2・層3を登録する
  - provenance.origin は `bootstrap` とする
  - `identifiability_classifier` は **この初期登録分でだけ** 使える。新しい条文の new_facts で使ったら C4 で停止する（D23）
  - 層3の possible_values は、DMN の決定表に出てくる値から作る（D4）。DMN にない値を補わないこと
  - 初期登録の手順は、スクリプトか関数として再実行できる形にしてほしい（形は判断に任せる）
  - DMN XML の内容から層1〜3を作るにあたり、元の DMN に不足や矛盾があれば、補わずに報告してほしい

### タスク3：受け入れ経路を実装する（wiring_generator.py）

- **既存モデルへの行の挿入**（D15、4.2節）：rule_row_additions の行を、`registry/dmn/<dmn_model>.dmn` を読み込んだうえで position の位置に挿入して出力する。レベル2（final.action = add_rows）と中間判定への行追加（intermediate の add_row）の両方
- **列の追加**（D19、4.2節）：intermediate の action が add_column のとき、input と inputData を足し、既存の全 rule のその列を「-」にする。層1の input_columns にも足す
- **出力値の追加**：intermediate の new_output_values は、参照先の判定の出力値に加わったものとして扱う（C11 で使う。DMN XML の構造は変わらない）
- **影響を受ける最終判定の作り直し**（D16、4.1節）：IR が変更した既存の表を起点に、層1の depends_on_decisions を逆にたどり、影響を受ける最終判定をすべて洗い出す。それぞれについて required_facts を求め、ルーティング表の行（actor・action は層1の entry_points から）とバインディング設定を作り直して出力する
- **同一モデルの原則**（D17）：中間判定を参照する最終判定は、参照先と同じ DMN モデルに入れる。異なるモデルの判定をドット参照していたら SchemaWiringError で停止してよい
- 登録簿の review には、ゲート名の一覧（gates）を引き続き出力する

### タスク4：クロス検証を v1.2 に合わせ、失敗テストを足す

- **C11**（D13）：ドット参照の列の条件値（「-」以外）が、参照先の判定の出力値（層2の行・IR の rule_row_additions・intermediate の new_output_values）にある
- **C10**：出力名の確認を、層2からの収集から層1の `output_columns` に置き換える（D12）
- **C6**（D21）：referenced_facts と value_additions のキーが層3に実在すること、**IR が使う既存 fact（required_facts のうち new_facts にないもの）がすべて referenced_facts にあること**
- **C7**（D15）：add_rows の場合は、挿入後の既存モデルに entry_decision_id の判定があること
- **C4**（D23）：new_facts での identifiability_classifier を停止する
- **C9**：整合条件6を足す
- 検証の順序は5章の2段階（生成前 C3→C9→C4→C6→C10→C11→C2、生成後 C1→C5→C7→C8）に従う
- **失敗テスト**：追加・拡張した検証（C4 の identifiability_classifier、C6 の網羅、C7 の add_rows、C9 の条件6、C10 の output_columns、C11）について、わざと違反させた IR で SchemaWiringError になるテストをコミットする。C8 は第16回と同様、一致を確かめるテストでよい（5章）

### タスク5：ゴールデンパスを v1.2 に合わせ、レベル2・中間判定のテストを足す

- **ゴールデンパス（特権アカウント条文）の更新**
  - `compound_fact_application.option_taken` を `new_table_row` にする（D18）
  - `knowledge_sources` から `ks_privileged_account_registry` を削除する（D22）
  - 各 FactDef の `approved_by` は `None` のままにする（ゲートが2つ立つことを引き続き確認するため）
  - C1〜C11 の通過、シナリオ 4/4、立つゲート（new_action_unapproved・new_fact_unapproved）を確認する
  - **承認後の確認**：action と各 FactDef の approved_by に名前を入れた版で、2つのゲートが下りることをテストで確かめる
- **レベル2と中間判定のテスト**（初期登録したあおいの登録簿を使う）
  - 中間判定への行追加：本人確認判定（または情報分類判定）に行を足す IR で、既存モデルへの挿入、D16 の作り直し（参照する最終判定がすべて出力されること）、C11 を確かめる
  - レベル2：送信可否判定に行を足す IR（final.action = add_rows）で、挿入と C7 を確かめる
  - add_column：中間判定に列を足す IR で、既存の全行が「-」で埋まり、required_facts に新しい列が入ることを確かめる
  - これらはテスト用の IR でよく、実在の条文である必要はない。ただし、**テスト用であることが分かる名前・clause_ref にし、正解 IR と混同しないこと**
  - 挿入後の DMN を評価するシナリオを、各テストに少なくとも1つ置く。**既定の行（ワイルドカード）に落ちる場合を1つ以上含めること**（6.2節。U2 で既定の行が許可になる問題の見落としを防ぐ）

---

## 3. 範囲外（今回はやらない）

- 正解 IR の作成（設計書6章）。次回以降に扱う
- 登録簿への反映（promotion）。out/ から registry/ への反映は、今回も手で行う（7章 U8）。ただし初期登録（タスク2）は registry/ に直接書いてよい
- DMN のモデル間 import（U9）
- 前段（条文 → IR）の自動化
- 一致プリミティブ・オーケストレータの実装、Kogito 実機での評価（U4）
- U1〜U10 のうち、上に挙げたもの以外の解決

---

## 4. 進め方の注意

- **設計書から外れる必要が出たら、黙って変えずに報告すること。** 型やフィールド名の細部など、実装上の都合で変えた点は理由と一緒に一覧にしてほしい
- **IR と登録簿に入れる情報は、条文本文と、出典付きで明示的に付与した context だけ**（設計書1.4節）。初期登録では、あおいの DMN に書かれていることだけを登録簿に入れる
- 第16回で実装した2段階検証・`{dmn_model, facts}` 形式・namespace 規則などは、v1.2 で採用した（付録B.2）。変えずに使ってよい
- D16 の作り直しで、1つの IR から複数のルーティング表の行とバインディング設定が出るようになる。出力の形（ファイルを分けるか、1ファイルにまとめるか）は判断に任せるので、決めた形を報告してほしい
- 作業が終わったら、**コミットして GitHub に push してほしい**。claude.ai 側は GitHub からコードを取得して照合する

---

## 5. 報告してほしいこと

1. 設計書 v1.2 から外れた点と、その理由（なければ「なし」）
2. 初期登録の結果：元にした DMN ファイルのパス、登録した判定（層1）・行数（層2）・fact（層3）の一覧と、あおいの DMN で見つかった不足や矛盾
3. 受け入れ経路の実装状況（挿入・add_column・出力値の追加・D16 の作り直し・D17 の停止）と、複数ルートを出力するときの形
4. C1〜C11 の実装状況と、追加した失敗テストの一覧（テスト名と、どの検証を確かめるか）
5. ゴールデンパスの実行結果（C1〜C11、シナリオ 4/4、ゲート、承認後にゲートが下りること）
6. レベル2・中間判定・add_column のテストの結果（既定の行に落ちるシナリオを含む）
7. 正解 IR の作成に向けて気づいた懸念（型で表しきれない条文の形、登録簿の不足など）
8. push したコミットのハッシュとテストの件数

---

## 6. claude.aiチャットへの引き継ぎメッセージ例

```
aoi_doc2dmnで、第17回（IR v2 v1.2・受け入れ経路）の実装が完了しました。
HANDOFF_<日付>_round18_to_web.md を読んで、結果を把握してください。
コミット <hash> を push 済みです。あおいの DMN の初期登録で見つかった点と、
正解IRの作成に向けた懸念を相談したいです。
```

---

## 7. このドキュメントの位置づけ

過去のHANDOFFファイルと同様、これは特定時点のスナップショットです。今後の議論で決定事項が更新された場合も、本ファイルはそのまま残し、更新が必要な場合は新しい日付・ラウンド番号のファイルを追加する運用とする。
