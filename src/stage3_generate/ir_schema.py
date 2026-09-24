"""③a 中間表現(IR)スキーマ。

②2軸分類・③a各判定の結果を1つの構造(`ClauseIR`)にまとめ、スキーマ配線ジェネレータが
4点セット(DMN XML／バインディング設定／ルーティング表／Scope・Decision登録簿)を
同時生成するための唯一の入力とする。

## reference/ からの踏襲と、aoi_doc2dmn向けの調整

土台は`reference/gijinka-extraction-pipeline/src/pipeline/ir_schema.py`および
`docs/ir_schema.md`(Apache KIE/Kogito実機で18/18一致の検証済み設計)。
以下は踏襲する:

- 4点セットを同一IRから同時生成する(別々の関数・タイミングで作らない)
- 複合fact禁止原則、FIRSTヒットポリシー、ActionRefの設計時2段階ルックアップ
- RuleRowDraft.outputsを出力名->値の汎用マップとして持つ設計判断

aoi_doc2dmn向けに調整した点:

1. **`human_review_status`を単一のstatusから、独立した複数ゲートのOR条件に変更**
   (`ReviewGates`)。reference側は`confidence < 0.7`のみを判定条件にしていたが、
   本リポジトリの実測でconfidenceは常に0.7超を返し較正情報を持たないことが判明した
   (`testdata/schema.md`参照)。そのため②で実装した`quote_verified`(ハルシネーション型検出)
   ・`vocab_flagged`(語彙的盲点型検出)を独立したゲートとして併置する。複数のゲートを
   1つの不透明なスコアに混ぜないのは複合fact禁止原則と同じ思想。
2. **`AxisResult`に`quote_verified`/`vocab_flagged`を追加**。②の
   `AxisClassificationResult`(src/stage2_classify/base.py)と対応させる。
3. `strategy_id`はreference側にあったが、本リポジトリの`AxisClassificationResult`には
   無いためオプショナルにした。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

ColumnSetRelationKind = Literal[
    "exact_match",
    "proper_subset",
    "intersecting_non_subset",
]

LevelOutcome = Literal["level1_new_table", "level2_row_addition"]

CompoundFactOption = Literal[
    "existing_table_row_addition",
    "new_intermediate_table",
    "rejected_compound_in_primitive",
]

HumanReviewStatus = Literal["pending", "approved", "rejected"]

CONFIDENCE_THRESHOLD = 0.7


@dataclass
class AxisResult:
    """②2軸分類の1軸分の結果。

    src/stage2_classify/base.py の AxisClassificationResult に対応する。
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
class ReviewGates:
    """④人間レビューへ回すかを判定する独立したゲート群。

    複数の条件を1つの不透明なスコアに混ぜず、それぞれ独立した真偽値として保持する
    (複合fact禁止原則と同じ思想)。`should_review`はこれらのOR。
    """

    confidence_below_threshold: bool = False
    quote_unverified: bool = False
    vocab_flagged: bool = False
    # ③固有のゲート。Action正規化で新規語彙が検出された場合に立てる。
    new_action_unapproved: bool = False

    @property
    def should_review(self) -> bool:
        return (
            self.confidence_below_threshold
            or self.quote_unverified
            or self.vocab_flagged
            or self.new_action_unapproved
        )

    def reasons(self) -> list[str]:
        """立っているゲートの名前を列挙する(④レビューUIでの提示用)。"""
        out = []
        if self.confidence_below_threshold:
            out.append(f"confidence < {CONFIDENCE_THRESHOLD}")
        if self.quote_unverified:
            out.append("evidence_quoteが条文中に実在しない(ハルシネーション疑い)")
        if self.vocab_flagged:
            out.append("既知の語彙的盲点パターンを含む")
        if self.new_action_unapproved:
            out.append("新規Actionが未承認")
        return out


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


@dataclass
class ColumnSetRelation:
    """③aレベル判定における、層ごとの列集合関係の判定結果。"""

    layer: Literal["atomic_fact", "intermediate", "final"]
    relation: ColumnSetRelationKind
    detail: str


@dataclass
class LevelDetermination:
    """③aレベル判定(3層×3パターン)の結果一式。"""

    atomic_fact_layer: ColumnSetRelation
    intermediate_layer: ColumnSetRelation
    final_layer: ColumnSetRelation
    outcome: LevelOutcome
    reused_intermediate_decisions: list[str] = field(default_factory=list)


@dataclass
class RuleRowDraft:
    """登録簿(rule_rows)1行分の草案。position順でFIRSTヒットポリシーの挿入位置を表す。

    outputsは決定表の出力名->値の汎用マップ(reference側の設計判断を踏襲)。
    """

    row_id: str
    decision_table_id: str
    position: int
    conditions: dict[str, str]
    outputs: dict[str, str]


@dataclass
class NewDecisionTableDraft:
    """レベル1(新規決定表)判定時に生成する決定表の草案。"""

    decision_table_id: str
    decision_id: str
    decision_name: str
    depends_on_decisions: list[str]
    depends_on_inputs: list[str]
    input_columns: list[str]
    rule_rows: list[RuleRowDraft]
    # 来歴専用(計算には使わない)。DMN標準ではauthorityRequirementはinputDataに
    # 付けられないため、decision側に紐づける。
    knowledge_sources: list[str] = field(default_factory=list)


@dataclass
class CompoundFactApplication:
    """複合fact禁止原則の適用結果(どの案を採ったか)。"""

    option_taken: CompoundFactOption
    rationale: str


@dataclass
class Provenance:
    origin: str
    clause_ref: str
    registered_by: str
    registered_at: str  # ISO8601


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
    level_determination: LevelDetermination
    rule_row_additions: list[RuleRowDraft]
    new_decision_tables: list[NewDecisionTableDraft]
    compound_fact_application: CompoundFactApplication
    provenance: Provenance
    review_gates: ReviewGates = field(default_factory=ReviewGates)
    human_review_status: HumanReviewStatus = "pending"

    def all_fact_keys(self) -> set[str]:
        """このIRが要求する全factキー(バインディング設定側と突合する集合)。"""
        keys: set[str] = set()
        for row in self.rule_row_additions:
            keys.update(row.conditions.keys())
        for table in self.new_decision_tables:
            keys.update(table.input_columns)
            for row in table.rule_rows:
                keys.update(row.conditions.keys())
        return keys


def build_review_gates(
    axis1: AxisResult,
    axis2: AxisResult,
    action: ActionRef,
) -> ReviewGates:
    """②の判定結果とAction正規化結果から、④人間レビューのゲートを組み立てる。

    軸1・軸2のいずれかがゲート条件に該当すれば、そのゲートを立てる(OR条件)。
    """
    return ReviewGates(
        confidence_below_threshold=(
            axis1.confidence_below_threshold or axis2.confidence_below_threshold
        ),
        quote_unverified=(not axis1.quote_verified) or (not axis2.quote_verified),
        vocab_flagged=axis1.vocab_flagged or axis2.vocab_flagged,
        new_action_unapproved=action.is_new and action.approved_by is None,
    )
