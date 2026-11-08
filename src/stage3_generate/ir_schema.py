"""③a 中間表現(IR)スキーマ v2。

②2軸分類・③a各判定の結果を1つの構造(`ClauseIR`)にまとめ、スキーマ配線ジェネレータが
4点セット(DMN XML／バインディング設定／ルーティング表／Scope・Decision登録簿)を
同時生成するための唯一の入力とする。

仕様: docs/design/aoi_doc2dmn_IRv2_design.md (IR v2 設計書 v1.1) の3章。
v1(コミット 9e66776)との差分は同書 付録A。

## v2 で変わった点(設計書2章 D1〜D11)

- **D1**: fact の定義を IR に入れる(`facts`)。v1 はバインディング設定を IR の外
  (`golden_path.py` の `BINDINGS` 定数)から渡していた
- **D3**: 区分の値は `passthrough`、2つの値の比較は `exact_match` 等の比較型
- **D5**: `sources` は `{from, path}` または `{from: literal, value}` の形
- **D6**: レベル判定を層ごとの型にし、`outcome` は `final.action` から導出する
  (v1 は3層すべてに同じ「列集合の関係」を当てはめ、`outcome` を別に書いていた)
- **D7**: 判定先(`target`)を IR に持たせる。v1 は DMN モデル名を引数で渡していた
- **D10**: `ReviewGates` に `new_fact_unapproved` を追加

## v1.2 で変わった点(設計書2章 D14・D18・D19・D20・D23)

- **D14**: fact の承認を記録する。`FactDef.approved_by` と `ValueAddition` を追加し、
  `new_fact_unapproved` は「承認者のいない新しい fact・値がある」ときだけ立つ
  (v1.1 は「新語彙あり」で立てていたため、新 fact を持つ条文では常に立ち目印にならなかった)
- **D18**: `CompoundFactOption` に `new_table_row` を追加
- **D19**: `IntermediateChange.new_input_columns` を追加
- **D20**: `ReviewGates` に `new_primitive_required` を追加
- **D23**: `identifiability_classifier` を一致プリミティブ表に戻す。ただし
  `new_facts` では使えない(あおいの初期登録分だけの境界事例。C4 で止める)

## v1.3 で変わった点(設計書2章 D29・D30・D31)

- **D29**: `IntermediateChange.new_output_values` を `dict[str, list[str]]`
  (出力名 -> 足す値)にした。中間判定は出力を複数持つため、値の一覧だけでは
  どの出力列の値かが分からず、列の取り違え(「未」を identity_level に足す類)を
  型で防げなかった
- **D30**: `FinalLayer.action` に `no_change`(最終判定は変えない)を足した。
  `outcome` は `level2_intermediate_only` を返す。中間判定だけを直す条文を、
  存在しない最終判定を作らずに表せるようにするため
- **D31**: `CompoundFactApplication` から `option_taken` を外し、手で書くのは
  `rejected_in_primitive`(一致モジュールで AND を組もうとして拒否された印)だけに
  した。`option_taken` は決定表ごとに `ClauseIR.option_taken` で導出する。
  1つの IR が新しい表の行と既存の表の行を両方作ることがあるため

## v1.4 で変わった点(設計書2章 D36・D37・D38)

- **D37**: `IntermediateChange.new_output_values` を
  `dict[str, list[ValueAddition]]`(出力名 -> 値と承認者)にした。中間判定の出力値を
  増やすと、それを参照するすべての最終判定の結果に影響しうるので、fact の値の追加
  (D14)と同じく人の承認を要る形にする。承認者のいない値があれば
  `new_fact_unapproved` が立つ
- **D36**: `new_output_values` には、その表が**まだ出していない**値だけを書く
  (C9条件3が双方向の一致を確かめる)。片方向だと、行が新しい値を黙って出しても
  止まらず、その値は参照する最終判定のどの行にも当たらない
- **D38**: `NewDecisionTableDraft` の `depends_on_decisions`・`depends_on_inputs` は
  手で書かず `input_columns` から導出する。どちらも機械的に決まるので、手で書かせると
  正解 IR と前段の出力の比較で揺れ、食い違っても検査されない

## v1.5 で変わった点(設計書2章 D45・D46)

- **D45**: `passthrough` の fact は予約語 **「その他」** を値として必ず持つ。「知っている
  状態だが、条文が挙げた値ではない」ことを表す。IR の `possible_values` には書かず、
  配線ジェネレータが層3とバインディング設定に自動で加える(`effective_possible_values`)。
  比較型(exact_match など)は値の集合が決まっているので持たない
- **D46**: 実行時、fact の値が `possible_values`(「その他」を含む)にない場合は DMN に
  渡さず人の確認に回す。バインディング設定に `on_unknown_value = escalate` を出す

## v1.6 で変わった点(設計書2章 D55・D58・D59)

- **D55**: `FactDef.other_value_note` を追加。passthrough の fact で予約語「その他」が
  指す状態(「許可を得ている」「請求していない」など)を書く。実行時に値を当てはめる側が、
  何を「その他」にしてよいかを知るための情報で、`new_facts` では必須(C4 で止める)
- **D58**: `ColumnSetRelationKind` に `disjoint`(列集合が1つも重ならない)を追加。
  「比べる相手がない」(compared_table_id = None)と「比べたが重ならない」を分ける
- **D59**: 比較型の fact の `possible_values` は、プリミティブの定義(`PRIMITIVE_VALUES`)
  から導く。D4(表に出てくる値だけ)は passthrough の fact にだけ当てる。表に出てくる値
  だけにすると、「未提供」のような正当な値が実行時にエスカレーションになる

## v1.7 で変わった点(設計書2章 D62)

- **D62**: `FactDef` を2つの型に分けた。`PassthroughFact`(区分の値の fact)は
  `possible_values` と `other_value_note`(必須)を持ち、`ComparisonFact`(比較の
  fact)はどちらも持たない(値の集合はプリミティブの定義から決まる。D59)。
  `FactDef` は2つの型の Union で、JSON では `primitive` が `passthrough` かどうかで
  型が決まる(`fact_def_from_json`)。v1.6 までは1つの型が両方の欄を持ち、「比較型では
  書いてはいけない欄」を型ではなく C4 でしか表せなかった。前段(条文 → IR)の自動化で
  LLM にこの型を見せるため、規則をスキーマに出す
- 層3とバインディング設定の出力は変えない。どちらの型でも、実際に使う値の一覧
  (「その他」や導出値を含む)を出す(`effective_values_of`・各型の `to_json`)

## v1.8 で変わった点(設計書2章 D67・D70)

- **D67**: v1.7 の実装で読み込みの時点に置いた歯止め(比較型の `possible_values`・
  `other_value_note` は `IRTypeError`、passthrough の `other_value_note` の欠けは
  None で通して C4 で止める)が、設計書の記述になった
- **D70**: 登録簿の層3を読むとき、比較型の `possible_values` がプリミティブの定義から
  導いた値と一致するかを確かめる(`origin="registry"`)。v1.7 までは読み捨てていたので、
  IR を経由して層3に入った値のずれに気付けなかった

## v1.9 で変わった点(前段設計書 v0.4 の4.5節)

`ReviewGates` の前段のゲートを入れ替えた。IR の型そのもの(`ActionRef` など)は
変わっていない。

- **廃止**: `action_mapping_unstable`(k=1 にしたため。P7)
- **追加**: `action_surface_ambiguous`(候補が2つ。P17)・`action_verb_mismatch`
  (中心の動詞が過去の語句に無い。P15)・`action_abstract_selected`(抽象的な段を
  選んだ。P15)・`action_name_rule`(名前・定義に様相の語がある。P16)
- **条件の変更**: `action_surface_unverified` は「様相を削った語句が条文にない」
  だけで立つ。様相はプログラムが削るので(P4)、様相が残っていること自体は
  ゲートにしない(評価の surface の3条件では引き続き数える)

## 引き継ぐ原則(設計書1.4)

- 4点セットの同時生成、複合 fact 禁止、FIRST と行の順序、来歴の表し方、Action 正規化
- IR に入れてよい情報は条文本文と出典付きの context だけ
- 人間レビューのゲートは1つのスコアに混ぜず、独立したゲートの OR として持つ
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Union

ColumnSetRelationKind = Literal[
    "exact_match",
    "proper_subset",
    "intersecting_non_subset",
    # D58: 列集合が1つも重ならない。「比べる相手がない」(None)と区別して記録する
    "disjoint",
]

LevelOutcome = Literal[
    "level1_new_table",
    "level2_row_addition",
    # D30: 中間判定だけを直し、最終判定は変えない
    "level2_intermediate_only",
]

# D31: 決定表ごとに導出する値。「拒否」は option_taken ではなく
# CompoundFactApplication.rejected_in_primitive で表す。
CompoundFactOption = Literal[
    "new_table_row",
    "existing_table_row_addition",
    "new_intermediate_table",
]

HumanReviewStatus = Literal["pending", "approved", "rejected"]

SourceKind = Literal["action_request", "master", "literal"]

IntermediateAction = Literal["reuse_value", "add_row", "add_column"]

FinalAction = Literal["add_rows", "new_table", "no_change"]

CONFIDENCE_THRESHOLD = 0.7

# 一致プリミティブ名 -> sources の引数名(設計書3.5)。
# プリミティブの実装そのものは③aの範囲外で、ここでは C4(sources のキーが引数名と
# 一致するか)の検証に使う定義としてのみ持つ。
PRIMITIVE_ARGS: dict[str, tuple[str, ...]] = {
    "passthrough": ("value",),
    "exact_match": ("claimed", "registered"),
    "fuzzy_match_name": ("claimed", "registered"),
    "date_match": ("claimed", "registered"),
    "ontology_class_match": ("subject", "target_class"),
    # D23: あおいの情報分類判定の初期登録分だけで使える境界事例。
    # 新しい条文の new_facts では C4 が止める(BOOTSTRAP_ONLY_PRIMITIVES)。
    "identifiability_classifier": ("data_category", "care_setting"),
}

# 比較型のプリミティブの possible_values(設計書3.5節の表)。D59: 比較型の値の集合は
# 条文ではなくプリミティブの定義で決まるので、possible_values はここから導く。
# D4(表に出てくる値だけ)は passthrough の fact にだけ当てる。
PRIMITIVE_VALUES: dict[str, tuple[str, ...]] = {
    "exact_match": ("一致", "不一致", "未提供"),
    "fuzzy_match_name": ("一致", "不一致", "未提供"),
    "date_match": ("一致", "不一致", "未提供"),
    "ontology_class_match": ("含む", "含まない"),
    "identifiability_classifier": ("可能", "不可能_院内", "不可能_公開"),
}


def derived_possible_values(primitive: str) -> Optional[tuple[str, ...]]:
    """この primitive の possible_values が定義から決まるなら、その値(D59)。

    passthrough は条文が挙げた値で決まる(D4)ので None を返す。
    """
    return PRIMITIVE_VALUES.get(primitive)


# new_facts では宣言できないプリミティブ(設計書2章 D23)。
BOOTSTRAP_ONLY_PRIMITIVES: frozenset[str] = frozenset({"identifiability_classifier"})

# 予約語「その他」(D45)。passthrough の fact が必ず持つ値で、「知っている状態だが、
# 条文が挙げた値ではない」ことを表す。条文が片側の値しか挙げない fact(例:
# 「請求した者」)で、反対側の状態を表す語が無かったために入れた。条文の情報を
# 足すのではなく、構造上の語である。
OTHER_VALUE = "その他"

# 「その他」が自動で付くプリミティブ(D45)。比較型は値の集合(一致/不一致/未提供)が
# 決まっているので付けない。
OTHER_VALUE_PRIMITIVES: frozenset[str] = frozenset({"passthrough"})

# 値が possible_values(「その他」を含む)に無いときの扱い(D46)。実行時は DMN に
# 渡さず人の確認に回す。実装は U4 だが、バインディング設定にはこの宣言を出す。
ON_UNKNOWN_VALUE = "escalate"


def has_other_value(primitive: str) -> bool:
    """この primitive の fact に予約語「その他」が付くか(D45)。"""
    return primitive in OTHER_VALUE_PRIMITIVES


def effective_possible_values(
    primitive: str, possible_values: list[str]
) -> list[str]:
    """実行時に受け取れる値の一覧。

    - 比較型なら、プリミティブの定義から導いた値を先に置く(D59)。渡された値に
      定義の外の値があれば落とさずに後ろに残す(黙って消すと、その値を参照する
      行が C5 をすり抜ける)。
    - passthrough なら、渡された値の末尾に予約語「その他」を足す(D45)。

    層3・バインディング設定・C5・C12 はすべてこれを通す。IR の `possible_values`
    に「その他」を書くこと、比較型の fact に possible_values を書くことは許さない
    (どちらも C4 で止める)。
    """
    derived = derived_possible_values(primitive)
    if derived is not None:
        values = list(derived)
        values.extend(v for v in possible_values if v not in values)
        return values
    values = list(possible_values)
    if has_other_value(primitive) and OTHER_VALUE not in values:
        values.append(OTHER_VALUE)
    return values


# --------------------------------------------------------------------------
# ② 2軸分類の結果(v1 から変更なし)
# --------------------------------------------------------------------------


@dataclass
class AxisResult:
    """②2軸分類の1軸分の結果。

    src/stage2_classify/base.py の AxisClassificationResult に対応する
    (`strategy_id` のみ stage2 側に無いためオプショナル)。
    """

    label: str
    confidence: float
    rationale: str
    evidence_quote: str = ""
    quote_verified: bool = False
    vocab_flagged: bool = False
    strategy_id: Optional[str] = None

    @property
    def confidence_below_threshold(self) -> bool:
        return self.confidence < CONFIDENCE_THRESHOLD


@dataclass
class ActionRef:
    """③a Action正規化の結果(設計時2段階ルックアップ)。

    NL表層->候補はLLM、階層への位置づけは人間が新規登録時のみ判断、
    実行時は静的文字列一致で完結させる、という設計を踏襲する。
    """

    surface: str
    normalized: str
    parent: Optional[str]
    is_new: bool
    approved_by: Optional[str] = None


# --------------------------------------------------------------------------
# fact の定義(v2 新設。設計書3.3・D1)
# --------------------------------------------------------------------------


@dataclass
class SourceRef:
    """一致プリミティブの引数1つ分の値の取得元(設計書3.3・D5)。

    フィールド名が `from_` なのは `from` が Python の予約語のため。JSON では
    `"from"` キーになる(`source_ref_to_json` / `source_ref_from_json`)。
    """

    from_: SourceKind
    path: Optional[str] = None  # action_request / master のとき
    value: Optional[str] = None  # literal のとき


def source_ref_to_json(ref: SourceRef) -> dict:
    """`SourceRef` を JSON 表現(`from` キー)に変換する。"""
    out: dict = {"from": ref.from_}
    if ref.from_ == "literal":
        out["value"] = ref.value
    else:
        out["path"] = ref.path
    return out


def source_ref_from_json(payload: dict) -> SourceRef:
    """JSON 表現から `SourceRef` を復元する(正解IR・登録簿の読み込み用)。"""
    return SourceRef(
        from_=payload["from"],
        path=payload.get("path"),
        value=payload.get("value"),
    )


@dataclass
class PassthroughFact:
    """区分の値をそのまま渡す fact(設計書3.3・D62)。

    条文が挙げた区分の値(`possible_values`)と、予約語「その他」が指す状態の注記
    (`other_value_note`)を持つ。どちらも passthrough の fact にしか意味がないので、
    v1.7 で `ComparisonFact` と型を分けた(v1.6 までは1つの `FactDef` が両方の欄を
    持ち、「比較型では書いてはいけない欄」を C4 でしか表せなかった)。

    `possible_values` に予約語「その他」は書かない(配線ジェネレータが末尾に足す。
    D45)。`other_value_note` は必須(D55)。
    """

    variable: str
    possible_values: list[str]
    # D55: 予約語「その他」が指す状態(例: 許可を得ている)。実行時に値を当てはめる
    # 側が、何を「その他」にしてよいかを知るための注記で、必須
    other_value_note: str
    sources: dict[str, SourceRef]
    # この fact の追加を承認した人。未承認なら None(D14)
    approved_by: Optional[str] = None
    # 型が primitive を決める。JSON の読み書きのために欄としても持つ
    primitive: Literal["passthrough"] = "passthrough"

    def to_json(self) -> dict:
        """層3・バインディング設定に出す形(設計書4.3・4.5)。

        出す欄は v1.6 と同じ。`possible_values` は条文が挙げた値のままで、
        予約語「その他」を足すのは呼び出し側(`effective_values_of`)の役目。
        """
        return {
            "variable": self.variable,
            "primitive": "passthrough",
            "possible_values": list(self.possible_values),
            "sources": {k: source_ref_to_json(v) for k, v in self.sources.items()},
            "approved_by": self.approved_by,
            "other_value_note": self.other_value_note,
        }


@dataclass
class ComparisonFact:
    """2つの値を比べる fact(設計書3.3・D62)。

    取りうる値はプリミティブの定義から決まる(D59)ので `possible_values` を持たず、
    予約語「その他」も付かない(D45)ので `other_value_note` も持たない。
    """

    variable: str
    primitive: str
    sources: dict[str, SourceRef]
    # 語彙登録時に人が付ける比較の種類の注釈(任意)
    comparison_kind: Optional[str] = None
    approved_by: Optional[str] = None

    def to_json(self) -> dict:
        """層3・バインディング設定に出す形(設計書4.3・4.5)。

        `possible_values` には、プリミティブの定義から導いた値を出す(D59・D62。
        「どちらの型でも、実際に使う値の一覧を出力する」)。未知のプリミティブは
        空で出し、C4 が止める。
        """
        out: dict = {
            "variable": self.variable,
            "primitive": self.primitive,
            "possible_values": list(derived_possible_values(self.primitive) or ()),
            "sources": {k: source_ref_to_json(v) for k, v in self.sources.items()},
        }
        if self.comparison_kind is not None:
            out["comparison_kind"] = self.comparison_kind
        out["approved_by"] = self.approved_by
        return out


# D62: fact は2つの型のどちらか。JSON では primitive が passthrough かどうかで決まる
FactDef = Union[PassthroughFact, ComparisonFact]


class IRTypeError(ValueError):
    """JSON から IR の型を作るときに、型で表した規則が破れていた(D62)。

    v1.6 までは C4 が止めていた「比較型の fact に possible_values を書く」が、
    v1.7 では型の上で表せない(`ComparisonFact` にその欄が無い)。黙って捨てると
    その値を参照する行が C5 をすり抜けるので、読み込みの時点で止める。
    """


def is_passthrough_fact(fact: FactDef) -> bool:
    """この fact が区分の値の fact か(D62)。"""
    return isinstance(fact, PassthroughFact)


def effective_values_of(fact: FactDef) -> list[str]:
    """この fact が実行時に受け取れる値の一覧(D45・D59・D62)。

    passthrough なら条文の値 ＋ 予約語「その他」、比較型ならプリミティブの定義から
    導いた値。層3・バインディング設定・C5・C12 はすべてこれを通す。
    """
    if isinstance(fact, PassthroughFact):
        return effective_possible_values("passthrough", fact.possible_values)
    return effective_possible_values(fact.primitive, [])


def fact_def_from_json(payload: dict, *, origin: str = "ir") -> FactDef:
    """JSON から fact を復元する(D62。primitive が passthrough なら区分の値の型)。

    `origin` は読み込み元。`"ir"`(既定)は IR の `new_facts` で、型で表した規則を
    ここで確かめる。`"registry"` は登録簿の層3で、比較型にも導出した
    `possible_values` が入っている(設計書4.5の出力の形)ため、その欄は読み捨てる。
    ただし D70 により、読み捨てる前に「プリミティブの定義から導いた値と一致するか」
    を確かめる(ずれていれば止める)。
    """
    primitive = payload["primitive"]
    sources = {
        k: source_ref_from_json(v) for k, v in payload.get("sources", {}).items()
    }
    if primitive == "passthrough":
        return PassthroughFact(
            variable=payload["variable"],
            possible_values=list(payload.get("possible_values", [])),
            # D55 の必須は C4 が確かめる(無い場合も読み込んで、停止の理由を
            # C4 のメッセージで出す)
            other_value_note=payload.get("other_value_note"),
            sources=sources,
            approved_by=payload.get("approved_by"),
        )
    if origin == "registry":
        # D70: 層3の比較型の値が、プリミティブの定義から導いた値と一致するかを
        # 確かめる。IR を経由して層3に入った値のずれ(例: 値の追加で「未提供」が
        # 落ちる、順序が変わる)に気付けないと、その値を参照する行が C5 をすり抜ける。
        declared = list(payload.get("possible_values") or [])
        derived = list(derived_possible_values(primitive) or ())
        if declared != derived:
            raise IRTypeError(
                f"層3の fact {payload['variable']!r}(primitive={primitive!r})の "
                f"possible_values {declared} が、プリミティブの定義から導いた値 "
                f"{derived} と一致しません(D70)。比較型の値はプリミティブの定義で"
                "決まるので、層3でも導出値のままでなければなりません。"
            )
    if origin == "ir":
        declared = payload.get("possible_values") or []
        if declared:
            raise IRTypeError(
                f"fact {payload['variable']!r}(primitive={primitive!r})は比較型なので "
                f"possible_values を持ちません(D59・D62)。書かれていた値: "
                f"{list(declared)}。比較型の値はプリミティブの定義 "
                f"{list(derived_possible_values(primitive) or ())} から導きます。"
            )
        if payload.get("other_value_note") is not None:
            raise IRTypeError(
                f"fact {payload['variable']!r}(primitive={primitive!r})は比較型なので "
                "other_value_note を持ちません(D55・D62)。予約語「その他」が付くのは "
                "passthrough の fact だけです。"
            )
    return ComparisonFact(
        variable=payload["variable"],
        primitive=primitive,
        sources=sources,
        comparison_kind=payload.get("comparison_kind"),
        approved_by=payload.get("approved_by"),
    )


@dataclass
class ValueAddition:
    """既存 fact の `possible_values` に足す値1つ分(設計書3.6・D14)。

    値ごとに承認者を持たせるのは、1つの fact に複数回にわたって値が足され、
    承認の単位が値ごとになるため。
    """

    value: str
    approved_by: Optional[str] = None

    def to_json(self) -> dict:
        return {"value": self.value, "approved_by": self.approved_by}

    @classmethod
    def from_json(cls, payload: dict) -> "ValueAddition":
        return cls(value=payload["value"], approved_by=payload.get("approved_by"))


@dataclass
class FactUsage:
    """この条文が使う fact(設計書3.3・D1)。

    `new_facts` は登録簿の層3(fact 台帳)に追加され、`referenced_facts` は層3に
    登録済みの fact を名前で参照する。`value_additions` は既存 fact の
    `possible_values` に値を足す場合に使う(fact 名 -> 値と承認者の一覧。D14)。

    `referenced_facts` は「IR が使う既存 fact を漏れなく列挙する欄」である(D21)。
    実在すること(層3にあること)と漏れがないことの両方を C6 が確かめる。
    """

    new_facts: list[FactDef] = field(default_factory=list)
    referenced_facts: list[str] = field(default_factory=list)
    value_additions: dict[str, list[ValueAddition]] = field(default_factory=dict)

    def unapproved_fact_names(self) -> list[str]:
        """承認者のいない新しい fact・新しい値を列挙する(D14。宣言順)。"""
        out = [f.variable for f in self.new_facts if f.approved_by is None]
        for variable, additions in self.value_additions.items():
            for addition in additions:
                if addition.approved_by is None:
                    out.append(f"{variable}={addition.value}")
        return out


# --------------------------------------------------------------------------
# 判定先(v2 新設。設計書3.3・D7)
# --------------------------------------------------------------------------


@dataclass
class DecisionTarget:
    """この条文の判定を入れる DMN モデルと、最終判定の ID。"""

    dmn_model: str
    entry_decision_id: str


# --------------------------------------------------------------------------
# レベル判定(v2 で層ごとの型に変更。設計書3.3・D6)
# --------------------------------------------------------------------------


@dataclass
class AtomicFactLayer:
    """① 事実の層: 必要な比較を既存の一致プリミティブで賄えるか。"""

    covered: bool
    new_primitives: list[str] = field(default_factory=list)


@dataclass
class IntermediateChange:
    """② 中間判定の層: 既存の中間判定をどう直すか。

    `relation` が判断の根拠(列集合の関係)、`action` が結論(直し方)で、v1 のように
    1つの欄に混ぜない(D6)。
    """

    table_id: str
    relation: ColumnSetRelationKind
    action: IntermediateAction
    # 中間判定に足す出力値。出力名 -> 足す値と承認者(D29・D37)。中間判定は出力を
    # 複数持つため、値の一覧だけではどの出力列の値かが分からない(例: 本人確認判定の
    # 「未」は identity_result の値で、identity_level には来ない)。
    #
    # **その表がまだ出していない値だけを書く**(D36)。既に出している値を書くと
    # C9条件3 が「余り」で止める。承認者のいない値があると
    # `new_fact_unapproved` ゲートが立つ(D37)。
    new_output_values: dict[str, list[ValueAddition]] = field(default_factory=dict)
    # action が add_column のとき足す入力列(D19)。足した列は層1の input_columns に
    # 加わり、既存の全行はその列が「-」で埋まる。
    new_input_columns: list[str] = field(default_factory=list)


@dataclass
class FinalLayer:
    """③ 最終判定の層: 既存の最終判定に行を足すか、新しい表を作るか、変えないか。

    `action="no_change"`(D30)は「中間判定だけを直す条文」を表す。このとき
    `compared_table_id`・`relation` は None で、`target.entry_decision_id` には
    影響を受ける既存の最終判定の1つを入れる(C9条件7・8 が確かめる)。
    """

    compared_table_id: Optional[str]
    relation: Optional[ColumnSetRelationKind]
    action: FinalAction


@dataclass
class LevelDetermination:
    """③aレベル判定。層ごとに問いと答えの形が違うため、層ごとの型を持つ(D6)。"""

    atomic_fact: AtomicFactLayer
    intermediate: list[IntermediateChange]
    final: FinalLayer

    def unapproved_output_values(self) -> list[str]:
        """承認者のいない新しい出力値を列挙する(D37。宣言順)。

        中間判定の出力値を増やすと、それを参照するすべての最終判定の結果が変わり
        うるので、fact の値の追加(D14)と同じく人の承認を要る扱いにする。
        """
        out: list[str] = []
        for change in self.intermediate:
            for name, additions in change.new_output_values.items():
                for addition in additions:
                    if addition.approved_by is None:
                        out.append(f"{change.table_id}.{name}={addition.value}")
        return out

    @property
    def outcome(self) -> LevelOutcome:
        """結論は最終判定の層の action から導く(値として持たない)。"""
        if self.final.action == "new_table":
            return "level1_new_table"
        if self.final.action == "add_rows":
            return "level2_row_addition"
        # D30: no_change = 中間判定だけを直した
        return "level2_intermediate_only"


# --------------------------------------------------------------------------
# 決定表(v1 から変更なし)
# --------------------------------------------------------------------------


@dataclass
class RuleRowDraft:
    """登録簿(rule_rows)1行分の草案。position順でFIRSTヒットポリシーの挿入位置を表す。

    outputsは決定表の出力名->値の汎用マップ。
    """

    row_id: str
    decision_table_id: str
    position: int
    conditions: dict[str, str]
    outputs: dict[str, str]


@dataclass
class NewDecisionTableDraft:
    """新しく作る決定表の草案(中間判定・最終判定とも)。"""

    decision_table_id: str
    decision_id: str
    decision_name: str
    input_columns: list[str]
    rule_rows: list[RuleRowDraft]
    # 来歴専用(計算には使わない)。DMN標準ではauthorityRequirementはinputDataに
    # 付けられないため、decision側に紐づける。
    knowledge_sources: list[str] = field(default_factory=list)

    @property
    def depends_on_inputs(self) -> list[str]:
        """この判定が使う入力事実の名前(D38。`input_columns` から導出)。

        ドットを含まない列がそのまま inputData の名前になる。手で書かせると
        `input_columns` と食い違っても検査されないため、導出にする。
        """
        return [c for c in self.input_columns if "." not in c]

    @property
    def depends_on_decision_names(self) -> list[str]:
        """この判定が使う他の判定の**名前**(D38。`input_columns` のドット参照から)。

        登録簿の層1が持つ `depends_on_decisions` は決定の**ID**なので、名前から ID へ
        の解決が要る。解決には IR と登録簿の両方が必要で、この型だけでは閉じない
        ため、ID への変換は配線ジェネレータ側で行う。
        """
        out: list[str] = []
        for column in self.input_columns:
            if "." not in column:
                continue
            name = column.split(".", 1)[0]
            if name not in out:
                out.append(name)
        return out


@dataclass
class CompoundFactApplication:
    """複合fact禁止原則の適用結果(D31)。

    手で書くのは `rejected_in_primitive`(一致モジュール側で AND を組もうとして
    拒否されたか)だけにする。True なら配線ジェネレータが C3 で停止する。

    どの案を採ったか(`option_taken`)は IR の他の欄から決定表ごとに導出する
    (`ClauseIR.option_taken`)。1つの IR が新しい表の行と既存の表への行追加を
    両方作ることがあり、IR に1つだけ手で書かせると、正解 IR と前段の出力の比較で
    揺れるため。
    """

    rejected_in_primitive: bool = False
    rationale: str = ""


@dataclass
class Provenance:
    origin: str
    clause_ref: str
    registered_by: str
    registered_at: str  # ISO8601

    def to_json(self) -> dict:
        return {
            "origin": self.origin,
            "clause_ref": self.clause_ref,
            "registered_by": self.registered_by,
            "registered_at": self.registered_at,
        }


# --------------------------------------------------------------------------
# ④人間レビューのゲート(v2 で new_fact_unapproved を追加。D10)
# --------------------------------------------------------------------------


@dataclass
class ReviewGates:
    """④人間レビューへ回すかを判定する独立したゲート群。

    複数の条件を1つの不透明なスコアに混ぜず、それぞれ独立した真偽値として保持する
    (複合fact禁止原則と同じ思想)。`should_review`はこれらのOR。
    """

    confidence_below_threshold: bool = False
    quote_unverified: bool = False
    vocab_flagged: bool = False
    # Action正規化で新規語彙が検出された場合に立てる。
    new_action_unapproved: bool = False
    # 承認者のいない新しい fact・既存 fact への新しい値・中間判定の新しい出力値が
    # ある場合に立てる(D10・D14・D37)。
    new_fact_unapproved: bool = False
    # 新しい一致プリミティブが必要な場合(atomic_fact.covered=False)に立てる(D20)。
    # 新規プリミティブの追加は③aの範囲外なので、必ず人間レビューに回す。
    new_primitive_required: bool = False
    # ここから下の5つは前段の Action 正規化が立てる(前段設計書4.5節 v0.4)。後半の
    # 配線ジェネレータは立てないので、既定は False のまま。
    # 様相を削った surface が条文にない(P4)。様相そのものはプログラムが削るので、
    # 様相が残っていることではゲートを立てない(v0.4 で条件が変わった)。
    action_surface_unverified: bool = False
    # 行為の語句の候補が2つ出た(P17)。どちらが規制の対象かを人が見る。
    action_surface_ambiguous: bool = False
    # 既存に寄せたが、中心の動詞が選んだ Action の過去の語句の動詞にない(P15)。
    action_verb_mismatch: bool = False
    # 抽象的な段の Action を選んだ(P15)。禁止が子すべてに及ぶので人が段を確かめる。
    action_abstract_selected: bool = False
    # 新規候補の名前・定義に規範の様相の語がある(P16)。
    action_name_rule: bool = False
    # 新規候補の名前・定義が既存の Action に近い(P8。取りこぼしを人が拾う)。
    action_possible_duplicate: bool = False

    @property
    def should_review(self) -> bool:
        return bool(self.gate_names())

    def gate_names(self) -> list[str]:
        """立っているゲートの名前を列挙する(集計用。設計書4.5・前段設計書4.5節)。"""
        names = [
            "confidence_below_threshold",
            "quote_unverified",
            "vocab_flagged",
            "new_action_unapproved",
            "new_fact_unapproved",
            "new_primitive_required",
            "action_surface_unverified",
            "action_surface_ambiguous",
            "action_verb_mismatch",
            "action_abstract_selected",
            "action_name_rule",
            "action_possible_duplicate",
        ]
        return [n for n in names if getattr(self, n)]

    def reasons(self) -> list[str]:
        """立っているゲートの説明文を列挙する(④レビューUIでの提示用)。"""
        out = []
        if self.confidence_below_threshold:
            out.append(f"confidence < {CONFIDENCE_THRESHOLD}")
        if self.quote_unverified:
            out.append("evidence_quoteが条文中に実在しない(ハルシネーション疑い)")
        if self.vocab_flagged:
            out.append("既知の語彙的盲点パターンを含む")
        if self.new_action_unapproved:
            out.append("新規Actionが未承認")
        if self.new_fact_unapproved:
            out.append("新規fact・既存factへの新規値・中間判定の新規出力値が未承認")
        if self.new_primitive_required:
            out.append("新しい一致プリミティブが必要(③aの範囲外)")
        if self.action_surface_unverified:
            out.append("様相を削った行為の語句が条文に逐語で実在しない")
        if self.action_surface_ambiguous:
            out.append("行為の語句の候補が2つ出た(どちらが規制の対象か)")
        if self.action_verb_mismatch:
            out.append(
                "既存の Action に寄せたが、中心の動詞がその Action の過去の語句にない"
            )
        if self.action_abstract_selected:
            out.append("抽象的な段の Action を選んだ(段は正しいか)")
        if self.action_name_rule:
            out.append("新規候補の名前・定義に規範の様相の語がある")
        if self.action_possible_duplicate:
            out.append("新規候補の Action が既存の Action に近い(既存に寄せるべきか)")
        return out


# --------------------------------------------------------------------------
# ClauseIR
# --------------------------------------------------------------------------


@dataclass
class ClauseIR:
    """1条文分のIR。スキーマ配線ジェネレータの唯一の入力。"""

    clause_ref: str
    clause_text: str
    axis1_result: AxisResult
    axis2_result: AxisResult
    actor: str
    action: ActionRef
    scope: str
    target: DecisionTarget
    facts: FactUsage
    level_determination: LevelDetermination
    rule_row_additions: list[RuleRowDraft]
    new_decision_tables: list[NewDecisionTableDraft]
    compound_fact_application: CompoundFactApplication
    provenance: Provenance
    review_gates: ReviewGates = field(default_factory=ReviewGates)
    human_review_status: HumanReviewStatus = "pending"

    def all_fact_keys(self) -> set[str]:
        """このIRのどこかの決定表で参照されている全factキー。

        ドット参照(他決定の出力)も含むため、fact としての突合には
        `wiring_generator._flat_fact_keys()` でドット参照を除いてから使う。
        C2(新規factが使われているか)の基準でもある。
        """
        keys: set[str] = set()
        for row in self.rule_row_additions:
            keys.update(row.conditions.keys())
        for table in self.new_decision_tables:
            keys.update(table.input_columns)
            for row in table.rule_rows:
                keys.update(row.conditions.keys())
        return keys

    @property
    def option_taken(self) -> dict[str, CompoundFactOption]:
        """複合条件をどう表したかを、決定表ごとに導出する(D31。設計書3.4)。

        - `new_decision_tables` の表: 他の判定から参照されていれば
          `new_intermediate_table`、それ以外は `new_table_row`
        - `rule_row_additions` だけがある既存の表: `existing_table_row_addition`

        キーは決定表ID。IR が新しい表の行と既存の表の行を両方作る場合、両方が入る
        (v1.2 までは IR に1つだけ手で書いていたため、どちらかが隠れていた)。

        「他の判定から参照されている」は、この IR の中の別の表が
        `判定名.出力名` でこの表の判定名を参照しているかで判定する(D38 で
        `depends_on_decisions` が導出になったので、入力列だけを見る)。新しく作る表を
        既存の判定が参照していることはない(登録簿は過去の条文の結果であり、今回の表を
        知らない)。

        導出は「その IR を作った時点の判断の記録」である(D41)。後の条文がこの表を
        参照しても、この IR の導出結果は変わらない。
        """
        out: dict[str, CompoundFactOption] = {}
        for table in self.new_decision_tables:
            referenced = False
            for other in self.new_decision_tables:
                if other.decision_table_id == table.decision_table_id:
                    continue
                if table.decision_name in other.depends_on_decision_names:
                    referenced = True
                    break
            out[table.decision_table_id] = (
                "new_intermediate_table" if referenced else "new_table_row"
            )
        for row in self.rule_row_additions:
            if row.decision_table_id in out:
                continue  # 新しく作る表の行は、その表の導出結果に含まれる
            out[row.decision_table_id] = "existing_table_row_addition"
        return out


def build_review_gates(
    axis1: AxisResult,
    axis2: AxisResult,
    action: ActionRef,
    facts: FactUsage,
    level_determination: Optional[LevelDetermination] = None,
) -> ReviewGates:
    """②の判定結果・Action正規化・fact の使い方から、④人間レビューのゲートを組み立てる。

    軸1・軸2のいずれかがゲート条件に該当すれば、そのゲートを立てる(OR条件)。
    `level_determination` を渡すと `new_primitive_required` も判定する(D20)。
    """
    return ReviewGates(
        confidence_below_threshold=(
            axis1.confidence_below_threshold or axis2.confidence_below_threshold
        ),
        quote_unverified=(not axis1.quote_verified) or (not axis2.quote_verified),
        vocab_flagged=axis1.vocab_flagged or axis2.vocab_flagged,
        new_action_unapproved=action.is_new and action.approved_by is None,
        # D14・D37: 「新語彙あり」ではなく「承認者がいない新語彙あり」で立てる。
        # これにより、承認者を入れればゲートが下りる。中間判定の新しい出力値
        # (D37)も、fact の値と同じく承認の対象にする。
        new_fact_unapproved=bool(facts.unapproved_fact_names())
        or bool(
            level_determination is not None
            and level_determination.unapproved_output_values()
        ),
        new_primitive_required=(
            level_determination is not None
            and not level_determination.atomic_fact.covered
        ),
    )


# --------------------------------------------------------------------------
# JSON から ClauseIR を復元する(正解IR の読み込み用。設計書6.2)
# --------------------------------------------------------------------------


def _axis_from_json(payload: dict) -> AxisResult:
    return AxisResult(
        label=payload["label"],
        confidence=payload["confidence"],
        rationale=payload.get("rationale", ""),
        evidence_quote=payload.get("evidence_quote", ""),
        quote_verified=payload.get("quote_verified", False),
        vocab_flagged=payload.get("vocab_flagged", False),
        strategy_id=payload.get("strategy_id"),
    )


def _rule_row_from_json(payload: dict) -> RuleRowDraft:
    return RuleRowDraft(
        row_id=payload["row_id"],
        decision_table_id=payload["decision_table_id"],
        position=payload["position"],
        conditions=dict(payload.get("conditions", {})),
        outputs=dict(payload.get("outputs", {})),
    )


def _level_from_json(payload: dict) -> LevelDetermination:
    atomic = payload["atomic_fact"]
    final = payload["final"]
    return LevelDetermination(
        atomic_fact=AtomicFactLayer(
            covered=atomic["covered"],
            new_primitives=list(atomic.get("new_primitives", [])),
        ),
        intermediate=[
            IntermediateChange(
                table_id=change["table_id"],
                relation=change["relation"],
                action=change["action"],
                new_output_values={
                    name: [ValueAddition.from_json(v) for v in values]
                    for name, values in change.get("new_output_values", {}).items()
                },
                new_input_columns=list(change.get("new_input_columns", [])),
            )
            for change in payload.get("intermediate", [])
        ],
        final=FinalLayer(
            compared_table_id=final.get("compared_table_id"),
            relation=final.get("relation"),
            action=final["action"],
        ),
    )


def clause_ir_from_json(payload: dict) -> ClauseIR:
    """正解IR の JSON から `ClauseIR` を復元する(設計書6.2)。

    `review_gates` は JSON から読まず、`build_review_gates()` で**導出する**。
    ゲートは軸1・軸2・Action・fact・レベル判定から機械的に決まるので、正解IR に
    手で書かせると、他の欄と食い違っても検査されない(D31・D38 と同じ考え方)。
    期待するゲートの一覧は、正解IR のファイルの `review.expected_gates` に書き、
    評価スクリプトが導出結果と突き合わせる。
    """
    axis1 = _axis_from_json(payload["axis1_result"])
    axis2 = _axis_from_json(payload["axis2_result"])
    action_payload = payload["action"]
    action = ActionRef(
        surface=action_payload["surface"],
        normalized=action_payload["normalized"],
        parent=action_payload.get("parent"),
        is_new=action_payload["is_new"],
        approved_by=action_payload.get("approved_by"),
    )
    facts_payload = payload.get("facts", {})
    facts = FactUsage(
        new_facts=[
            fact_def_from_json(f) for f in facts_payload.get("new_facts", [])
        ],
        referenced_facts=list(facts_payload.get("referenced_facts", [])),
        value_additions={
            name: [ValueAddition.from_json(v) for v in values]
            for name, values in facts_payload.get("value_additions", {}).items()
        },
    )
    level = _level_from_json(payload["level_determination"])
    target_payload = payload["target"]
    provenance_payload = payload["provenance"]
    return ClauseIR(
        clause_ref=payload["clause_ref"],
        clause_text=payload["clause_text"],
        axis1_result=axis1,
        axis2_result=axis2,
        actor=payload["actor"],
        action=action,
        scope=payload["scope"],
        target=DecisionTarget(
            dmn_model=target_payload["dmn_model"],
            entry_decision_id=target_payload["entry_decision_id"],
        ),
        facts=facts,
        level_determination=level,
        rule_row_additions=[
            _rule_row_from_json(r) for r in payload.get("rule_row_additions", [])
        ],
        new_decision_tables=[
            NewDecisionTableDraft(
                decision_table_id=t["decision_table_id"],
                decision_id=t["decision_id"],
                decision_name=t["decision_name"],
                input_columns=list(t["input_columns"]),
                rule_rows=[_rule_row_from_json(r) for r in t.get("rule_rows", [])],
                knowledge_sources=list(t.get("knowledge_sources", [])),
            )
            for t in payload.get("new_decision_tables", [])
        ],
        compound_fact_application=CompoundFactApplication(
            rejected_in_primitive=payload["compound_fact_application"].get(
                "rejected_in_primitive", False
            ),
            rationale=payload["compound_fact_application"].get("rationale", ""),
        ),
        provenance=Provenance(
            origin=provenance_payload["origin"],
            clause_ref=provenance_payload["clause_ref"],
            registered_by=provenance_payload["registered_by"],
            registered_at=provenance_payload["registered_at"],
        ),
        review_gates=build_review_gates(axis1, axis2, action, facts, level),
        human_review_status=payload.get("human_review_status", "pending"),
    )
