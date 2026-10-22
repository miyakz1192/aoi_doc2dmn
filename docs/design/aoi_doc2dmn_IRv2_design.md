# aoi_doc2dmn ③a 中間表現（IR）v2 設計書（Markdown 版）

> **この文書について**
> - Word 版「aoi_doc2dmn_IRv2_設計書.docx」v1.1（2026-10-23）の Markdown 版。正本は Word 版で、図は文章と表で置き換えている。
> - **③a の IR 設計の基準文書である。** 次の旧文書と食い違う点は、本書を優先する。
>   - gijinka_dmn_generation_design_v1.docx：バインディング設定（fact の定義は IR に入れ、共有分は登録簿の層3に置く。区分の値は passthrough）、ルーティング表（dmn_model・decision_id・required_facts を持つ）、登録簿（層1に dmn_model、層3 fact 台帳を新設）
>   - gijinka_extraction_pipeline_design_v1.docx：レベル1／2判定（3層とも同じ「列集合の関係」で表すのをやめ、層ごとの型にする。関係＝根拠と action＝結論を分ける）
>   - 4点セットの同時生成、複合 fact 禁止、FIRST、Action 正規化などの原則は旧文書から引き継ぐ（1.4節）。
> - 実装の現状（コミット 9e66776 時点）は claude/aoi_doc2dmn_AsIs_spec_9e66776.md を参照。

| 項目 | 内容 |
| --- | --- |
| 版 | v1.1（ToBe 設計・レビュー待ち） |
| 作成日／改訂日 | 2026-10-16／2026-10-23 |
| 作成 | Claude（Kazuhiro さんとの議論に基づく） |
| 対象 | aoi_doc2dmn ③a（宣言的 × ハード制約） |
| 基準とした実装 | GitHub miyakz1192/aoi_doc2dmn コミット 9e66776 |

## はじめに

本書は、③a の中間表現（IR）をどう直すかを決める設計書（ToBe）である。前段（条文 → IR）の自動化に入る前に、前段が出力する IR の形を確定させることを目的とする。

### 本書の位置づけ

③a の現状は、別文書「aoi_doc2dmn ③a IR→4点セット配線 仕様書」（以下、AsIs 仕様書）に記録している。AsIs 仕様書は「コミット 9e66776 の実装が今どうなっているか」、本書は「これからどうするか」を書く。2つを分けたのは、実装済みの内容と今後の設計が混ざらないようにするためと、変更の理由（現状の問題）を記録として残すためである。

| 文書 | 内容 | 時点 |
| --- | --- | --- |
| AsIs 仕様書（Claude Docs）<br>https://claude.ai/code/artifact/11cc2737-119c-4776-8f64-d7085b0afbce | IR・配線ジェネレータ・4点セットの実装の記録と、そこで見つかった問題 | コミット 9e66776 |
| 本書（IR v2 設計書） | IR の改訂内容、4点セットの導き方、クロス検証、正解 IR の作り方 | これから実装する |

### 対象範囲

- 対象：③a（軸1＝ハード制約 × 軸2＝宣言的）の条文を DMN 決定表にする工程
- 対象外：③b（手続き的 × ハード制約／ObligationManager）、判断ガイド・エスカレーション基準の条文、実行時の仕組み（一致プリミティブ・オーケストレータ）の実装

### 読み方

このプロジェクトに初めて参加する人は、8章の用語集を先に読むとよい。1章で前提をそろえ、2章で決定事項を一覧し、3〜5章で IR v2 の中身を説明する。6章は次の作業（正解 IR の作成）、7章は残っている論点である。

## 1. 前提

### 1.1 全体の流れと IR v2 の位置づけ

**図1　③a の全体の流れと IR v2 の位置づけ**（図は Word 版を参照。以下は図の内容）

条文テキスト → ②2軸分類（実装済み）→ 前段：条文 → IR（これから作る）→ IR v2 → 配線ジェネレータ（v2で改修）→ 4点セット（DMN XML／バインディング設定／ルーティング表／Scope・Decision 登録簿 層1・2・3）。

前段は、人が手で作った正解 IR（5〜10件）で評価する。前段は登録簿にある既存の決定表と fact 台帳を参照し、レベル判定と fact の再利用を判断する。

IR v2 は、前段（条文 → IR）の出力であり、配線ジェネレータの唯一の入力である。前段の自動化を評価するために、人が手で作った正解 IR を用意する（6章）。前段は、既存の決定表と fact 台帳を参照しながら、レベル判定と fact の再利用を判断する。

### 1.2 ②2軸分類と③a の範囲

抽出パイプラインの②では、各条文を2つの軸で分類する。軸1は「機械的に白黒を付けられるか」、軸2は「時間の経過を伴うか」という、別々の性質を問う。LLM には6択の1問ではなく、軸ごとに独立した2問として判定させている。

| 軸 | ラベル | 意味 |
| --- | --- | --- |
| 軸1（層） | ハード制約 | 具体的な禁止・許可・義務が明記され、白黒を判定できる |
|   | 判断ガイド | 抽象的な規範や努力義務で、状況に応じた判断を要する |
|   | エスカレーション基準 | 確信度や影響範囲などの閾値で、人間の確認を求める |
| 軸2（時間構造） | 宣言的 | その時点の状態だけで結論が出る |
|   | 手続き的 | トリガー → 期限 → 分岐のように時間の経過を伴う |

**図2　②2軸分類の組み合わせと、それぞれの行き先**（図は Word 版を参照。以下は図の内容）

| 軸1 ＼ 軸2 | 宣言的（その場で結論） | 手続き的（時間経過を伴う） |
| --- | --- | --- |
| ハード制約 | **③a：DMN 決定表（本書の対象）** | ③b：BPMN／ObligationManager（範囲外） |
| 判断ガイド | 前例参照で即時に助言（未設計） | 発生しない前提 → 誤分類として④人間レビューへ |
| エスカレーション基準 | 即時に人間の確認を求める（未設計） | ObligationManager の設定として扱う（②の判定対象外） |

本書の対象は、図2で強調した「ハード制約 × 宣言的」のマスだけである。②でこのマスに分類された条文が③a に進み、DMN 決定表になる。

### 1.3 中間判定と最終判定

決定表どうしは依存関係を持てる。依存関係を描いた図を DRD と呼ぶ。DRD の中で、他の判定に材料（評価）を渡す判定を**中間判定**、AI エージェントの行動を許可するか禁止するかを決める判定を**最終判定**と呼ぶ。決定表としての書き方は同じで、違いは DRD の中での位置だけである。

**図3　DRD の例：中間判定と最終判定（あおいの情報開示）**（図は Word 版を参照。以下は図の内容）

- 事実 name_match・birth_match・phone_match → **本人確認判定（中間判定）** → 確認レベル
- 事実 has_medical・has_pii・identifiable → **情報分類判定（中間判定）** → 機微度
- 確認レベル・機微度・事実 contact_consent → **送信可否判定（最終判定）** → 許可／禁止／条件付き許可。ルーティング表から呼ばれる入口
- 本人確認判定は、第14条で新設する **口頭伝達可否判定（最終判定）** でも再利用する

|  | 中間判定 | 最終判定 |
| --- | --- | --- |
| 役割 | 他の判定に渡す材料を作る | 行動の可否を決める |
| 出力の例 | 確認レベル、機微度 | 許可／禁止／条件付き許可 |
| 出力の行き先 | 別の判定の入力（「本人確認判定.identity_level」のように参照される） | オーケストレータ（実行するか止めるか） |
| ルーティング表との関係 | 直接は呼ばれない | ルーティング表から呼ばれる入口（エントリポイント） |

分ける理由は3つある。本人確認判定のように複数の最終判定で使い回せること、1枚の表に全ての事実を詰め込んで行数が爆発するのを防げること、途中の評価が記録に残り判断の理由を追えることである。

### 1.4 引き継ぐ設計原則

次の原則は、9e66776 以前の設計から変えずに引き継ぐ。

| 原則 | 内容 |
| --- | --- |
| 4点セットの同時生成 | DMN XML・バインディング設定・ルーティング表・登録簿を、同一の IR から1回の呼び出しで生成する。別々に書くと「宣言と実体のズレ」が起きる（Kogito 実機検証での教訓） |
| 複合 fact 禁止 | 「A かつ B」のような複合判定を一致プリミティブに持たせず、決定表の行として表す。判定を人が目で確かめられる場所に集める |
| FIRST と行の順序 | ヒットポリシーは FIRST。危険な条件（禁止）を上の行に置く。行の挿入位置は人が決める（priority_tier は見送り） |
| 来歴の表し方 | 計算に使わない情報源は knowledgeSource とし、authorityRequirement で decision に紐付ける（inputData には付けられない） |
| Action 正規化 | LLM が候補を出し、新しい語は人が階層への位置づけを承認する。実行時は文字列一致だけで引く |
| IR に入れてよい情報 | 条文本文と、出典付きで明示的に付与した context だけ。レビューアの頭の中の知識を経路なしで混ぜない |
| 人間レビューのゲート | 複数の条件を1つのスコアに混ぜず、独立したゲートの OR として持つ |

## 2. 決定事項と理由

2026年10月14〜16日の議論で決めた事項を一覧にする。いずれも AsIs 仕様書の9章（要確認事項）で見つかった問題への対応である。

| # | 決定 | 理由 | 9e66776 との違い |
| --- | --- | --- | --- |
| D1 | fact の定義を IR に入れる。IR には「この条文で新しく定義する fact」と「既存 fact への参照」を持たせる | 前段の出力を IR 1つにまとめるため。名前だけでなく中身（値・取得元）まで IR の中で整合を確かめるため | バインディング設定は IR の外（golden_path.py の BINDINGS 定数）から渡していた |
| D2 | fact の本体は共有の台帳に置き、登録簿の層3（fact 台帳）とする | 役割・環境などの fact は複数の条文で使う。4点セットの外にファイルを増やさない | 共有の置き場所がなかった |
| D3 | 区分の値（役割・環境・権限など）は passthrough、2つの値の比較（本人確認など）は exact_match 等の比較型を使う | 判定の基準（どの役割が禁止か）を決定表に残し、人が読めるようにするため | 全 fact が exact_match と宣言されていたが、実態は passthrough だった |
| D4 | possible_values には、決定表が参照する値（条文に出てくる値）だけを書く。組織の役割一覧のようなマスタは、必要になった時点で knowledgeSource として出典付きで接続する | レビューアが業務知識で値を補うと、条文外の知識が経路なしで混ざる | 「運用担当」「検証環境」など条文にない値が入っていた |
| D5 | sources を {from, path} または {from: literal, value} の形に戻す | 取得元の種類（行動要求・マスタ・固定値）を機械的に区別するため | 「action_request.operator.role」のような文字列だった |
| D6 | レベル判定を層ごとの型にし、結論（outcome）は最終判定の層の action から導く | 層ごとに問いと答えの形が違う。結論と根拠のつじつまを型で保証するため | 3層すべてに同じ「列集合の関係」を当てはめ、outcome を別に書いていた |
| D7 | IR に判定先（入れる DMN モデルと、最終判定の ID）を持たせる | ルーティング表と登録簿に、呼ぶべき判定とモデルを書くため | DMN モデル名は IR の外から引数で渡していた |
| D8 | ルーティング表の1行に dmn_model・decision_id・required_facts を持たせる。登録簿の層1に dmn_model を戻す | 行動要求から呼ぶ判定とモデルを一意に特定し、中間判定の先まで含めて集める事実を決めるため | ルーティング表は actor×action と入力列だけだった |
| D9 | クロス検証を7項目追加する（5章） | 名前の一致だけでは、値・取得元・判定先のズレを検出できない | 検証は3項目（不足・余剰・複合 fact）だけだった |
| D10 | 新しい fact や既存 fact への新しい値は人の承認を必須とし、ReviewGates にゲートを1つ足す | Action 正規化と同じく、語彙を増やす判断は人が行う | fact の追加に承認の仕組みがなかった |
| D11 | AsIs 仕様書は書き換えず残し、本書を別に作る | 実装済みの記録と今後の設計を混ぜないため。変更の理由を残すため | — |

## 3. IR v2 の型

### 3.1 全体構造

**図4　IR v2（ClauseIR）の構造**（図は Word 版を参照。以下は図の内容）

- 入力と分類（変更なし）：clause_ref／clause_text／axis1_result／axis2_result
- 規範の主語と行為（変更なし）：actor／action（ActionRef）／scope
- **判定先【新設】**：target.dmn_model／target.entry_decision_id
- **事実【新設】**：facts.new_facts（FactDef）／facts.referenced_facts／facts.value_additions
- **レベル判定【型を変更】**：atomic_fact／intermediate[]／final。outcome は final.action から導出
- 決定表・来歴・レビュー（ほぼ変更なし）：new_decision_tables／rule_row_additions／compound_fact_application／provenance／review_gates／human_review_status

オレンジ色の3グループ（判定先・事実・レベル判定）が新設または変更する部分である。それ以外は9e66776 の型をそのまま使う。

### 3.2 ClauseIR v2 のフィールド

| フィールド | 型 | 意味 | v1 からの変更 |
| --- | --- | --- | --- |
| clause_ref / clause_text | str | 条文の参照名と本文 | なし |
| axis1_result / axis2_result | AxisResult | ②の分類結果 | なし |
| actor | str | 行為の主体（例：あおい） | なし |
| action | ActionRef | 正規化した Action | なし |
| scope | str | 自由記述のラベル。配線では使わない | なし |
| target | DecisionTarget | 判定を入れる DMN モデルと、最終判定の ID | 新設（D7） |
| facts | FactUsage | 新しく定義する fact と、参照する既存 fact | 新設（D1） |
| level_determination | LevelDetermination | レベル判定（層ごとの型） | 型を変更（D6） |
| new_decision_tables | list[NewDecisionTableDraft] | 新しく作る決定表（中間判定・最終判定とも） | なし |
| rule_row_additions | list[RuleRowDraft] | 既存の決定表へ追加する行（中間判定・最終判定とも） | なし |
| compound_fact_application | CompoundFactApplication | 複合条件をどこで表したか | なし |
| provenance | Provenance | 来歴 | なし |
| review_gates | ReviewGates | 人間レビューのゲート | ゲートを1つ追加（D10） |
| human_review_status | pending / approved / rejected | レビュー状態 | なし（保存方法は未決、7章） |

### 3.3 新設・変更する型

| 型 | フィールド | 型 | 意味 |
| --- | --- | --- | --- |
| DecisionTarget | dmn_model | str | 判定を入れる DMN モデル（ファイル）の名前。既存モデルに足す場合はその名前 |
|   | entry_decision_id | str | この条文の最終判定（DRD の最上位）の decision ID。ルーティング表から呼ばれる |
| FactUsage | new_facts | list[FactDef] | この条文で新しく定義する fact。登録簿の層3に追加される |
|   | referenced_facts | list[str] | 層3に登録済みの fact の名前（variable） |
|   | value_additions | dict[str, list[str]] | 既存 fact の possible_values に追加する値（fact 名 → 値の一覧） |
| FactDef | variable | str | fact の名前。DMN の inputData 名と同じ |
|   | primitive | str | 一致プリミティブ。区分の値は passthrough、比較は exact_match など（3.5節） |
|   | possible_values | list[str] | 決定表が参照する値（条文に出てくる値） |
|   | sources | dict[str, SourceRef] | プリミティブの引数名 → 値の取得元 |
|   | comparison_kind | str（任意） | 語彙登録時に人が付ける比較の種類の注釈 |
| SourceRef | from | str | 取得元の種類：action_request（行動要求）／ master（マスタ照会）／ literal（固定値） |
|   | path または value | str | from が action_request・master なら path、literal なら value |
| AtomicFactLayer | covered | bool | 必要な比較を既存の一致プリミティブで賄えるか |
|   | new_primitives | list[str] | 新しく必要なプリミティブ（covered=False のとき） |
| IntermediateChange | table_id | str | 比べた既存の中間判定の決定表 |
|   | relation | ColumnSetRelationKind | 列集合の関係（判断の根拠） |
|   | action | reuse_value / add_row / add_column | 直し方。既存の値を使う／行を足す／列を足す |
|   | new_output_values | list[str] | 中間判定に足す出力値（例：中（氏名なし・2要素一致）） |
| FinalLayer | compared_table_id | str または None | 比べた既存の最終判定の決定表。比べる相手がなければ None |
|   | relation | ColumnSetRelationKind または None | 列集合の関係（判断の根拠） |
|   | action | add_rows / new_table | 既存の最終判定に行を足すか、新しい最終判定を作るか |
| LevelDetermination | atomic_fact | AtomicFactLayer | ① 事実の層 |
|   | intermediate | list[IntermediateChange] | ② 中間判定の層。空なら中間判定は使わない、または既存をそのまま使う |
|   | final | FinalLayer | ③ 最終判定の層 |
|   | outcome（導出） | level1_new_table / level2_row_addition | final.action が new_table なら level1、add_rows なら level2。値は持たず計算する |

ColumnSetRelationKind（exact_match / proper_subset / intersecting_non_subset）は v1 と同じ値を使う。ただし一致プリミティブの exact_match とは別物である。

### 3.4 変更しない型

次の型は v1（コミット 9e66776）のまま使う。本書だけで読めるよう、フィールドの定義を再掲する。

| 型 | フィールド | 型 | 意味 |
| --- | --- | --- | --- |
| AxisResult | label | str | 分類結果のラベル。軸1はハード制約／判断ガイド／エスカレーション基準、軸2は宣言的／手続き的 |
|   | confidence | float | LLM が自己申告する確信度（0〜1）。実測では常に 0.7 を超え、正誤の目安にならない |
|   | rationale | str | LLM が書いた判定理由の文章 |
|   | evidence_quote | str | 判定の根拠として条文から一字一句引用した文字列 |
|   | quote_verified | bool | evidence_quote が条文中に実在するかをプログラムで確かめた結果。False ならハルシネーションの疑い |
|   | vocab_flagged | bool | 誤分類しやすい既知の語彙（認められ／申し出／許可を得／承認を受け・得）を含むか |
|   | strategy_id | str（任意） | どの分類器（プロンプト）で判定したか。現状は使われていない |
|   | confidence_below_threshold | bool（プロパティ） | confidence < 0.7 なら True。レビューのゲートに使う |
| ActionRef | surface | str | 条文中の行為の表現そのまま（例：特権/管理者権限で操作を行う） |
|   | normalized | str | 正規化した Action 名。ルーティング表で文字列一致に使う（例：特権操作） |
|   | parent | str または None | Action の階層上の親（例：システム操作） |
|   | is_new | bool | 既存の Action 語彙にない新しい語か |
|   | approved_by | str または None | 新しい語の階層上の位置づけを承認した人。未承認なら None |
| NewDecisionTableDraft | decision_table_id | str | DMN の decisionTable 要素の ID |
|   | decision_id | str | DMN の decision 要素の ID |
|   | decision_name | str | 判定の名前（DMN の decision 名） |
|   | depends_on_decisions | list[str] | この判定が使う他の判定（中間判定）の ID |
|   | depends_on_inputs | list[str] | この判定が使う入力事実 |
|   | input_columns | list[str] | 決定表の入力列。「判定名.出力名」の形は他の判定の出力を指す |
|   | rule_rows | list[RuleRowDraft] | 決定表の行の一覧 |
|   | knowledge_sources | list[str] | 来歴として紐付ける知識源。計算には使わない |
| RuleRowDraft | row_id | str | 行 ID。DMN の rule id と登録簿の row_id になる |
|   | decision_table_id | str | 所属する決定表。中間判定の表への行追加にも使う |
|   | position | int | FIRST での評価順（1始まり）。小さいほど先に評価される |
|   | conditions | dict[str, str] | 列名 → 条件値。「-」は何でもよい（ワイルドカード） |
|   | outputs | dict[str, str] | 出力名 → 値（例：decision=禁止、reason_code=…） |
| CompoundFactApplication | option_taken | existing_table_row_addition / new_intermediate_table / rejected_compound_in_primitive | 複数条件の組み合わせ（AND）をどこで表すか。決定表の行／中間の表を挟む／一致モジュールで AND を組む（禁止。配線ジェネレータが停止する） |
|   | rationale | str | その表し方を選んだ理由 |
| Provenance | origin | str | 由来の種類。規程の条文なら regulation_clause |
|   | clause_ref | str | 根拠となる条文 |
|   | registered_by | str | 登録した人・仕組み |
|   | registered_at | str（ISO8601） | 登録日時（UTC） |

ReviewGates には次のゲートを1つ足す（D10）。

| ゲート | 立つ条件 | 理由 |
| --- | --- | --- |
| confidence_below_threshold | 軸1または軸2の confidence < 0.7 | v1 のまま |
| quote_unverified | 軸1または軸2の quote_verified=False | v1 のまま |
| vocab_flagged | 軸1または軸2の vocab_flagged=True | v1 のまま |
| new_action_unapproved | ActionRef.is_new=True かつ approved_by=None | v1 のまま |
| new_fact_unapproved【追加】 | facts.new_facts または facts.value_additions が空でない | 語彙（fact・値）を増やす判断は人が行う |

### 3.5 一致プリミティブの使い分け

| 用途 | primitive | sources の引数 | possible_values | 例 |
| --- | --- | --- | --- | --- |
| 区分の値をそのまま渡す | passthrough | value | 決定表が参照する区分の値 | operator_role = システム開発担当 |
| 2つの値が同じかを知る | exact_match | claimed, registered | 一致 / 不一致 / 未提供 | 電話番号の照合 |
| 表記ゆれを吸収して比べる | fuzzy_match_name | claimed, registered | 一致 / 不一致 / 未提供 | 氏名の照合 |
| 日付を比べる | date_match | claimed, registered | 一致 / 不一致 / 未提供 | 生年月日の照合 |
| 分類の階層に含まれるか | ontology_class_match | subject, target_class | 含む / 含まない | 検査結果が病歴に含まれるか |

比較型の結果（一致／不一致）を決定表に書くと、判定の基準（何と比べたか）がバインディング設定の固定値に移り、表から見えなくなる。そのため、区分の値は passthrough を基本とし、比較型は「同じか」自体を知りたい場合に限る。

### 3.6 型の定義（Python の抜粋）

```
@dataclass
class SourceRef:
    from_: Literal["action_request", "master", "literal"]
    path: str | None = None      # action_request / master のとき
    value: str | None = None     # literal のとき

@dataclass
class FactDef:
    variable: str
    primitive: str
    possible_values: list[str]
    sources: dict[str, SourceRef]
    comparison_kind: str | None = None

@dataclass
class FactUsage:
    new_facts: list[FactDef] = field(default_factory=list)
    referenced_facts: list[str] = field(default_factory=list)
    value_additions: dict[str, list[str]] = field(default_factory=dict)

@dataclass
class DecisionTarget:
    dmn_model: str
    entry_decision_id: str

@dataclass
class AtomicFactLayer:
    covered: bool
    new_primitives: list[str] = field(default_factory=list)

@dataclass
class IntermediateChange:
    table_id: str
    relation: ColumnSetRelationKind
    action: Literal["reuse_value", "add_row", "add_column"]
    new_output_values: list[str] = field(default_factory=list)

@dataclass
class FinalLayer:
    compared_table_id: str | None
    relation: ColumnSetRelationKind | None
    action: Literal["add_rows", "new_table"]

@dataclass
class LevelDetermination:
    atomic_fact: AtomicFactLayer
    intermediate: list[IntermediateChange]
    final: FinalLayer

    @property
    def outcome(self) -> LevelOutcome:
        if self.final.action == "new_table":
            return "level1_new_table"
        return "level2_row_addition"
```

### 3.7 IR の中で守る整合条件

IR の各欄は互いに矛盾してはならない。次の条件は配線ジェネレータがクロス検証（5章 C9）で確かめる。

1. final.action が new_table なら、new_decision_tables に target.entry_decision_id の決定表がある
1. final.action が add_rows なら、compared_table_id は target.entry_decision_id の決定表であり、rule_row_additions にその表の行がある
1. intermediate の action が add_row なら、rule_row_additions に table_id の表の行があり、new_output_values の値がその行の出力に現れる
1. intermediate の table_id と final の compared_table_id は、登録簿の層1に実在する
1. atomic_fact.covered が False なら、new_primitives が空でない（新しいプリミティブの追加は③a の範囲外なので、人間レビューに回す）

### 3.8 例：特権アカウント条文の IR v2

ゴールデンパスの条文（ipa_secguide_v4_6_7_1「システム開発を担当する者が本番環境において特権/管理者権限で操作を行うことを禁止する。」）を IR v2 で書くと次のようになる。分類結果・決定表の行・来歴は v1 と同じなので省略する。

```
{
  "actor": "あおい",
  "action": { "surface": "特権/管理者権限で操作を行う", "normalized": "特権操作",
              "parent": "システム操作", "is_new": true, "approved_by": null },
  "target": { "dmn_model": "privileged_operation",
              "entry_decision_id": "decision_privileged_operation" },
  "facts": {
    "new_facts": [
      { "variable": "operator_role", "primitive": "passthrough",
        "possible_values": ["システム開発担当"],
        "sources": { "value": { "from": "action_request", "path": "operator.role" } } },
      { "variable": "target_environment", "primitive": "passthrough",
        "possible_values": ["本番環境"],
        "sources": { "value": { "from": "action_request", "path": "target.environment" } } },
      { "variable": "requested_privilege", "primitive": "passthrough",
        "possible_values": ["特権"],
        "sources": { "value": { "from": "action_request", "path": "requested_privilege" } } }
    ],
    "referenced_facts": [], "value_additions": {}
  },
  "level_determination": {
    "atomic_fact": { "covered": true, "new_primitives": [] },
    "intermediate": [],
    "final": { "compared_table_id": null, "relation": null, "action": "new_table" }
  }
}
```

v1 との違いは3点ある。fact が IR の中に入ったこと、primitive が実態どおり passthrough になったこと、possible_values から条文にない値（運用担当・検証環境など）が消えたことである。この IR では new_action_unapproved と new_fact_unapproved の2つのゲートが立ち、人間レビューに回る。

比較のため、中間判定を使う第14条（口頭伝達の制限）のレベル判定を IR v2 で書くと次のようになる。

| 層 | 値 | 読み方 |
| --- | --- | --- |
| ① 事実 | covered = True | 既存のプリミティブで賄える |
| ② 中間判定 | table_id = dt_identity、relation = proper_subset、action = add_row、new_output_values = [中（氏名なし・2要素一致）] | 本人確認判定に行を1つ足し、新しい確認レベルの値を加える |
| ③ 最終判定 | compared_table_id = dt_transmission、relation = proper_subset、action = new_table | 列の上では送信可否判定に行を足せるが、行為（口頭伝達）が違うので新しい表を作る |
| 結論 | outcome = level1_new_table（導出） | — |

③で「関係は真部分集合だが、結論は新しい表」となっている。v2 では関係（根拠）と action（結論）を別の欄に持つので、この判断を型の上で表せる。

## 4. 4点セットの導き方

v2 では、4点セットのすべてを IR v2 だけから作る。配線ジェネレータの引数から new_bindings と DMN モデル名を外し、代わりに登録簿（既存の決定表と fact 台帳）を読み込む。

**図5　IR v2 から4点セットへの導き方**（図は Word 版を参照。以下は図の内容）

| IR v2 の欄 | 使われる成果物 |
| --- | --- |
| target | DMN XML、ルーティング表、登録簿 |
| actor／action | ルーティング表 |
| facts | バインディング設定、ルーティング表、登録簿（層3） |
| new_decision_tables／rule_row_additions | DMN XML、登録簿（層1・層2） |
| provenance／review | 登録簿 |

4点すべてが IR v2 だけから作られる（v1 ではバインディング設定だけ IR の外から渡していた）。

```
generate_wiring_bundle(
    ir: ClauseIR, *,
    registry: Registry,   # 既存の登録簿（層1・層2・層3）
) -> WiringBundle         # dmn_xml, bindings, routing_table, registry（更新後）
```

### 4.1 required_facts の求め方

ルーティング表とバインディング設定は、最終判定を評価するのに実際に集める必要がある事実（required_facts）を基にする。中間判定を使う場合、最終判定の入力列には「本人確認判定.identity_level」のような他の判定の出力が入る。そのため、入力列をそのまま写すのではなく、依存関係をたどって求める。

1. target.entry_decision_id の決定表の input_columns から始める
1. 「判定名.出力名」の形の列は、その判定（IR の new_decision_tables、または登録簿の層1）の input_columns に置き換える
1. 2を、ドットを含む列がなくなるまで繰り返す
1. 残った列の集合が required_facts である。すべて層3（fact 台帳）か facts.new_facts にあること
第14条の例では、口頭伝達可否判定の入力列「本人確認判定.identity_level」が本人確認判定の入力列に置き換わり、required_facts は {name_match, birth_match, phone_match} になる。

### 4.2 DMN XML

| 出力する項目 | IR v2 のどこから |
| --- | --- |
| definitions の id・name | target.dmn_model |
| decision・decisionTable・rule | new_decision_tables（新規の表）。rule_row_additions は既存モデルへの行追加として出力する |
| inputData | new_decision_tables の input_columns のうちドットを含まない列 |
| knowledgeSource・authorityRequirement | new_decision_tables の knowledge_sources |

既存のモデルに行を足す場合（レベル2、または中間判定への行追加）は、そのモデルの DMN XML を読み込み、position の位置に rule を挿入して出力する。v1 はこの経路を持たないので、v2 で追加する。

### 4.3 バインディング設定

| 出力する項目 | IR v2 のどこから |
| --- | --- |
| 対象とする fact | required_facts のすべて |
| 各 fact の定義 | facts.new_facts にあればそれを、なければ層3（fact 台帳）の定義を使う。value_additions があれば possible_values に加える |
| dmn_model | target.dmn_model |

### 4.4 ルーティング表

| 項目 | IR v2 のどこから | 例（特権アカウント） |
| --- | --- | --- |
| actor・action | actor、action.normalized | あおい、特権操作 |
| dmn_model | target.dmn_model | privileged_operation |
| decision_id | target.entry_decision_id | decision_privileged_operation |
| required_facts | 4.1節の手順で求める | operator_role, target_environment, requested_privilege |

### 4.5 Scope/Decision 登録簿

| 層 | 内容 | v1 からの変更 |
| --- | --- | --- |
| 層1（decision_tables） | 決定表ごとのメタ情報。decision_table_id・decision_id・decision_name・hit_policy・input_columns・depends_on・knowledge_sources・provenance | dmn_model を追加（D8） |
| 層2（rule_rows） | ルール行1本ずつの台帳。row_id・position・conditions・decision・reason_code・provenance・history | なし |
| 層3（fact_registry） | fact 台帳。FactDef の各フィールドに加え、defined_by（最初に定義した条文）・used_by（使っている決定表）・provenance・history | 新設（D2） |
| review | 条文単位のレビュー状態。human_review_status・should_review・gate_reasons | gate_reasons に加え、ゲート名の一覧（gates）も持たせる |

v1 の gate_reasons は日本語の説明文だけで、集計に向かなかった。v2 ではゲート名（例：new_fact_unapproved）の一覧も併せて出力する。

## 5. クロス検証

配線ジェネレータは、4点セットを作った後に次の項目を確かめ、1つでも失敗すれば SchemaWiringError で停止する。C1〜C3 は v1 から引き継ぎ、C4〜C10 を追加する（D9）。

| # | 検証 | 内容 | 区分 |
| --- | --- | --- | --- |
| C1 | fact の不足 | required_facts のすべてが facts.new_facts か層3にある | v1 から |
| C2 | fact の余剰 | facts.new_facts のすべてが、どこかの決定表で使われている | v1 から |
| C3 | 複合 fact の拒否 | compound_fact_application が rejected_compound_in_primitive でない | v1 から |
| C4 | sources と引数 | sources のキーが、primitive の引数名と一致する（3.5節） | 追加 |
| C5 | 決定表の値 | 決定表の各条件の値（「-」以外）が、その fact の possible_values（value_additions を含む）にある | 追加 |
| C6 | 参照 fact の実在 | facts.referenced_facts と value_additions のキーが層3に実在する | 追加 |
| C7 | 判定先の実在 | target.entry_decision_id が DMN XML と層1に実在する | 追加 |
| C8 | 集める事実の一致 | ルーティング表の required_facts とバインディング設定の fact の集合が一致する | 追加 |
| C9 | レベル判定の整合 | 3.7節の5条件を満たす | 追加 |
| C10 | ドット参照の解決 | 「判定名.出力名」の判定が実在し、その出力名を持つ | 追加 |

各検証には、わざと違反させた IR で SchemaWiringError になることを確かめる失敗テストを付け、リポジトリにコミットする。v1 では C1〜C3 の失敗テストが手元で確認されただけで、コミットされていなかった。

> C5 は v1 で検出できなかったズレ（possible_values に決定表の値がない）を捕まえる。C4 は sources に意味のないキーが入ったまま通ってしまう問題を捕まえる。いずれも AsIs 仕様書の4.3節で、エラーにならずに通ることを確認した項目である。

## 6. 正解 IR の作り方

前段の自動化を評価するには、「この条文ならこの IR が正解」というデータが必要である。②の改善が進んだのは28件の正解ラベルで毎回測定できたからで、前段でも同じ仕組みを用意する。正解 IR を手で作る作業は、IR v2 の型で実際の条文を表しきれるかの検証も兼ねる。

### 6.1 件数と選び方

5〜10件を作る。テストデータ28件のうち、ハード制約 × 宣言的の条文から選ぶ。次の型を少なくとも1件ずつ含める。

| 型 | 確かめたいこと | 候補 |
| --- | --- | --- |
| 新しい表を作る単純な条文 | v2 の基本形。v1 のゴールデンパスを v2 で書き直す | ipa_secguide_v4_6_7_1（特権アカウント） |
| 既存の fact を再利用する条文 | referenced_facts と value_additions、fact 台帳の更新 | 特権アカウントと同じ役割・環境を見る条文（28件から選ぶ） |
| 既存の表に行を足す条文（レベル2） | final.action = add_rows と、既存モデルへの行挿入 | 特権アカウントの表に行を足す条文（28件から選ぶ） |
| 中間判定を使う条文 | required_facts の算出、intermediate の書き方 | 第14条（口頭伝達の制限） |
| 値の種類が多い条文 | possible_values と C5 | 28件から選ぶ |

28件は IPA 情報セキュリティ関連規程と厚生労働省モデル就業規則が出典で、中間判定を使う条文やレベル2の条文が含まれない可能性がある。その場合は、既存設計の実例である第14条を加える。第14条には、あおいの送信可否判定（本人確認判定・情報分類判定・送信可否判定）が既存の決定表として登録簿にあることが前提になるので、これを初期データとして登録する。

### 6.2 作り方

1. IR に入れる情報は、条文本文と、出典付きで明示的に付与した context に限る（1.4節）
1. 1件ずつ JSON で作り、testdata/ir_gold/ に置く
1. 配線ジェネレータに通し、C1〜C10 をすべて通過することを確かめる
1. DMN 評価のシナリオ（入力の事実と期待する判定）を付け、自作エンジンで期待通りになることを確かめる
1. Kazuhiro さんがレビューし、承認したものを正解とする

### 6.3 前段での使い方

前段は要素ごとに自動化する（Action 正規化 → Scope 属性の抽出 → 行の構成 → レベル判定）。正解 IR は、要素ごとに正解と比べられる形で使う。たとえば Action 正規化なら action.normalized と is_new、Scope 属性の抽出なら facts の variable と possible_values を比べる。

## 7. 未決事項

本書で決めきれなかった論点を挙げる。U1・U2 は前段の評価に影響するので、正解 IR を作る段階で方針を決めたい。

| # | 論点 | 内容 | 当面の扱い |
| --- | --- | --- | --- |
| U1 | 値の語彙の管理 | passthrough では「開発担当」と「システム開発担当」のような表記ゆれが吸収されない | fact 台帳の possible_values を統制語彙とし、新しい値は new_fact_unapproved で人が承認する。正規化の仕組みは別途 |
| U2 | 既定の行が「許可」になる | 最後のワイルドカード行が許可なので、知らない値や未登録の値もすべて許可に落ちる（安全側に倒れない） | 既定の行を許可にするか、確認を求める出力にするかを条文の性質ごとに決める必要がある |
| U3 | レビュー結果の保存 | human_review_status を approved／rejected に変えた結果を記録して IR に反映する仕組みがない | 未定 |
| U4 | 実行時の仕組み | 一致プリミティブ・オーケストレータが未実装で、配線が実行時に動くかは未検証。Kogito 実機での評価も未実施 | 前段と並行するかは別途判断 |
| U5 | 複数の最終判定 | 1つの条文が複数の Action に関わる場合、最終判定が複数になる | v2 は1条文1最終判定とする。必要になった時点で拡張 |
| U6 | エスカレーション系の条文 | 承認・期限を伴う条文は単発の決定表で表しきれず、③b の領域に入る | ③a は ハード制約 × 宣言的 に限る |
| U7 | fact を変えたときの影響 | fact 台帳の定義を変えると、その fact を使う全ての決定表に影響する | used_by で影響範囲を出せるようにしておく。再検証の手順は別途 |

## 8. 用語集

本書と AsIs 仕様書に出てくる用語をまとめる。今後はこの用語集を正とする。

### 8.1 プロジェクト全体

| 用語 | 意味 |
| --- | --- |
| 擬人化アーキテクチャ | AI エージェントを「新しい従業員」として迎え、人と同じ就業規則・社内規程で統制するという考え方 |
| あおい | 設計の題材にしている架空の医療事務 AI エージェント |
| aoi_doc2dmn | 自然言語の規程から、AI エージェントの行動を制御する決定表（DMN）を自動生成するプログラムのリポジトリ |
| 抽出パイプライン | 規程から決定表を作る5段階の工程。①条文抽出・関連フィルタ、②2軸分類、③構造化変換（③a 宣言的／③b 手続き的）、④人間レビュー、⑤登録・反映 |
| 前段／後半 | ③a を「条文 → IR」（前段、未実装）と「IR → 4点セット」（後半、実装済み）に分けたもの |
| ゴールデンパス | 例外や複雑な分岐を含まない、最も単純な正常系の1本道。③a では特権アカウント条文1件で通した |
| HANDOFF | web の Claude とサーバ上の Claude Code の間で作業を引き継ぐ Markdown 文書 |

### 8.2 ②分類

| 用語 | 意味 |
| --- | --- |
| 軸1／軸2 | 条文の層（ハード制約／判断ガイド／エスカレーション基準）と時間構造（宣言的／手続き的）の分類（1.2節） |
| gold label（正解ラベル） | テストデータに付けた軸1・軸2の正解。条文本文と明示的な context だけを根拠に付ける |
| オラクル（--use-oracle） | ②の分類器を呼ばず、正解ラベルを使う実行モード |
| confidence | LLM が自己申告する確信度。実測では常に 0.7 を超え、正誤の目安にならない |
| evidence_quote／quote_verified | 判定根拠の逐語引用と、それが条文に実在するかの検証結果 |
| vocab_flagged | 誤分類を繰り返した既知の語彙（認められ／申し出／許可を得／承認を受け・得）を含む条文に立てるフラグ |
| もぐら叩き | ある誤分類を直すと、別の条文が新たに誤分類される現象 |

### 8.3 IR と生成

| 用語 | 意味 |
| --- | --- |
| IR（中間表現） | 条文の規範を、決定表を作れる形に整理した内部データ。前段の出力で、後半の入力 |
| ClauseIR | 1条文分の IR を表す Python のクラス |
| Actor × Action × Scope → Decision | 規範の基本形。「誰が、何を、どの範囲で行うと、許可か禁止か」 |
| Action 正規化 | 条文中の行為の表現を、登録済みの Action 名にそろえること。新しい語は人が承認する |
| fact（事実） | 決定表が見る入力の項目。決定表の「列」になる |
| FactDef | fact の定義（名前・一致プリミティブ・possible_values・sources）。v2 で IR に入る |
| fact 台帳（層3） | 複数の条文で共有する fact の定義を置く、登録簿の層3。v2 で新設 |
| 一致プリミティブ | fact の値を作る固定の関数（passthrough・exact_match など）。条文からは作らない |
| possible_values | fact について決定表が参照する値の一覧 |
| sources | 一致プリミティブの引数の値をどこから取るか（行動要求・マスタ・固定値） |
| 複合 fact 禁止原則 | 複合判定を一致プリミティブに持たせず、決定表の行として表すという原則 |
| レベル1／レベル2 | 新しい条文を、新しい決定表にするか（レベル1）、既存の表に行として足すか（レベル2） |
| 列集合の関係 | 新しい条文が見る fact の集合と、既存の表の列の集合の関係。完全一致／真部分集合／交差するが部分集合でない |
| 配線 | 同じ変数名・キーが4点セットの間で一致してつながっている状態 |
| 配線ジェネレータ | IR から4点セットを生成し、配線の整合を検証する部品（wiring_generator.py） |
| 4点セット | DMN XML・バインディング設定・ルーティング表・Scope/Decision 登録簿 |
| クロス検証 | 4点セットと IR の間の整合を機械的に確かめること。失敗すると SchemaWiringError |
| required_facts | 最終判定を評価するために実際に集める事実。中間判定の先までたどって求める |
| ④人間レビュー／ReviewGates | 自動処理の結果を人が確かめる工程と、そこへ回すかを決める独立したゲート |
| provenance（来歴） | ルールがどの条文に由来し、誰がいつ登録したかの記録 |

### 8.4 DMN と実行

| 用語 | 意味 |
| --- | --- |
| DMN | Decision Model and Notation。業務の判定ロジックを決定表で表す OMG の国際標準 |
| DMN XML（DMN モデル） | DMN の内容を XML で書いたファイル（.dmn）。実行エンジンが読み込んで判定する |
| 決定表 | 入力列・出力列・行（ルール）からなる表 |
| DRD | Decision Requirements Diagram。判定どうしの依存関係を示す図 |
| 中間判定／最終判定 | 他の判定の入力になる判定と、最後に行動の可否を出す判定（1.3節） |
| エントリポイント | ルーティング表から呼ばれる最終判定。IR v2 の target.entry_decision_id |
| ヒットポリシー／FIRST | 複数の行に当てはまったときの扱い。FIRST は上から最初に当てはまった行を採用する |
| ワイルドカード（-） | 決定表のセルで「何でもよい」を表す記号 |
| FEEL | 決定表のセルに書く式言語 |
| inputData／knowledgeSource | DMN で外から与える事実と、来歴専用の情報源 |
| informationRequirement／authorityRequirement | 計算に必要な依存と、来歴を示すだけの紐付け |
| Kogito（Apache KIE） | DMN を実行するオープンソースのエンジン |
| 自作エンジン | DMN を簡易的に評価する Python の検証用エンジン（dmn_engine.py）。過去に Kogito と 18/18 一致 |
| オーケストレータ | 実行時に行動要求を受け、ルーティング表で判定を選び、事実を集めて DMN を呼ぶ部品。未実装 |
| バインディング設定 | 各 fact の値をどう作るかを宣言する JSON |
| ルーティング表 | actor × action から、呼ぶ判定・DMN モデル・集める事実を引く表 |
| Scope/Decision 登録簿 | 決定表（層1）・ルール行（層2）・fact（層3）を来歴・履歴・レビュー状態と共に記録する台帳 |

## 付録A　v1（9e66776）と v2 の差分

| 項目 | v1（9e66776） | v2（本書） |
| --- | --- | --- |
| fact の定義 | IR の外（BINDINGS 定数） | IR の facts。共有分は登録簿の層3 |
| 一致プリミティブ | 全 fact を exact_match と宣言（実態は passthrough） | 区分の値は passthrough、比較は比較型 |
| possible_values | 条文にない値を含む | 決定表が参照する値だけ |
| sources | 「action_request.operator.role」形式の文字列 | {from, path} / {from: literal, value} |
| レベル判定 | 3層とも同じ「列集合の関係」、outcome は別に記入 | 層ごとの型、outcome は導出 |
| 判定先 | DMN モデル名は引数で渡す | IR の target（dmn_model・entry_decision_id） |
| ルーティング表 | actor×action と入力列 | dmn_model・decision_id・required_facts を追加 |
| 登録簿 | 層1・層2・review | 層1に dmn_model、層3（fact 台帳）を新設、review にゲート名 |
| クロス検証 | 3項目 | 10項目 |
| ReviewGates | 4ゲート | 5ゲート（new_fact_unapproved を追加） |
| 既存モデルへの行追加 | 経路なし（新規ファイルのみ） | 既存の DMN XML に rule を挿入する経路を追加 |

