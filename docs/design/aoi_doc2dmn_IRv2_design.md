# aoi_doc2dmn ③a 中間表現（IR）v2 設計書（Markdown 版）

> **この文書について**
> - Word 版「aoi_doc2dmn_IRv2_設計書.docx」v1.4（2026-10-23）の Markdown 版。正本は Word 版で、図は文章と表で置き換えている。
> - **③a の IR 設計の基準文書である。** 次の旧文書と食い違う点は、本書を優先する。
>   - gijinka_dmn_generation_design_v1.docx：バインディング設定（fact の定義は IR に入れ、共有分は登録簿の層3に置く。区分の値は passthrough）、ルーティング表（dmn_model・decision_id・required_facts を持つ）、登録簿（層1に dmn_model、層3 fact 台帳を新設）
>   - gijinka_extraction_pipeline_design_v1.docx：レベル1／2判定（3層とも同じ「列集合の関係」で表すのをやめ、層ごとの型にする。関係＝根拠と action＝結論を分ける）
>   - 4点セットの同時生成、複合 fact 禁止、FIRST、Action 正規化などの原則は旧文書から引き継ぐ（1.4節）。
> - v1 の実装の記録（コミット 9e66776 時点）は claude/aoi_doc2dmn_AsIs_spec_9e66776.md を参照（リポジトリでは docs/design/ に置く）。

| 項目 | 内容 |
| --- | --- |
| 版 | v1.4（ToBe 設計・レビュー待ち。v1.1 からの変更は付録B、v1.2 からは付録C、v1.3 からは付録D） |
| 作成日／改訂日 | 2026-10-16／2026-10-23 |
| 作成 | Claude（Kazuhiro さんとの議論に基づく） |
| 対象 | aoi_doc2dmn ③a（宣言的 × ハード制約） |
| 基準とした実装 | v1：コミット 9e66776（AsIs）／v2 初回実装：コミット 10fc38b／v1.2 実装：コミット 71d3424／v1.3 実装：コミット e6e3ee6 |

## はじめに

本書は、③a の中間表現（IR）をどう直すかを決める設計書（ToBe）である。前段（条文 → IR）の自動化に入る前に、前段が出力する IR の形を確定させることを目的とする。

### 本書の位置づけ

③a の現状は、別文書「aoi_doc2dmn ③a IR→4点セット配線 仕様書」（以下、AsIs 仕様書）に記録している。AsIs 仕様書は「コミット 9e66776 の実装が今どうなっているか」、本書は「これからどうするか」を書く。2つを分けたのは、実装済みの内容と今後の設計が混ざらないようにするためと、変更の理由（現状の問題）を記録として残すためである。

| 文書 | 内容 | 時点 |
| --- | --- | --- |
| AsIs 仕様書（Claude Docs）<br>https://claude.ai/code/artifact/11cc2737-119c-4776-8f64-d7085b0afbce | IR・配線ジェネレータ・4点セットの実装の記録と、そこで見つかった問題 | コミット 9e66776 |
| 本書（IR v2 設計書） | IR の改訂内容、4点セットの導き方、クロス検証、正解 IR の作り方 | v1.1 までの内容はコミット 10fc38b、v1.2 の内容はコミット 71d3424、v1.3 の内容はコミット e6e3ee6 で実装済み。v1.4 の追加分はこれから |

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

次の D12〜D23 は、IR v2 の初回実装（コミット 10fc38b）の報告（第16回）で見つかった論点を受けて、2026年10月23日に追加した。

| # | 決定 | 理由 | 初回実装からの変更 |
| --- | --- | --- | --- |
| D12 | 登録簿の層1に output_columns（出力列の名前の一覧）を持たせる。出力の値は層1に書かず、層2の行から集める | 層1は表の形を表す。行がまだない判定（初期登録した直後など）でも出力列が分かるようにするため。値を2か所に持たない | 層1に出力列がなく、C10 は層2の行から集めて代用していた |
| D13 | クロス検証 C11 を追加する。「判定名.出力名」の列の条件値が、参照先の判定の出力値にある | C5（fact の値）の中間判定版。綴りの違いや中間判定への行の追加し忘れで、絶対に当たらない行ができるのを防ぐ | ドット参照の列の値は検証していなかった |
| D14 | fact の承認を記録する。FactDef に approved_by を足し、value_additions は値ごとに approved_by を持つ形にする。new_fact_unapproved は「承認者のいない新しい fact・値がある」ときに立つ | Action の approved_by と揃える。「新語彙あり」と割り切ると、新しい fact を持つ条文でゲートが常に立ち、目印として機能しない | ゲートを解除する手段がなかった |
| D15 | 既存の DMN モデルへの行の挿入を実装する。DMN ファイルは registry/dmn/<dmn_model>.dmn に置き、層1の dmn_model から引く。C7 は add_rows の場合も、挿入後の DMN XML に判定があることを確かめる | レベル2と中間判定への行追加を受け入れるため | 既存 DMN への挿入は範囲外で、add_rows の IR は C7 の DMN XML 側を通せなかった |
| D16 | 既存の表に行・列・出力値を足した IR では、その表を（間接的にも）使うすべての最終判定を洗い出し、それぞれのルーティング表の行とバインディング設定を作り直して出力する。洗い出しは層1の depends_on_decisions を逆にたどる。作り直すために、層1に entry_points（その最終判定を呼ぶ actor・action の組）を持たせる | 中間判定に行を足すと、それを使う全ての最終判定の結果が変わりうる。entry_decision_id の1本分だけ作ると、足した fact を実行時に誰も集めない | ルーティング表とバインディング設定は entry_decision_id の1本分だけだった |
| D17 | 中間判定を参照する最終判定は、参照先と同じ DMN モデルに入れる。DMN のモデル間 import は当面使わない | D16 の作り直しとドット参照の解決を1つのモデルの中で閉じるため | 規定がなかった |
| D18 | CompoundFactApplication.option_taken に new_table_row（新しく作る表の行として表す）を足す | 新規の表を作りながら existing_table_row_addition と名乗る誤ラベルをなくす | 選択肢がなく、ゴールデンパスは existing_table_row_addition を充てていた |
| D19 | IntermediateChange に new_input_columns（add_column のとき足す入力列）を足す。足した列は層1の input_columns に加え、既存の全行をその列「-」で埋める | 追加列が rule_row_additions の条件キーにしか現れず、required_facts の算出から漏れていた | 追加列を指す欄がなかった |
| D20 | ReviewGates に new_primitive_required（atomic_fact.covered=False）を足す | 新しいプリミティブが必要な条文は③a の範囲外なので、人間レビューに回す。回す先のゲートが定義されていなかった | ゲートがなかった |
| D21 | facts.referenced_facts は、IR が使う既存 fact を漏れなく列挙する欄とする。C6 で「層3に実在する」ことと「漏れがない」ことの両方を確かめる | IR だけを見て、条文が使う fact がすべて分かるようにするため。前段の出力と正解 IR を比べるときにも使う | 列挙しなくても検証を通り、規範か記録かが不明だった |
| D22 | knowledge_sources には、条文または出典付きの context に根拠があるものだけを書く。ゴールデンパスの ks_privileged_account_registry は削除する | これを参照していた exact_match の registered 引数が D3 で消え、根拠がなくなった（1.4節「IR に入れてよい情報」） | v1 のまま残していた |
| D23 | identifiability_classifier は、あおいの送信可否判定の初期登録分（provenance.origin = bootstrap）でだけ使える。新しい条文の new_facts では使えない（C4 で止める） | 既存の境界事例として据え置く（gijinka_dmn_generation_design_v1 7.4節）。新規には複合 fact 禁止原則に従わせる | 一致プリミティブの一覧から外していたため、情報分類判定を初期登録できなかった |

次の D24〜D35 は、v1.2 の実装（コミット 71d3424）の報告（第18回）で見つかった逸脱6件と懸念8件を受けて、2026年10月23日に追加した。対応関係は付録Cにまとめる。

| # | 決定 | 理由 | v1.2 からの変更 |
| --- | --- | --- | --- |
| D24 | D16 の洗い出しの起点は、IR が実際に行・列・出力値を足した既存の表に限る（rule_row_additions の表、intermediate の add_row・add_column の表、new_output_values を足した表、final が add_rows の表）。reuse_value の表と、new_table の compared_table_id は起点にしない | どちらも比べた相手で、表には何も足さない。含めると、新しい表を作るだけの IR が既存の DMN ファイルを要求して止まる | intermediate の table_id と final の compared_table_id をすべて起点にしていた |
| D25 | 「IR が使う既存 fact」は、IR が書いた行と表から求める。新規の表の input_columns と、rule_row_additions の条件のうち「-」以外の列、new_input_columns を集め、ドット参照の列と new_facts を除いたもの。C6 はこれが referenced_facts と一致することを確かめる | required_facts（ルート全体）を基準にすると、既存の最終判定に1行足すだけの条文に、その判定の全 fact の宣言を強いる。条文が言及した fact だけを列挙させるため | C6 の網羅を required_facts で確かめていた（初回実装では IR 自身のルート、v1.2 の記述ではそれ以上） |
| D26 | D25 により C6 は IR だけで判定できるので、生成前の位置（C4 の後）で1回だけ行う。C1 は、参照先の中間判定が使う fact も含めた required_facts 全体の不足を捕まえる歯止めとして残す | C6 を実在と網羅に分ける必要がなくなる。C1 には D25 の後も固有の役割（IR が言及しない中間判定の fact）が残る | C6 を2つに分け、網羅を required_facts の導出の後に置いていた（第18回の逸脱#2） |
| D27 | required_facts の手順1は、入力列にその IR が add_column で足す列を加えてから始める | D19 で層1の input_columns に足すことにしたが、4.1節の手順に反映していなかった | 手順に記述がなかった（第18回の逸脱#6） |
| D28 | 層2の conditions・outputs の正本は DMN の FEEL 表記のまま持つ（"中","未" のような複数値も1つの文字列）。値を比べる処理（C5・C11 など）は、FEEL のセル → 値の集合 に変換する1つの関数だけを通す | 往復で情報を落とさないため。比べる場所ごとに揃え方を実装すると、検証の間でずれる | 揃え方の規定がなかった（第18回の逸脱#5） |
| D29 | IntermediateChange.new_output_values を dict[str, list[str]]（出力名 → 足す値）にする | 中間判定は出力を複数持つ。値の一覧だけでは、どの出力列の値かが分からず、列の取り違えを型で防げない | list[str] だった |
| D30 | FinalLayer.action に no_change（最終判定は変えない）を足す。このとき compared_table_id・relation は None、target.entry_decision_id は影響を受ける既存の最終判定の1つ、outcome は level2_intermediate_only とする | 中間判定だけを直す条文を、存在しない最終判定を作らずに表すため | add_rows と new_table の2択だった |
| D31 | CompoundFactApplication の option_taken は手で書かず、IR の他の欄から決定表ごとに導出する。手で書くのは rejected_in_primitive（一致モジュールで AND を組もうとして拒否された）の印だけとする | 1つの IR が新しい表の行と既存の表の行を両方作ることがある。導出できる欄を手で書かせると、正解 IR と前段の出力の比較で揺れる | IR に1つだけ手で書いていた |
| D32 | 初期登録では、DMN XML に加えて、出典（ファイルと行）を明示した設計資料から登録してよい。あおいの entry_points は sequence_diagram_explained.md の記述を出典とする | 1.4節の「出典付きで明示的に付与した context」に当たる。DMN に actor・action は書かれないので、これがないと D16 が成立しない | 規定がなかった（第18回の懸念#2） |
| D33 | 初期登録で取得元が分からない fact は、層3の sources を空にしてよい。バインディング設定には sources_undefined = true を付けて出力し、空であることを隠さない | 取得元を補うと創作になる。必要になるのは一致プリミティブの実装（U4）のときで、それまでは未定義であることが見えれば足りる | 空の sources がそのまま出力されていた（第18回の懸念#1） |
| D34 | 初期登録の元の DMN に不足・矛盾があれば、reference/ の元ファイルは書き換えず、registry/ の側で修正する。修正は層1・層2の history に内容・理由・承認者を残す。修正後の登録簿に C5・C11 を適用し、違反があれば止める。あおいの送信可否判定では、tr_r3・tr_r5 の「未」と、参照されない inputData（added by me）を削除する（承認：Kazuhiro） | 「未」は identity_level に決して来ない値で、消しても評価結果は変わらない。残すと正解 IR で参照するたびに C11 に当たる | find_problems() が報告するだけで、矛盾を抱えたまま登録していた（第18回の懸念#8） |
| D35 | 既存の表への行の挿入は、その表の hit_policy が FIRST の場合に限る。FIRST でなければ停止する。新規の表は常に FIRST で作り、IR に hit_policy の欄は持たせない。depends_on_inputs には inputData の名前（input_columns と同じ）を入れる | position による挿入は FIRST でしか意味を持たない。名前で揃えると input_columns・required_facts・バインディング設定と同じ語で引ける | 検査がなく、depends_on_inputs の値も規定がなかった（第18回の懸念#6・#7） |

次の D36〜D43 は、v1.3 の実装（コミット e6e3ee6）の報告（第20回）を受けて、2026年10月23日に追加した。D43 は正解 IR の作成に入るための決定である。対応関係は付録Dにまとめる。

| # | 決定 | 理由 | v1.3 からの変更 |
| --- | --- | --- | --- |
| D36 | new_output_values には、その表がまだ出していない出力値だけを書く。C9 条件3は双方向の一致とする：追加した行の出力値のうち、その表（層2の既存行）がまだ出していない値の集合が、new_output_values と出力名ごとに一致する | 片方向（宣言した値が行にある）だけだと、行が新しい値を黙って出しても止まらない。参照する最終判定の行に当たらず、既定の行（U2 では許可）に落ちる。既にある値を書くかどうかの揺れもなくなる | 宣言した値が行に現れることだけを確かめていた |
| D37 | new_output_values を値ごとに承認者を持つ形（dict[str, list[ValueAddition]]）にする。承認者のいない出力値があれば new_fact_unapproved を立てる | 中間判定の出力値を増やすと、それを参照するすべての最終判定の結果に影響しうる。fact の値の追加（D14）より影響が大きいのに、承認の仕組みがなかった | 値の一覧だけで、承認者がなかった |
| D38 | NewDecisionTableDraft の depends_on_decisions と depends_on_inputs は手で書かず、input_columns から導出する（ドット参照の判定名と、ドットを含まない列） | どちらも input_columns から機械的に決まる。手で書かせると、正解 IR と前段の出力の比較で揺れ、食い違っても検査されない（D31 と同じ考え方） | IR に手で書き、そのまま層1に写していた |
| D39 | 既存の DMN モデルの namespace は変えない。http://gijinka.example.com/dmn/<dmn_model> の規則は、新しく作るモデルにだけ使う | namespace は実行エンジンや利用側がモデルを識別する名前で、後から変えると参照が切れる | 規定がなく、あおいのモデル（…/dmn/aoi）と規則が食い違って見えた |
| D40 | 層1に history を持たせる。DMN モデル全体に関わる修正（参照されない inputData の削除など）は、そのモデルに属する層1のすべてのエントリに同じ記録を残す | どの決定表から見ても、registry/ の DMN が元と違う理由が分かるようにする。モデル単位の層は、モデルが複数になった時点で検討する | 4.5節の層1に history がなかった |
| D41 | referenced_facts は条文の語ではなく、書いた行と表から決める（D25 の基準のまま）。option_taken の導出は、その IR を作った時点の判断の記録とし、後の条文が参照しても変えない | 条文が言及しても行の条件が「-」なら、判定には使っていない。option_taken はその条文が複合条件をどう表したかの記録である | 正解 IR を書く人への注記がなかった |
| D42 | あおいの元の DMN（aoi_transmission_baseline.dmn）を tests/fixtures/ にコミットし、元の DMN を使うテストがリポジトリだけで動くようにする | reference/ はリポジトリに入っておらず、修正前後の一致などの肝心のテストが他の環境で確かめられない | 37件のテストが reference/ のない環境で飛ばされていた |
| D43 | 正解 IR は、testdata/ir_gold/ に1条文1ファイルで置き、適用順を manifest.json に書く。評価は、初期登録した登録簿から始めて、manifest の順に IR を配線ジェネレータに通し、出力された登録簿を次の IR の入力にする（メモリ上だけで行い、registry/ には反映しない） | 既存の fact を再利用する条文やレベル2の条文は、先に定義された fact・表を前提にする。promotion（U8）を実装せずに、条文の間の依存を再現できる | 正解 IR の置き方と評価の手順が決まっていなかった |

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
| facts | FactUsage | 新しく定義する fact と、参照する既存 fact | 新設（D1・D14・D21） |
| level_determination | LevelDetermination | レベル判定（層ごとの型） | 型を変更（D6） |
| new_decision_tables | list[NewDecisionTableDraft] | 新しく作る決定表（中間判定・最終判定とも） | なし |
| rule_row_additions | list[RuleRowDraft] | 既存の決定表へ追加する行（中間判定・最終判定とも） | なし |
| compound_fact_application | CompoundFactApplication | 一致モジュールで複合条件を組もうとして拒否された印。表し方（option_taken）は決定表ごとに導出する | 選択肢を追加（D18）、導出に変更（D31） |
| provenance | Provenance | 来歴 | なし |
| review_gates | ReviewGates | 人間レビューのゲート | ゲートを2つ追加（D10・D20） |
| human_review_status | pending / approved / rejected | レビュー状態 | なし（保存方法は未決、7章） |

### 3.3 新設・変更する型

| 型 | フィールド | 型 | 意味 |
| --- | --- | --- | --- |
| DecisionTarget | dmn_model | str | 判定を入れる DMN モデル（ファイル）の名前。既存モデルに足す場合はその名前 |
|   | entry_decision_id | str | この条文の最終判定（DRD の最上位）の decision ID。ルーティング表から呼ばれる |
| FactUsage | new_facts | list[FactDef] | この条文で新しく定義する fact。登録簿の層3に追加される |
|   | referenced_facts | list[str] | IR が使う既存 fact（層3に登録済み）の名前を漏れなく列挙する（D21） |
|   | value_additions | dict[str, list[ValueAddition]] | 既存 fact の possible_values に追加する値（fact 名 → 値と承認者の一覧）（D14） |
| ValueAddition | value | str | 追加する値 |
|   | approved_by | str または None | この値の追加を承認した人。未承認なら None（D14） |
| FactDef | variable | str | fact の名前。DMN の inputData 名と同じ |
|   | primitive | str | 一致プリミティブ。区分の値は passthrough、比較は exact_match など（3.5節） |
|   | possible_values | list[str] | 決定表が参照する値（条文に出てくる値） |
|   | sources | dict[str, SourceRef] | プリミティブの引数名 → 値の取得元 |
|   | comparison_kind | str（任意） | 語彙登録時に人が付ける比較の種類の注釈 |
|   | approved_by | str または None | この fact の追加を承認した人。未承認なら None（D14） |
| SourceRef | from（Python では from_） | str | 取得元の種類：action_request（行動要求）／ master（マスタ照会）／ literal（固定値）。from は Python の予約語なのでフィールド名は from_、JSON では from |
|   | path または value | str | from が action_request・master なら path、literal なら value |
| AtomicFactLayer | covered | bool | 必要な比較を既存の一致プリミティブで賄えるか |
|   | new_primitives | list[str] | 新しく必要なプリミティブ（covered=False のとき） |
| IntermediateChange | table_id | str | 比べた既存の中間判定の決定表 |
|   | relation | ColumnSetRelationKind | 列集合の関係（判断の根拠） |
|   | action | reuse_value / add_row / add_column | 直し方。既存の値を使う／行を足す／列を足す |
|   | new_output_values | dict[str, list[ValueAddition]] | 中間判定に足す出力値。出力名 → 足す値と承認者の一覧（D29・D37）。その表がまだ出していない値だけを書く（D36）。例：identity_level → [中（氏名なし・2要素一致）] |
|   | new_input_columns | list[str] | action が add_column のとき足す入力列（D19） |
| FinalLayer | compared_table_id | str または None | 比べた既存の最終判定の決定表。比べる相手がない場合と no_change の場合は None |
|   | relation | ColumnSetRelationKind または None | 列集合の関係（判断の根拠） |
|   | action | add_rows / new_table / no_change | 既存の最終判定に行を足すか、新しい最終判定を作るか、最終判定は変えないか（no_change は D30 で追加） |
| LevelDetermination | atomic_fact | AtomicFactLayer | ① 事実の層 |
|   | intermediate | list[IntermediateChange] | ② 中間判定の層。空なら中間判定は使わない、または既存をそのまま使う |
|   | final | FinalLayer | ③ 最終判定の層 |
|   | outcome（導出） | level1_new_table / level2_row_addition / level2_intermediate_only | final.action が new_table なら level1_new_table、add_rows なら level2_row_addition、no_change なら level2_intermediate_only。値は持たず計算する |

ColumnSetRelationKind（exact_match / proper_subset / intersecting_non_subset）は v1 と同じ値を使う。ただし一致プリミティブの exact_match とは別物である。

### 3.4 変更しない型

次の型は v1（コミット 9e66776）の形を引き継ぐ。ただし CompoundFactApplication は手で書く欄を印1つに減らし（D31）、NewDecisionTableDraft の depends_on の2つを導出に変え（D38）、knowledge_sources の書き方を決めた（D22）。本書だけで読めるよう、フィールドの定義を再掲する。

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
|   | depends_on_decisions（導出） | list[str] | この判定が使う他の判定（中間判定）。input_columns のドット参照から導出し、手で書かない（D38） |
|   | depends_on_inputs（導出） | list[str] | この判定が使う入力事実の名前。input_columns のうちドットを含まない列から導出し、手で書かない（D38） |
|   | input_columns | list[str] | 決定表の入力列。「判定名.出力名」の形は他の判定の出力を指す |
|   | rule_rows | list[RuleRowDraft] | 決定表の行の一覧 |
|   | knowledge_sources | list[str] | 来歴として紐付ける知識源。計算には使わない。条文または出典付きの context に根拠があるものだけを書く（D22） |
| RuleRowDraft | row_id | str | 行 ID。DMN の rule id と登録簿の row_id になる |
|   | decision_table_id | str | 所属する決定表。中間判定の表への行追加にも使う |
|   | position | int | FIRST での評価順（1始まり）。小さいほど先に評価される |
|   | conditions | dict[str, str] | 列名 → 条件値。「-」は何でもよい（ワイルドカード） |
|   | outputs | dict[str, str] | 出力名 → 値（例：decision=禁止、reason_code=…） |
| CompoundFactApplication | rejected_in_primitive | bool | 複合条件（AND）を一致モジュールで組もうとして拒否されたか。True なら配線ジェネレータが停止する（C3）。v1.2 までの option_taken = rejected_compound_in_primitive に当たる（D31） |
|   | rationale | str | 複合条件の表し方についての説明 |
|   | option_taken（導出） | dict[str, new_table_row / existing_table_row_addition / new_intermediate_table] | 決定表ごとに導出する（D31）。new_decision_tables の表は、他の判定から参照されれば new_intermediate_table、それ以外は new_table_row。rule_row_additions だけがある既存の表は existing_table_row_addition |
| Provenance | origin | str | 由来の種類。規程の条文なら regulation_clause |
|   | clause_ref | str | 根拠となる条文 |
|   | registered_by | str | 登録した人・仕組み |
|   | registered_at | str（ISO8601） | 登録日時（UTC） |

ReviewGates には次の2つのゲートを足す（D10・D20）。should_review はすべてのゲートの OR である。

| ゲート | 立つ条件 | 理由 |
| --- | --- | --- |
| confidence_below_threshold | 軸1または軸2の confidence < 0.7 | v1 のまま |
| quote_unverified | 軸1または軸2の quote_verified=False | v1 のまま |
| vocab_flagged | 軸1または軸2の vocab_flagged=True | v1 のまま |
| new_action_unapproved | ActionRef.is_new=True かつ approved_by=None | v1 のまま |
| new_fact_unapproved【追加】 | approved_by が None の FactDef（new_facts）、ValueAddition（value_additions）、または中間判定の出力値（new_output_values）が1つでもある（D14・D37） | 語彙（fact・値・中間判定の出力値）を増やす判断は人が行う |
| new_primitive_required【追加】 | atomic_fact.covered = False（D20） | 新しいプリミティブが必要な条文は③a の範囲外 |

### 3.5 一致プリミティブの使い分け

| 用途 | primitive | sources の引数 | possible_values | 例 |
| --- | --- | --- | --- | --- |
| 区分の値をそのまま渡す | passthrough | value | 決定表が参照する区分の値 | operator_role = システム開発担当 |
| 2つの値が同じかを知る | exact_match | claimed, registered | 一致 / 不一致 / 未提供 | 電話番号の照合 |
| 表記ゆれを吸収して比べる | fuzzy_match_name | claimed, registered | 一致 / 不一致 / 未提供 | 氏名の照合 |
| 日付を比べる | date_match | claimed, registered | 一致 / 不一致 / 未提供 | 生年月日の照合 |
| 分類の階層に含まれるか | ontology_class_match | subject, target_class | 含む / 含まない | 検査結果が病歴に含まれるか |
| （初期登録分のみ）個人の特定可否 | identifiability_classifier | data_category, care_setting | 可能 / 不可能_院内 / 不可能_公開 | あおいの情報分類判定の identifiable（D23） |

この表は、プリミティブ名 → 引数名の定義（PRIMITIVE_ARGS）としてコードに持たせ、C4 で使う。表にないプリミティブが出てきたら、C4 で停止する（新しいプリミティブの追加は③a の範囲外）。

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
    approved_by: str | None = None                       # D14

@dataclass
class ValueAddition:                                     # D14
    value: str
    approved_by: str | None = None

@dataclass
class FactUsage:
    new_facts: list[FactDef] = field(default_factory=list)
    referenced_facts: list[str] = field(default_factory=list)   # D21：漏れなく列挙
    value_additions: dict[str, list[ValueAddition]] = field(default_factory=dict)

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
    new_output_values: dict[str, list[ValueAddition]] = field(default_factory=dict)  # D29・D37
    new_input_columns: list[str] = field(default_factory=list)  # D19

@dataclass
class FinalLayer:
    compared_table_id: str | None
    relation: ColumnSetRelationKind | None
    action: Literal["add_rows", "new_table", "no_change"]      # D30

@dataclass
class LevelDetermination:
    atomic_fact: AtomicFactLayer
    intermediate: list[IntermediateChange]
    final: FinalLayer

    @property
    def outcome(self) -> LevelOutcome:
        if self.final.action == "new_table":
            return "level1_new_table"
        if self.final.action == "add_rows":
            return "level2_row_addition"
        return "level2_intermediate_only"                    # D30

@dataclass
class CompoundFactApplication:                           # D31
    rejected_in_primitive: bool = False
    rationale: str = ""
    # option_taken は決定表ごとに導出する（プロパティ）
```

### 3.7 IR の中で守る整合条件

IR の各欄は互いに矛盾してはならない。次の8条件は配線ジェネレータがクロス検証（5章 C9）で確かめる。条件7・8は v1.3 で足した。

1. final.action が new_table なら、new_decision_tables に target.entry_decision_id の決定表がある
1. final.action が add_rows なら、compared_table_id は target.entry_decision_id の決定表であり、rule_row_additions にその表の行がある
1. intermediate の action が add_row なら、rule_row_additions に table_id の表の行がある。new_output_values の出力名はその表の output_columns にある。さらに、追加した行の出力値のうちその表がまだ出していない値の集合が、new_output_values と出力名ごとに一致する（足りなくても余っても不可。D29・D36）
1. intermediate の table_id と final の compared_table_id は、登録簿の層1に実在する
1. atomic_fact.covered が False なら、new_primitives が空でない（新しいプリミティブの追加は③a の範囲外なので、new_primitive_required ゲートで人間レビューに回す）
1. intermediate の action が add_column なら、new_input_columns が空でなく、追加した列が rule_row_additions のその表の行の条件に現れる（D19）
1. final.action が no_change なら、intermediate に add_row か add_column が1つ以上あり、compared_table_id は None で、target.entry_decision_id は層1に実在する最終判定（entry_points が空でない）である（D30）
1. final.action が add_rows か no_change なら、IR の actor と action.normalized の組が、target.entry_decision_id の判定の entry_points にある（既存のルートに足す条文が、別の行為を名乗らないようにする）

### 3.8 例：特権アカウント条文の IR v2

ゴールデンパスの条文（ipa_secguide_v4_6_7_1「システム開発を担当する者が本番環境において特権/管理者権限で操作を行うことを禁止する。」）を IR v2 で書くと次のようになる。分類結果・決定表の行は v1 と同じなので省略する。knowledge_sources は ks_action_request だけにする（D22）。

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
        "sources": { "value": { "from": "action_request", "path": "operator.role" } },
        "approved_by": null },
      { "variable": "target_environment", "primitive": "passthrough",
        "possible_values": ["本番環境"],
        "sources": { "value": { "from": "action_request", "path": "target.environment" } },
        "approved_by": null },
      { "variable": "requested_privilege", "primitive": "passthrough",
        "possible_values": ["特権"],
        "sources": { "value": { "from": "action_request", "path": "requested_privilege" } },
        "approved_by": null }
    ],
    "referenced_facts": [], "value_additions": {}
  },
  "level_determination": {
    "atomic_fact": { "covered": true, "new_primitives": [] },
    "intermediate": [],
    "final": { "compared_table_id": null, "relation": null, "action": "new_table" }
  },
  "compound_fact_application": { "rejected_in_primitive": false, "rationale": "…" }
}
```

option_taken は書かない。この IR では decision_privileged_operation の表が new_table_row と導出される（D31）。v1 との違いは3点ある。fact が IR の中に入ったこと、primitive が実態どおり passthrough になったこと、possible_values から条文にない値（運用担当・検証環境など）が消えたことである。この IR では new_action_unapproved と new_fact_unapproved の2つのゲートが立ち、人間レビューに回る。Kazuhiro さんが承認したら、action と各 FactDef の approved_by に名前を入れると、ゲートが下りる（D14）。

比較のため、中間判定を使う第14条（口頭伝達の制限）のレベル判定を IR v2 で書くと次のようになる。

| 層 | 値 | 読み方 |
| --- | --- | --- |
| ① 事実 | covered = True | 既存のプリミティブで賄える |
| ② 中間判定 | table_id = dt_identity、relation = proper_subset、action = add_row、new_output_values = {identity_level: [中（氏名なし・2要素一致）]} | 本人確認判定に行を1つ足し、新しい確認レベルの値を加える |
| ③ 最終判定 | compared_table_id = dt_transmission、relation = proper_subset、action = new_table | 列の上では送信可否判定に行を足せるが、行為（口頭伝達）が違うので新しい表を作る |
| 結論 | outcome = level1_new_table（導出） | — |

③で「関係は真部分集合だが、結論は新しい表」となっている。v2 では関係（根拠）と action（結論）を別の欄に持つので、この判断を型の上で表せる。

第14条から口頭伝達の部分を除き、「氏名と生年月日が一致すれば、電話番号が一致しなくても確認レベルを中とする」だけを定める条文なら、③は action = no_change になる（D30）。target.entry_decision_id には送信可否判定（decision_transmission）を入れ、actor・action は その entry_points の「あおい × 外部送信」と揃える。ルートは D16 の作り直しで出る。

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
    registry: Registry,   # 既存の登録簿（層1・層2・層3）と registry/dmn/ の DMN ファイル
) -> WiringBundle         # dmn_xml, bindings, routing_table, registry（更新後の全体）
```

| 項目 | 内容 |
| --- | --- |
| 永続の登録簿 | registry/registry.json（層1・層2・層3・review）。DMN ファイルは registry/dmn/<dmn_model>.dmn（D15） |
| 出力 | out/ 以下。返す登録簿は、入力に今回の IR を反映した全体 |
| 登録簿への反映 | 出力を registry/ に反映する操作（promotion）は、人間レビューを経た別操作とする。未実装（7章 U8） |

### 4.1 required_facts の求め方

ルーティング表とバインディング設定は、最終判定を評価するのに実際に集める必要がある事実（required_facts）を基にする。中間判定を使う場合、最終判定の入力列には「本人確認判定.identity_level」のような他の判定の出力が入る。そのため、入力列をそのまま写すのではなく、依存関係をたどって求める。

1. target.entry_decision_id の決定表の input_columns から始める。この IR が add_column で足す列があれば、その表の入力列に加えておく（D27）
1. 「判定名.出力名」の形の列は、その判定（IR の new_decision_tables、または登録簿の層1）の input_columns に置き換える
1. 2を、ドットを含む列がなくなるまで繰り返す
1. 残った列の集合が required_facts である。すべて層3（fact 台帳）か facts.new_facts にあること
第14条の例では、口頭伝達可否判定の入力列「本人確認判定.identity_level」が本人確認判定の入力列に置き換わり、required_facts は {name_match, birth_match, phone_match} になる。

ドット参照が循環していたり、参照先の判定に入力列がなかったりした場合は、SchemaWiringError で停止する。どちらも required_facts が黙って小さくなる経路だからである。手順2で置き換えるときも、参照先の表に add_column で足す列があれば加える。

#### 影響を受ける最終判定の洗い出し（D16）

IR が既存の表に行・列・出力値を足した場合、作り直すルートは entry_decision_id の1本では足りない。次の手順で、影響を受けるすべての最終判定を洗い出し、それぞれについて上の手順で required_facts を求める。

1. IR が実際に変更した既存の表の判定を起点にする。rule_row_additions の表、intermediate の action が add_row・add_column の表、new_output_values を足した表、final.action が add_rows の表である。reuse_value の表と、new_table の compared_table_id は、何も足さないので起点にしない（D24）
1. 層1の depends_on_decisions を逆にたどり、起点の判定を（間接的にも）使っている判定をすべて集める
1. 集めた判定のうち、ルーティング表に載っている判定（最終判定）と、IR の entry_decision_id が、作り直す対象である
たとえば第14条で本人確認判定に行を足すと、送信可否判定と口頭伝達可否判定の2本が対象になる。D17 により、これらは同じ DMN モデルに入っている。final.action が no_change の IR では、IR 自身のルートも既存の最終判定のルートなので、出力するルートはすべてこの洗い出しから出る。ルーティング表には、IR 自身のルート（entry_decision_id の行）を先頭に置き、同じ actor・action・decision_id の行は1つにまとめる。

### 4.2 DMN XML

| 出力する項目 | IR v2 のどこから |
| --- | --- |
| definitions の id・name | target.dmn_model（name も dmn_model にする。判定名は decision の name に入る） |
| definitions の namespace | 新しく作るモデルは http://gijinka.example.com/dmn/<dmn_model>。既存のモデルは元の namespace を変えない（D39） |
| decision・decisionTable・rule | new_decision_tables（新規の表）。rule_row_additions は既存モデルへの行追加として出力する |
| inputData | new_decision_tables の input_columns のうちドットを含まない列 |
| knowledgeSource・authorityRequirement | new_decision_tables の knowledge_sources |

既存のモデルに行を足す場合（レベル2、または中間判定への行追加）は、registry/dmn/<dmn_model>.dmn を読み込み、position の位置に rule を挿入して出力する（D15）。挿入先の表の hit_policy が FIRST でなければ停止する（D35）。列を足す場合（add_column）は、input と inputData を足し、既存の全 rule のその列を「-」にする（D19）。中間判定の出力値を足すだけなら、DMN XML の構造は変わらない。

ドット参照の requiredDecision の href には、参照先の decision の id を入れる。FEEL 式（inputExpression の text）には decision の name を使う。初回実装で href に name を入れていた誤りは修正済みである。

### 4.3 バインディング設定

バインディング設定は DMN モデルごとに1つ作り、{"dmn_model": …, "facts": [...]} の形にする。D16 で複数のルートを作り直す場合は、それらが使う fact をまとめて出力する。

| 出力する項目 | IR v2 のどこから |
| --- | --- |
| 対象とする fact | 作り直すすべてのルートの required_facts の和集合 |
| 各 fact の定義 | facts.new_facts にあればそれを、なければ層3（fact 台帳）の定義を使う。value_additions があれば possible_values に加える |
| dmn_model | target.dmn_model |
| sources_undefined | 層3の sources が空の fact（初期登録で取得元が分からなかったもの）に true を付ける（D33）。それ以外の fact には付けない |

### 4.4 ルーティング表

出力するのは、entry_decision_id の行と、D16 で洗い出した最終判定の行である。既存の最終判定の行は、層1の entry_points から actor・action を引いて作り直す（D16）。

| 項目 | IR v2 のどこから | 例（特権アカウント） |
| --- | --- | --- |
| actor・action | actor、action.normalized | あおい、特権操作 |
| dmn_model | target.dmn_model | privileged_operation |
| decision_id | target.entry_decision_id | decision_privileged_operation |
| required_facts | 4.1節の手順で求める | operator_role, target_environment, requested_privilege |

### 4.5 Scope/Decision 登録簿

| 層 | 内容 | v1 からの変更 |
| --- | --- | --- |
| 層1（decision_tables） | 決定表ごとのメタ情報。decision_table_id・decision_id・decision_name・dmn_model・entry_points（最終判定を呼ぶ actor・action の組。中間判定は空）・hit_policy・input_columns・output_columns・depends_on_decisions・depends_on_inputs・knowledge_sources・provenance・history。decision_table_id・decision_id・decision_name のどれでも引ける。depends_on の2つは input_columns から導出した値を入れる（D38） | dmn_model（D8）、output_columns（D12）、entry_points（D16）、history（D40）を追加 |
| 層2（rule_rows） | ルール行1本ずつの台帳。row_id・position・conditions・decision・reason_code・provenance・history。conditions・outputs のセルは DMN の FEEL 表記のまま持つ（D28） | セルの表記を規定（D28） |
| 層3（fact_registry） | fact 台帳。FactDef の各フィールド（approved_by を含む）に加え、defined_by（最初に定義した条文）・used_by（使っている決定表）・provenance・history | 新設（D2） |
| review | 条文単位のレビュー状態。human_review_status・should_review・gate_reasons | gate_reasons に加え、ゲート名の一覧（gates）も持たせる |

v1 の gate_reasons は日本語の説明文だけで、集計に向かなかった。v2 ではゲート名（例：new_fact_unapproved）の一覧も併せて出力する。

出力の値（例：確認レベルの「高」「中」）は層1に書かず、層2の行の outputs から集める（D12）。possible_values を「表に出てくる値だけ」にした D4 と同じ考え方で、同じ情報を2か所に持たない。

層2のセルは DMN の表記（"高"、"中","未" など）のまま持ち、IR の行は素の値（高）を持つ。値を比べるときは、FEEL のセルを値の集合に変換する1つの関数を必ず通す（D28）。depends_on_inputs には inputData の id ではなく名前（input_columns と同じ語）を入れる（D35）。

#### 初期登録（bootstrap）

既存の DMN を登録簿に取り込む操作を初期登録と呼び、provenance.origin = bootstrap とする。あおいの送信可否判定（本人確認判定・情報分類判定・送信可否判定）を最初の対象とした。

| 項目 | 扱い |
| --- | --- |
| 情報源 | DMN XML と、出典（ファイルと行）を明示した設計資料だけ（D32）。あおいの entry_points は sequence_diagram_explained.md を出典とする |
| 層3の primitive | 本書3.5節の表（あおいの fact を例として名指ししている）を出典とする |
| 層3の sources | 取得元が分からなければ空にする。バインディング設定に sources_undefined を付ける（D33） |
| 元の DMN の不足・矛盾 | reference/ の元ファイルは書き換えない。registry/ の側で修正し、history に内容・理由・承認者を残す（D34） |
| 修正後の検証 | 登録簿に C5・C11 を適用し、違反があれば止める（D34） |

## 5. クロス検証

配線ジェネレータは次の項目を確かめ、1つでも失敗すれば SchemaWiringError で停止する。C1〜C3 は v1 から引き継ぎ、C4〜C10 を追加し（D9）、v1.2 で C11 を足した（D13）。v1.3 で C6 の基準と C9 の条件を改めた（D25・D26・D29・D30）。

検証は2段階で行う。IR と登録簿だけで判定できるものは4点セットを作る前に（C3 → C9 → C4 → C6 → C10 → C11 → C2）、成果物を突き合わせるものは作った後に（C1 → C5 → C7 → C8）確かめる。C9 が破れていると required_facts の算出自体が始められないためである。C6 は D25 により IR だけで判定できるので、生成前の位置で1回行う（D26）。

| # | 検証 | 内容 | 区分 |
| --- | --- | --- | --- |
| C1 | fact の不足 | required_facts（作り直すすべてのルートの和集合）のすべてが facts.new_facts か層3にある。IR が言及しない中間判定の fact の不足は、C1 だけが捕まえる（D26） | v1 から |
| C2 | fact の余剰 | facts.new_facts のすべてが、どこかの決定表で使われている | v1 から |
| C3 | 複合 fact の拒否 | compound_fact_application.rejected_in_primitive が False である（D31） | v1 から |
| C4 | sources と引数 | sources のキーが、primitive の引数名と一致する（3.5節）。表にないプリミティブ、および new_facts での identifiability_classifier は停止（D23） | 追加 |
| C5 | 決定表の値 | 新規の表の行と rule_row_additions の各条件の値（「-」以外、ドット参照の列を除く）が、その fact の possible_values（value_additions を含む）にある | 追加 |
| C6 | 参照 fact の実在と網羅 | facts.referenced_facts と value_additions のキーが層3に実在する。D25 の手順で求めた「IR が使う既存 fact」と referenced_facts が一致する（D21・D25） | 追加（v1.2 で網羅を追加、v1.3 で基準を変更） |
| C7 | 判定先の実在 | target.entry_decision_id が DMN XML（add_rows の場合は挿入後の既存モデル）と層1に実在する（D15） | 追加（v1.2 で add_rows に対応） |
| C8 | 集める事実の一致 | ルーティング表の required_facts とバインディング設定の fact の集合が一致する | 追加 |
| C9 | レベル判定の整合 | 3.7節の8条件を満たす | 追加（v1.2 で条件6、v1.3 で条件7・8を追加、v1.4 で条件3を双方向に） |
| C10 | ドット参照の解決 | 「判定名.出力名」の判定が実在し、その出力名を持つ。出力名は IR の新規表の行、または層1の output_columns で確かめる（D12） | 追加 |
| C11 | ドット参照の値 | 「判定名.出力名」の列の条件値（「-」以外）が、参照先の判定の出力値にある。出力値は層2の行・IR の rule_row_additions・intermediate の new_output_values（同じ出力名の分だけ）から集める（D13・D29）。値は D28 の関数で揃えて比べる | v1.2 で追加 |

各検証には、わざと違反させた IR で SchemaWiringError になることを確かめる失敗テストを付け、リポジトリにコミットする。ただし C8 は、ルーティング表とバインディング設定を同じ required_facts から導くので、IR の側から違反を作れない。C8 には両者が一致することを確かめるテストを置き、どちらかの導出だけが変わったときに気付けるようにする。

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

28件は IPA 情報セキュリティ関連規程と厚生労働省モデル就業規則が出典で、中間判定を使う条文やレベル2の条文が含まれない可能性がある。その場合は、既存設計の実例である第14条を加える。第14条には、あおいの送信可否判定（本人確認判定・情報分類判定・送信可否判定）が既存の決定表として登録簿にあることが前提になる。この初期登録と既存モデルへの行の挿入は、コミット 71d3424 で実装した（4.5節）。中間判定だけを直す条文（final.action = no_change）が見つかれば、それも1件含める。

### 6.2 作り方

1. IR に入れる情報は、条文本文と、出典付きで明示的に付与した context に限る（1.4節）
1. 1件ずつ JSON で作り、testdata/ir_gold/ に置く。適用順を manifest.json に書く（D43）
1. 初期登録した登録簿から始め、manifest の順に配線ジェネレータに通す。各 IR が C1〜C11 をすべて通過することを確かめる。出力された登録簿は次の IR の入力にし、registry/ には反映しない（D43）
1. DMN 評価のシナリオ（入力の事実と期待する判定）を付け、自作エンジンで期待通りになることを確かめる。シナリオには、possible_values にない値が既定の行（ワイルドカード）に落ちる場合を少なくとも1件含め、その期待値を明記する（7章 U2 の挙動を見えるようにするため）
1. Kazuhiro さんがレビューし、承認したものを正解とする
書き方の注意を2つ挙げる。referenced_facts は条文の語ではなく、書いた行と表から決める。条文が「電話番号が一致しなくても」と言及していても、行の条件が「-」なら入れない（D41）。new_output_values には、その表がまだ出していない値だけを書く（D36）。

#### 正解 IR のファイル

| キー | 内容 |
| --- | --- |
| gold_id | 正解 IR の ID（ファイル名と同じ） |
| source | 条文の出典（文書名・条番号）と、testdata の clause_ref |
| context | IR に入れた context と、その出典。なければ空 |
| ir | ClauseIR の JSON。軸1・軸2は testdata の正解ラベルを使う |
| evidence | 根拠の対応表。new_facts の各値、決定表の各行、action.surface について、根拠になる条文の語句（逐語）を書く。レビューで 1.4節の原則を確かめるため |
| scenarios | DMN 評価のシナリオ。入力の事実・期待する判定・種類（追加した行に当たる／既定の行に落ちる など） |
| review | status（draft／approved／rejected）、reviewed_by、notes。approved になったものだけを正解として使う |

型で表しきれない条文が見つかった場合は、無理に表さず、どこが表せないかを記録して報告する。正解 IR を作る作業は、IR v2 の型の検証も兼ねている（6章冒頭）。

### 6.3 前段での使い方

前段は要素ごとに自動化する（Action 正規化 → Scope 属性の抽出 → 行の構成 → レベル判定）。正解 IR は、要素ごとに正解と比べられる形で使う。たとえば Action 正規化なら action.normalized と is_new、Scope 属性の抽出なら facts の variable と possible_values を比べる。

## 7. 未決事項

本書で決めきれなかった論点を挙げる。U1・U2 は前段の評価に影響するので、正解 IR を作る段階で方針を決めたい。

| # | 論点 | 内容 | 当面の扱い |
| --- | --- | --- | --- |
| U1 | 値の語彙の管理 | passthrough では「開発担当」と「システム開発担当」のような表記ゆれが吸収されない | fact 台帳の possible_values を統制語彙とし、新しい値は new_fact_unapproved で人が承認する。正規化の仕組みは別途 |
| U2 | 既定の行が「許可」になる | 最後のワイルドカード行が許可なので、知らない値や未登録の値もすべて許可に落ちる（安全側に倒れない） | 既定の行を許可にするか、確認を求める出力にするかを条文の性質ごとに決める必要がある |
| U3 | レビュー結果の保存 | human_review_status を approved／rejected に変えた結果や、approved_by の承認を、誰がいつ行ったかとして記録する仕組みがない | 当面は IR（正解 IR の JSON）の approved_by に書く。登録簿の history への記録は別途 |
| U4 | 実行時の仕組み | 一致プリミティブ・オーケストレータが未実装で、配線が実行時に動くかは未検証。Kogito 実機での評価も未実施 | 前段と並行するかは別途判断 |
| U5 | 複数の最終判定 | 1つの条文が複数の Action に関わる場合、最終判定が複数になる | v2 は1条文1最終判定とする。必要になった時点で拡張 |
| U6 | エスカレーション系の条文 | 承認・期限を伴う条文は単発の決定表で表しきれず、③b の領域に入る | ③a は ハード制約 × 宣言的 に限る |
| U7 | fact を変えたときの影響 | fact 台帳の定義を変えると、その fact を使う全ての決定表に影響する | used_by で影響範囲を出せるようにしておく。再検証の手順は別途 |
| U8 | 登録簿への反映（promotion） | 配線ジェネレータの出力（out/）を、人間レビューを経て registry/ に反映する操作がない | 当面は手で反映する。正解 IR の作成で必要になれば実装する |
| U9 | DMN のモデル間参照 | D17 で中間判定と最終判定を同じモデルに入れることにしたが、モデルが大きくなったときに分けられない | 必要になった時点で DMN の import を検討する |
| U10 | 業務知識の混入 | 前段を自動化したとき、LLM が条文にない値を possible_values に提案するのを止める仕組みがない | 当面は new_fact_unapproved ゲート（人が見る）だけが歯止め。前段の設計で扱う |
| U11 | あおいの fact の取得元 | 初期登録した7件の fact の sources（どのマスタのどの項目と比べるか）が分からない。参考実装のテストには patient_record という取得元が出てくるが、本書の from の語彙（action_request／master／literal）にない | 空のまま sources_undefined を付ける（D33）。一致プリミティブの実装（U4）に入るときに、あおいの fact の取得元の表を作って埋める。patient_record は master に当たると見込む |

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

## 付録B　v1.1 から v1.2 への変更点

v1.2 は、IR v2 の初回実装（コミット 10fc38b）の報告（第16回 HANDOFF）を受けて改訂した。報告の7節にあった論点12件と、1節にあった実装上の判断10件を、次のように扱った。

### B.1 論点12件の扱い

| # | 論点 | 扱い |
| --- | --- | --- |
| 1 | 層1に出力列の一覧がない | D12（output_columns を追加） |
| 2 | 識別子が3つ（decision id・decision table id・decision name）ある | 層1をどれでも引けるようにする（4.5節）。ドット参照の href は id、FEEL は name（4.2節） |
| 3 | new_fact_unapproved を解除する手段がない | D14（approved_by を追加） |
| 4 | D4 と U2 の相互作用 | 正解 IR のシナリオに既定の行に落ちる場合を含める（6.2節）。U2 は未決のまま |
| 5 | C5 の中間判定版がない | D13（C11 を追加） |
| 6 | add_column で足す列を指す欄がない | D19（new_input_columns を追加） |
| 7 | covered=False に対応するゲートがない | D20（new_primitive_required を追加） |
| 8 | referenced_facts の意味が未定義 | D21（漏れなく列挙する欄とし、C6 で確かめる） |
| 9 | バインディング設定のトップレベル形 | 初回実装の判断（{dmn_model, facts}）を採用（4.3節） |
| 10 | 新規の表の行を表す選択肢がない | D18（new_table_row を追加） |
| 11 | depends_on の単数・複数の不一致 | depends_on_decisions・depends_on_inputs の2つとする（4.5節） |
| 12 | ks_privileged_account_registry の残置 | D22（削除） |

### B.2 実装上の判断10件の取り込み

| # | 判断 | 本書での扱い |
| --- | --- | --- |
| 1 | クロス検証を生成前と生成後の2段階にした | 採用（5章） |
| 2 | バインディング設定を {dmn_model, facts} にした | 採用（4.3節） |
| 3 | namespace を http://gijinka.example.com/dmn/<dmn_model> にした | 採用（4.2節） |
| 4 | definitions の name を dmn_model にした | 採用（4.2節） |
| 5 | depends_on_decisions・depends_on_inputs の2つを維持 | 採用（4.5節） |
| 6 | C10 の出力名を層2から集めた | D12 の output_columns に置き換える |
| 7 | C4 で表にないプリミティブを止めた | 採用（3.5節・5章） |
| 8 | PRIMITIVE_ARGS から identifiability_classifier を外した | 初期登録分に限って使えるようにする（D23） |
| 9 | C5 を新規表の行と rule_row_additions の両方に適用し、ドット参照は対象外にした | 採用。ドット参照は C11 で確かめる（5章） |
| 10 | SourceRef のフィールド名を from_ にした | 採用（3.3節） |

## 付録C　v1.2 から v1.3 への変更点

v1.3 は、v1.2 の実装（コミット 71d3424）の報告（第18回 HANDOFF）を受けて改訂した。報告の1節にあった設計書からの逸脱6件と、7節にあった懸念8件を、次のように扱った。

### C.1 逸脱6件の扱い

| # | 逸脱 | 扱い |
| --- | --- | --- |
| 1 | reuse_value の表と new_table の compared_table_id を D16 の起点にしない | 採用し、4.1節の書き方を直す（D24） |
| 2 | C6 を実在と網羅に分け、網羅を required_facts の導出の後に置いた | D25 で網羅の基準を IR の行に変えたので、分ける必要がなくなる。C6 は生成前に1回（D26） |
| 3 | C6 の網羅の基準を IR 自身のルートの required_facts にした | 方向は採用。ただしレベル2では最終判定の全 fact の宣言を強いる（第902条のテストで7件を列挙していた）ので、基準を IR が書いた行と表に改める（D25） |
| 4 | C1 は通しで流すと C6 が先に止めるので、失敗テストは関数を直接呼ぶ | 採用。D25 の後は、IR が言及しない中間判定の fact の不足を捕まえる役割が C1 に戻る（D26） |
| 5 | C11 で FEEL の表記を外して素の値に揃えた | 採用し、層2の表記と比べ方を規則として明記する（D28） |
| 6 | required_facts の算出で add_column の列を加えた | 採用し、4.1節の手順に書く（D27） |

### C.2 懸念8件の扱い

| # | 懸念 | 扱い |
| --- | --- | --- |
| 1 | 層3の sources を DMN から起こせない | 空のまま sources_undefined を付ける（D33）。取得元の表は U4 のときに作る（U11） |
| 2 | entry_points を設計資料から出典付きで入れた | 認める（D32） |
| 3 | new_output_values に出力名がない | dict（出力名 → 値）にする（D29）。C9 条件3も直す |
| 4 | FinalLayer に「最終判定は変えない」がない | no_change を足す（D30）。C9 に条件7・8を足す |
| 5 | option_taken が IR に1つしかない | 手で書かず、決定表ごとに導出する。手で書くのは拒否の印だけ（D31） |
| 6 | hit_policy が FIRST 固定で IR に欄がない | IR に欄は持たせない。挿入先が FIRST でなければ停止する（D35） |
| 7 | depends_on_inputs は id か名前か | 名前とする（D35） |
| 8 | 送信可否判定の「未」 | registry/ の側で削除し、history に承認者（Kazuhiro）を残す。初期登録に C5・C11 を適用する（D34） |

## 付録D　v1.3 から v1.4 への変更点

v1.4 は、v1.3 の実装（コミット e6e3ee6）の報告（第20回 HANDOFF）を受けて改訂した。報告の1節にあった設計書からの逸脱5件と、7節にあった懸念8件を、次のように扱った。あわせて、正解 IR の置き方と評価の手順を決めた（D43、6.2節）。

### D.1 逸脱5件の扱い

| # | 逸脱 | 扱い |
| --- | --- | --- |
| 1 | モデル全体の修正の history を、層1の3件すべてに残した | 採用（D40） |
| 2 | 層1に history を新設した | 採用し、4.5節に書く（D40） |
| 3 | DMN を再直列化した（&quot; → "、未使用の xmlns:feel の欠落） | 採用。feel: の接頭辞は元のファイルでも使われておらず、XML として等価 |
| 4 | inputData と一緒に DMNDI の図形も削除した | 採用。残すと存在しない要素を指す参照になる |
| 5 | 登録簿への C5・C11 を配線ジェネレータ側に置いた | 採用。比べ方を1か所に集める D28 と同じ考え方 |

### D.2 懸念8件の扱い

| # | 懸念 | 扱い |
| --- | --- | --- |
| 1 | new_output_values に既にある値を書くかの規則がない | まだ出していない値だけを書き、C9 条件3を双方向の一致にする（D36）。新しい出力値も承認の対象にする（D37） |
| 2 | 条文の語と referenced_facts がずれる | D25 の基準を保ち、6.2節に注記する（D41） |
| 3 | option_taken の導出が時点で変わる | IR を作った時点の判断の記録とする（D41） |
| 4 | depends_on_inputs の書き方が未定義 | depends_on の2つを導出にし、IR から外す（D38） |
| 5 | 層3の sources が空 | U11 のまま |
| 6 | あおいのモデルの namespace がモデル名と合わない | 既存モデルの namespace は変えない（D39） |
| 7 | 再直列化で元の DMN との差分が大きく見える | 現状どおり |
| 8 | C8 の失敗テストが作れない | 現状どおり（5章） |

このほか、元の DMN を使うテストが reference/ のない環境で飛ばされる点を、D42 で扱った。

