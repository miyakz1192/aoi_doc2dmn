"""ゴールデンパス(ipa_secguide_v4_6_7_1: 特権アカウント)のIR v2 構築。

③aの最初の1周として、単一条文・単一決定表・既存表への統合判断なし、という
最も単純なケースで「条文 → IR → 4点セット」が一気通貫で動くことを確認するための実装。

仕様: docs/design/aoi_doc2dmn_IRv2_design.md 3.8節(特権アカウント条文の IR v2)。

## 対象条文

> システム開発を担当する者が本番環境において特権/管理者権限で操作を行うことを禁止する。

(IPA「中小企業の情報セキュリティ対策ガイドライン」第4.0版 付録5、6.7 特権アカウント)

## この条文を選んだ理由

- `hard_declarative`セル(軸1=ハード制約、軸2=宣言的)、`is_ambiguous=false`
- 判定に必要な事実が「操作者の役割」「対象環境」「要求権限」の3つの独立した属性のみで、
  いずれも条文本文から直接読み取れる(外部知識・contextを必要としない。
  `testdata/schema.md`「gold label判定の方針」節の論点に触れない)
- 他の条文・既存決定表を参照しないため、レベル1/レベル2判定が発生しない
- 3つの属性はいずれも単一比較で判定でき、複合fact禁止原則に自然に適合する

## v1(コミット 9e66776)との違い

設計書3.8の通り3点ある。

1. fact の定義が IR の中に入った(`facts.new_facts`)。v1 は `BINDINGS` 定数を
   配線ジェネレータの引数として外から渡していた(D1)
2. `primitive` が実態どおり `passthrough` になった。v1 は3件すべて `exact_match` と
   宣言していたが、決定表は生の区分値を条件にしており実態と合っていなかった(D3)
3. `possible_values` から条文にない値が消えた。v1 は「運用担当」「その他」「検証環境」
   「開発環境」「一般」を含んでいたが、いずれも条文に書かれていない(D4・設計書1.4)

あわせて `target`(判定先)を IR に持たせ(D7)、レベル判定を層ごとの型にした(D6)。
`DEFINITIONS_ID`／`DEFINITIONS_NAME`／`NAMESPACE` 定数は廃止し、配線ジェネレータが
`target.dmn_model` から導出する。

## 決定表

| position | operator_role | target_environment | requested_privilege | → decision | reason_code |
|---|---|---|---|---|---|
| 1 | システム開発担当 | 本番環境 | 特権 | 禁止 | dev_privileged_op_on_production |
| 2 | - | - | - | 許可 | not_prohibited_combination |
"""
from __future__ import annotations

from datetime import datetime, timezone

from .ir_schema import (
    ActionRef,
    AtomicFactLayer,
    AxisResult,
    ClauseIR,
    CompoundFactApplication,
    DecisionTarget,
    FactDef,
    FactUsage,
    FinalLayer,
    LevelDetermination,
    NewDecisionTableDraft,
    Provenance,
    RuleRowDraft,
    SourceRef,
    build_review_gates,
)

CLAUSE_ID = "ipa_secguide_v4_6_7_1"
CLAUSE_REF = "IPA情報セキュリティ関連規程 6.7 特権アカウント(第3項)"

DMN_MODEL = "privileged_operation"
DECISION_ID = "decision_privileged_operation"
DECISION_NAME = "特権操作可否判定"
DECISION_TABLE_ID = "dt_privileged_operation"

# D22: knowledge_sources には、条文または出典付きの context に根拠があるものだけを
# 書く。v1 にあった ks_privileged_account_registry は、これを参照していた
# exact_match の registered 引数が D3 で消えたため根拠が無くなり、削除した。
KS_ACTION_REQUEST = "ks_action_request"

INPUT_COLUMNS = ["operator_role", "target_environment", "requested_privilege"]


def _new_facts(approved_by: str | None = None) -> list[FactDef]:
    """この条文で新しく定義する fact(設計書3.8)。

    3つとも「区分の値をそのまま渡す」用途なので primitive は passthrough、
    sources の引数名は `value` 1つ(設計書3.5)。possible_values は条文に
    出てくる値だけに限る(D4)。

    `approved_by` は新しい fact の語彙を承認した人(D14)。既定は None で、
    `new_fact_unapproved` ゲートが立つ。承認後は名前を入れるとゲートが下りる。
    """
    return [
        FactDef(
            variable="operator_role",
            primitive="passthrough",
            possible_values=["システム開発担当"],
            sources={
                "value": SourceRef(from_="action_request", path="operator.role"),
            },
            approved_by=approved_by,
        ),
        FactDef(
            variable="target_environment",
            primitive="passthrough",
            possible_values=["本番環境"],
            sources={
                "value": SourceRef(from_="action_request", path="target.environment"),
            },
            approved_by=approved_by,
        ),
        FactDef(
            variable="requested_privilege",
            primitive="passthrough",
            possible_values=["特権"],
            sources={
                "value": SourceRef(from_="action_request", path="requested_privilege"),
            },
            approved_by=approved_by,
        ),
    ]


def build_golden_path_ir(
    axis1_result: AxisResult,
    axis2_result: AxisResult,
    *,
    clause_text: str,
    action_approved_by: str | None = None,
    facts_approved_by: str | None = None,
    registered_by: str = "claude_code",
    registered_at: str | None = None,
) -> ClauseIR:
    """ゴールデンパス条文のIR v2 を構築する。

    axis1_result / axis2_result は②の分類器の実出力を渡す
    (ハードコードせず、実際の判定結果をIRに反映させる)。

    `action_approved_by` / `facts_approved_by` に名前を入れると、それぞれ
    `new_action_unapproved` / `new_fact_unapproved` のゲートが下りる(D14)。
    既定はどちらも None で、2つのゲートが立った状態になる。
    """
    action = ActionRef(
        surface="特権/管理者権限で操作を行う",
        normalized="特権操作",
        parent="システム操作",
        is_new=True,
        approved_by=action_approved_by,
    )

    facts = FactUsage(new_facts=_new_facts(facts_approved_by))

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
        # D38: depends_on_decisions・depends_on_inputs は input_columns から導出する
        # ので手で書かない
        input_columns=list(INPUT_COLUMNS),
        rule_rows=rule_rows,
        knowledge_sources=[KS_ACTION_REQUEST],
    )

    level_determination = LevelDetermination(
        atomic_fact=AtomicFactLayer(covered=True, new_primitives=[]),
        intermediate=[],
        final=FinalLayer(
            compared_table_id=None,
            relation=None,
            action="new_table",
        ),
    )

    compound_fact = CompoundFactApplication(
        # D31: どの案を採ったかは手で書かず、IR から導出する
        # (この条文では dt_privileged_operation が new_table_row になる)。
        # 手で書くのは「一致モジュール側で AND を組もうとして拒否された」印だけ。
        rejected_in_primitive=False,
        rationale=(
            "「開発担当者 かつ 本番環境 かつ 特権権限」という複合条件は、"
            "一致プリミティブ側でANDを組まず、新しく作る決定表の1行(id_r_priv_1)の"
            "3列の条件として表現する(複合fact禁止原則)。"
        ),
    )

    provenance = Provenance(
        origin="regulation_clause",
        clause_ref=CLAUSE_REF,
        registered_by=registered_by,
        registered_at=registered_at or datetime.now(timezone.utc).isoformat(),
    )

    # D20: level_determination を渡すと new_primitive_required も判定する。
    # この条文は covered=True なので立たない。
    review_gates = build_review_gates(
        axis1_result, axis2_result, action, facts, level_determination
    )

    return ClauseIR(
        clause_ref=CLAUSE_REF,
        clause_text=clause_text,
        axis1_result=axis1_result,
        axis2_result=axis2_result,
        actor="あおい",
        action=action,
        scope="情報システムの運用操作",
        target=DecisionTarget(dmn_model=DMN_MODEL, entry_decision_id=DECISION_ID),
        facts=facts,
        level_determination=level_determination,
        rule_row_additions=[],
        new_decision_tables=[new_table],
        compound_fact_application=compound_fact,
        provenance=provenance,
        review_gates=review_gates,
        human_review_status="pending",
    )
