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

## 引き継ぐ原則(設計書1.4)

- 4点セットの同時生成、複合 fact 禁止、FIRST と行の順序、来歴の表し方、Action 正規化
- IR に入れてよい情報は条文本文と出典付きの context だけ
- 人間レビューのゲートは1つのスコアに混ぜず、独立したゲートの OR として持つ
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

ColumnSetRelationKind = Literal[
    "exact_match",
    "proper_subset",
    "intersecting_non_subset",
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

# new_facts では宣言できないプリミティブ(設計書2章 D23)。
BOOTSTRAP_ONLY_PRIMITIVES: frozenset[str] = frozenset({"identifiability_classifier"})


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
class FactDef:
    """fact 1つ分の定義。DMN の inputData 1つに対応する(設計書3.3)。"""

    variable: str
    primitive: str
    possible_values: list[str]
    sources: dict[str, SourceRef]
    # 語彙登録時に人が付ける比較の種類の注釈(任意)
    comparison_kind: Optional[str] = None
    # この fact の追加を承認した人。未承認なら None(D14)
    approved_by: Optional[str] = None

    def to_json(self) -> dict:
        out: dict = {
            "variable": self.variable,
            "primitive": self.primitive,
            "possible_values": list(self.possible_values),
            "sources": {k: source_ref_to_json(v) for k, v in self.sources.items()},
        }
        if self.comparison_kind is not None:
            out["comparison_kind"] = self.comparison_kind
        out["approved_by"] = self.approved_by
        return out

    @classmethod
    def from_json(cls, payload: dict) -> "FactDef":
        return cls(
            variable=payload["variable"],
            primitive=payload["primitive"],
            possible_values=list(payload.get("possible_values", [])),
            sources={
                k: source_ref_from_json(v) for k, v in payload.get("sources", {}).items()
            },
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

    @property
    def should_review(self) -> bool:
        return any(
            (
                self.confidence_below_threshold,
                self.quote_unverified,
                self.vocab_flagged,
                self.new_action_unapproved,
                self.new_fact_unapproved,
                self.new_primitive_required,
            )
        )

    def gate_names(self) -> list[str]:
        """立っているゲートの名前を列挙する(集計用。設計書4.5)。"""
        names = [
            "confidence_below_threshold",
            "quote_unverified",
            "vocab_flagged",
            "new_action_unapproved",
            "new_fact_unapproved",
            "new_primitive_required",
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
        new_facts=[FactDef.from_json(f) for f in facts_payload.get("new_facts", [])],
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
