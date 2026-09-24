"""ゴールデンパス(ipa_secguide_v4_6_7_1: 特権アカウント)のIR構築。

③aの最初の1周として、単一条文・単一決定表・既存表への統合判断なし、という
最も単純なケースで「条文 → IR → 4点セット」が一気通貫で動くことを確認するための実装。

## 対象条文

> システム開発を担当する者が本番環境において特権/管理者権限で操作を行うことを禁止する。

(IPA「中小企業の情報セキュリティ対策ガイドライン」第4.0版 付録5、6.7 特権アカウント)

## この条文を選んだ理由

- `hard_declarative`セル(軸1=ハード制約、軸2=宣言的)、`is_ambiguous=false`
- 判定に必要な事実が「操作者の役割」「対象環境」「要求権限」の3つの独立した属性のみで、
  いずれも条文本文から直接読み取れる(外部知識・contextを必要としない。
  `testdata/schema.md`「gold label判定の方針」節の論点に触れない)
- 他の条文・既存決定表を参照しないため、レベル1/レベル2判定(複数DMNファイル問題)が発生しない
- 3つの属性はいずれも単一比較で判定でき、複合fact禁止原則に自然に適合する
  (「開発担当者 かつ 本番環境 かつ 特権権限」というANDは決定表の1行として表現し、
  一致プリミティブ側では組まない)

## IRの構造

3つのフラットfactを入力とする新規決定表を1つ作る(レベル1相当だが、既存表が無いため
判定自体は自明)。FIRSTヒットポリシーで、危険な条件(禁止に該当するケース)を上位行に置く。

| position | operator_role | target_environment | requested_privilege | → decision | reason_code |
|---|---|---|---|---|---|
| 1 | システム開発担当 | 本番環境 | 特権 | 禁止 | dev_privileged_op_on_production |
| 2 | - | - | - | 許可 | not_prohibited_combination |
"""
from __future__ import annotations

from datetime import datetime, timezone

from .ir_schema import (
    ActionRef,
    AxisResult,
    ClauseIR,
    ColumnSetRelation,
    CompoundFactApplication,
    LevelDetermination,
    NewDecisionTableDraft,
    Provenance,
    RuleRowDraft,
    build_review_gates,
)

CLAUSE_ID = "ipa_secguide_v4_6_7_1"
CLAUSE_REF = "IPA情報セキュリティ関連規程 6.7 特権アカウント(第3項)"

DECISION_ID = "decision_privileged_operation"
DECISION_NAME = "特権操作可否判定"
DECISION_TABLE_ID = "dt_privileged_operation"

DEFINITIONS_ID = "privileged_operation_definitions"
DEFINITIONS_NAME = "特権操作可否判定"
NAMESPACE = "http://gijinka.example.com/dmn/privileged_operation"

KS_ACCOUNT_REGISTRY = "ks_privileged_account_registry"
KS_ACTION_REQUEST = "ks_action_request"

INPUT_COLUMNS = ["operator_role", "target_environment", "requested_privilege"]

# バインディング設定。どの一致プリミティブを、どこから取得した値に適用するか。
# possible_valuesを明示し、sourcesは名前付きキーで与える(reference側の設計を踏襲)。
BINDINGS = [
    {
        "variable": "operator_role",
        "primitive": "exact_match",
        "possible_values": ["システム開発担当", "運用担当", "その他"],
        "sources": {
            "subject": "action_request.operator.role",
            "registry": "privileged_account_registry.role",
        },
    },
    {
        "variable": "target_environment",
        "primitive": "exact_match",
        "possible_values": ["本番環境", "検証環境", "開発環境"],
        "sources": {
            "subject": "action_request.target.environment",
        },
    },
    {
        "variable": "requested_privilege",
        "primitive": "exact_match",
        "possible_values": ["特権", "一般"],
        "sources": {
            "subject": "action_request.requested_privilege",
            "registry": "privileged_account_registry.granted_privilege",
        },
    },
]


def build_golden_path_ir(
    axis1_result: AxisResult,
    axis2_result: AxisResult,
    *,
    clause_text: str,
    action_approved_by: str | None = None,
    registered_by: str = "claude_code",
    registered_at: str | None = None,
) -> ClauseIR:
    """ゴールデンパス条文のIRを構築する。

    axis1_result / axis2_result は②の分類器の実出力を渡す
    (ハードコードせず、実際の判定結果をIRに反映させる)。
    """
    action = ActionRef(
        surface="特権/管理者権限で操作を行う",
        normalized="特権操作",
        parent="システム操作",
        is_new=True,
        approved_by=action_approved_by,
    )

    rule_rows = [
        RuleRowDraft(
            row_id="id_r_priv_1",
            decision_table_id=DECISION_TABLE_ID,
            position=1,
            conditions={
                "operator_role": "システム開発担当",
                "target_environment": "本番環境",
                "requested_privilege": "特権",
            },
            outputs={
                "decision": "禁止",
                "reason_code": "dev_privileged_op_on_production",
            },
        ),
        RuleRowDraft(
            row_id="id_r_priv_2",
            decision_table_id=DECISION_TABLE_ID,
            position=2,
            conditions={
                "operator_role": "-",
                "target_environment": "-",
                "requested_privilege": "-",
            },
            outputs={
                "decision": "許可",
                "reason_code": "not_prohibited_combination",
            },
        ),
    ]

    new_table = NewDecisionTableDraft(
        decision_table_id=DECISION_TABLE_ID,
        decision_id=DECISION_ID,
        decision_name=DECISION_NAME,
        depends_on_decisions=[],
        depends_on_inputs=list(INPUT_COLUMNS),
        input_columns=list(INPUT_COLUMNS),
        rule_rows=rule_rows,
        knowledge_sources=[KS_ACCOUNT_REGISTRY, KS_ACTION_REQUEST],
    )

    level_determination = LevelDetermination(
        atomic_fact_layer=ColumnSetRelation(
            layer="atomic_fact",
            relation="proper_subset",
            detail=(
                "operator_role / target_environment / requested_privilege の3factは"
                "いずれも既存のprimitiveに相当する単一比較(exact_match)で賄える。"
                "新規primitiveの追加は不要。"
            ),
        ),
        intermediate_layer=ColumnSetRelation(
            layer="intermediate",
            relation="exact_match",
            detail=(
                "中間決定を必要としない。3factから直接最終判定に至るため、"
                "再利用すべき既存中間決定も無い。"
            ),
        ),
        final_layer=ColumnSetRelation(
            layer="final",
            relation="intersecting_non_subset",
            detail=(
                "action=特権操作 は既存ルーティング表に存在しない新規actionであり、"
                "既存決定表への行追加(レベル2)は行わず新規決定表を作成する。"
                "ただし本スコープでは既存DMNファイル自体が存在しないため、"
                "この判定は形式的なものにとどまる。"
            ),
        ),
        outcome="level1_new_table",
        reused_intermediate_decisions=[],
    )

    compound_fact = CompoundFactApplication(
        option_taken="existing_table_row_addition",
        rationale=(
            "「開発担当者 かつ 本番環境 かつ 特権権限」という複合条件は、"
            "一致プリミティブ側でANDを組まず、決定表の1行(id_r_priv_1)の"
            "3列の条件として表現する(複合fact禁止原則)。"
        ),
    )

    provenance = Provenance(
        origin="regulation_clause",
        clause_ref=CLAUSE_REF,
        registered_by=registered_by,
        registered_at=registered_at or datetime.now(timezone.utc).isoformat(),
    )

    review_gates = build_review_gates(axis1_result, axis2_result, action)

    return ClauseIR(
        clause_ref=CLAUSE_REF,
        clause_text=clause_text,
        axis1_result=axis1_result,
        axis2_result=axis2_result,
        actor="あおい",
        action=action,
        scope="情報システムの運用操作",
        level_determination=level_determination,
        rule_row_additions=[],
        new_decision_tables=[new_table],
        compound_fact_application=compound_fact,
        provenance=provenance,
        review_gates=review_gates,
        human_review_status="pending",
    )
