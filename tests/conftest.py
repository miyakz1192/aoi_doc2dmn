"""テスト共通のフィクスチャ。

クロス検証 C1〜C11 の失敗テストには2種類の IR が必要になる。

1. **ゴールデンパス相当の単純な IR**(`simple_ir`): 新規決定表1枚、fact 2件、
   登録簿は空。C1〜C5・C7・C9条件1・C9条件5 を破るのに使う
2. **中間判定を使う IR**(`intermediate_ir` と `seeded_registry`): 既存の中間判定を
   参照する最終判定。ゴールデンパスには登録簿のエントリ・ドット参照・`value_additions` が
   無いため、C6・C9条件3・C9条件4・C10・C11 はこちらでしか破れない

**C6 の基準は IR が書いた行と表である**(D25)。そのため `intermediate_ir` の
`referenced_facts` は空になる(入力列がドット参照1列だけで、fact の列を使っていない)。
参照先の中間判定が使う fact の不足は C1 が捕まえる(D26)。

2 の登録簿はテスト内だけで組み立てる(永続台帳 `registry/registry.json` には書き込まない)。
D17(中間判定を参照する最終判定は同じ DMN モデルに入れる)に合わせて、`seeded_registry` と
`intermediate_ir` は同じ `dmn_model` を使う。

**あおいの初期登録した登録簿を使うテスト**(レベル2・中間判定への行追加・add_column)は
`tests/test_acceptance_paths.py` にあり、`registry/registry.json` と
`registry/dmn/aoi_transmission.dmn` を読み込む。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_generate.ir_schema import (  # noqa: E402
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
    ValueAddition,
    build_review_gates,
)
from src.stage3_generate.registry import Registry  # noqa: E402

FIXED_TIMESTAMP = "2026-10-22T00:00:00+00:00"


def make_axis(label: str) -> AxisResult:
    """ゲートが立たない(クロス検証に影響しない)軸の結果。"""
    return AxisResult(
        label=label,
        confidence=1.0,
        rationale="test",
        evidence_quote="test",
        quote_verified=True,
        vocab_flagged=False,
    )


def make_provenance(clause_ref: str) -> Provenance:
    return Provenance(
        origin="regulation_clause",
        clause_ref=clause_ref,
        registered_by="pytest",
        registered_at=FIXED_TIMESTAMP,
    )


def passthrough_fact(variable: str, values: list[str], path: str) -> FactDef:
    return FactDef(
        variable=variable,
        primitive="passthrough",
        possible_values=list(values),
        sources={"value": SourceRef(from_="action_request", path=path)},
    )


def _assemble(
    *,
    clause_ref: str,
    target: DecisionTarget,
    facts: FactUsage,
    level: LevelDetermination,
    new_tables: list[NewDecisionTableDraft],
    row_additions: list[RuleRowDraft],
) -> ClauseIR:
    axis1 = make_axis("ハード制約")
    axis2 = make_axis("宣言的")
    action = ActionRef(
        surface="操作を行う",
        normalized="テスト操作",
        parent="システム操作",
        is_new=True,
        approved_by="pytest",
    )
    return ClauseIR(
        clause_ref=clause_ref,
        clause_text="テスト用の条文。",
        axis1_result=axis1,
        axis2_result=axis2,
        actor="あおい",
        action=action,
        scope="テスト",
        target=target,
        facts=facts,
        level_determination=level,
        rule_row_additions=row_additions,
        new_decision_tables=new_tables,
        compound_fact_application=CompoundFactApplication(
            # D31: 手で書くのは拒否の印だけ。option_taken は IR から導出する
            rejected_in_primitive=False,
            rationale="テスト",
        ),
        provenance=make_provenance(clause_ref),
        review_gates=build_review_gates(axis1, axis2, action, facts, level),
    )


@pytest.fixture
def empty_registry() -> Registry:
    return Registry.empty()


@pytest.fixture
def simple_ir() -> ClauseIR:
    """新規決定表1枚・fact 2件・登録簿なしの最小 IR(C1〜C5・C7・C9 用)。"""
    table = NewDecisionTableDraft(
        decision_table_id="dt_simple",
        decision_id="decision_simple",
        decision_name="単純判定",
        depends_on_decisions=[],
        depends_on_inputs=["role", "environment"],
        input_columns=["role", "environment"],
        rule_rows=[
            RuleRowDraft(
                row_id="id_simple_1",
                decision_table_id="dt_simple",
                position=1,
                conditions={"role": "開発担当", "environment": "本番"},
                outputs={"decision": "禁止", "reason_code": "prohibited"},
            ),
            RuleRowDraft(
                row_id="id_simple_2",
                decision_table_id="dt_simple",
                position=2,
                conditions={"role": "-", "environment": "-"},
                outputs={"decision": "許可", "reason_code": "allowed"},
            ),
        ],
    )
    return _assemble(
        clause_ref="テスト規程 第1条",
        target=DecisionTarget(dmn_model="simple", entry_decision_id="decision_simple"),
        facts=FactUsage(
            new_facts=[
                passthrough_fact("role", ["開発担当"], "operator.role"),
                passthrough_fact("environment", ["本番"], "target.environment"),
            ]
        ),
        level=LevelDetermination(
            atomic_fact=AtomicFactLayer(covered=True),
            intermediate=[],
            final=FinalLayer(compared_table_id=None, relation=None, action="new_table"),
        ),
        new_tables=[table],
        row_additions=[],
    )


@pytest.fixture
def seeded_registry() -> Registry:
    """中間判定(本人確認判定)を1枚持つ登録簿。

    層1に決定表(`output_columns`・`entry_points` 込み)、層2にその行、層3にその
    決定表が使う fact 2件を置く。C10 は層1の `output_columns` を、C11 は層2の行の
    出力値を見る(D12・D13)。

    `entry_points` が空なので、この決定表は中間判定である(設計書4.5 層1)。
    """
    provenance = make_provenance("既存規程 第0条").to_json()
    return Registry(
        decision_tables=[
            {
                "decision_table_id": "dt_identity",
                "decision_id": "decision_identity",
                "decision_name": "本人確認判定",
                "dmn_model": "aoi_transmission",
                "entry_points": [],
                "hit_policy": "FIRST",
                "input_columns": ["birth_match", "phone_match"],
                "output_columns": ["identity_level"],
                "depends_on_decisions": [],
                "depends_on_inputs": ["birth_match", "phone_match"],
                "knowledge_sources": [],
                "provenance": provenance,
            }
        ],
        rule_rows=[
            {
                "row_id": "id_identity_1",
                "decision_table_id": "dt_identity",
                "position": 1,
                "conditions": {"birth_match": "一致", "phone_match": "一致"},
                "outputs": {"identity_level": "高"},
                "provenance": provenance,
                "history": [],
            }
        ],
        fact_registry=[
            {
                **passthrough_fact("birth_match", ["一致", "不一致"], "patient.birth").to_json(),
                "defined_by": "既存規程 第0条",
                "used_by": ["dt_identity"],
                "provenance": provenance,
                "history": [],
            },
            {
                **passthrough_fact("phone_match", ["一致", "不一致"], "patient.phone").to_json(),
                "defined_by": "既存規程 第0条",
                "used_by": ["dt_identity"],
                "provenance": provenance,
                "history": [],
            },
        ],
    )


@pytest.fixture
def intermediate_ir() -> ClauseIR:
    """既存の中間判定(本人確認判定)の出力を参照する最終判定の IR。

    `seeded_registry` と組み合わせて使う。required_facts は依存をたどって
    {birth_match, phone_match} になる(入力列はドット参照1列だけ)。

    D17 により、参照先(本人確認判定)と同じ `dmn_model` に入れる。

    `referenced_facts` が空なのは D25 のため。この IR が書いた表の入力列は
    ドット参照1列だけで、fact の列を1つも使っていない。birth_match・phone_match は
    参照先の中間判定が使う fact であり、「IR が使う既存 fact」ではない
    (この2件の不足を捕まえるのは C1 の役目。D26)。
    """
    table = NewDecisionTableDraft(
        decision_table_id="dt_verbal",
        decision_id="decision_verbal",
        decision_name="口頭伝達可否判定",
        depends_on_decisions=["decision_identity"],
        depends_on_inputs=[],
        input_columns=["本人確認判定.identity_level"],
        rule_rows=[
            RuleRowDraft(
                row_id="id_verbal_1",
                decision_table_id="dt_verbal",
                position=1,
                conditions={"本人確認判定.identity_level": '"高"'},
                outputs={"decision": "許可", "reason_code": "identity_ok"},
            ),
            RuleRowDraft(
                row_id="id_verbal_2",
                decision_table_id="dt_verbal",
                position=2,
                conditions={"本人確認判定.identity_level": "-"},
                outputs={"decision": "禁止", "reason_code": "identity_insufficient"},
            ),
        ],
    )
    return _assemble(
        clause_ref="情報管理規程 第14条",
        target=DecisionTarget(
            dmn_model="aoi_transmission", entry_decision_id="decision_verbal"
        ),
        facts=FactUsage(referenced_facts=[]),
        level=LevelDetermination(
            atomic_fact=AtomicFactLayer(covered=True),
            intermediate=[],
            final=FinalLayer(
                compared_table_id="dt_identity",
                relation="proper_subset",
                action="new_table",
            ),
        ),
        new_tables=[table],
        row_additions=[],
    )


@pytest.fixture
def value_addition():
    """`ValueAddition` を作る小さなヘルパー(承認済みの既定)。"""
    return lambda value, approved_by="pytest": ValueAddition(
        value=value, approved_by=approved_by
    )
