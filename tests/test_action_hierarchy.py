"""Action 台帳の2段化と禁止の継承の失敗テスト(IR v2 設計書 v1.9 の D73〜D76)。

第31回 HANDOFF 2章 タスクA3 が挙げた3つを押さえる。

1. 抽象的な Action の行が作られない(D76)
2. 継承する判定の事実が required_facts から漏れたら気付ける(D75・C8)
3. 合成で継承側の許可が無視される(D74)

あわせて、階層のたどり方(環があっても止まる)と、子孫の行がまだ無い抽象的な Action の
扱いも確かめる。
"""
from __future__ import annotations

import dataclasses

import pytest

from tests.conftest import make_provenance, passthrough_fact

from src.stage3_generate.registry import (
    ACTION_ABSTRACT,
    ACTION_CONCRETE,
    Registry,
    action_ancestors,
    action_descendants,
)
from src.stage3_generate.scenario_eval import (
    ESCALATE_DECISION,
    compose_decisions,
    evaluate_route,
)
from src.stage3_generate.wiring_generator import (
    SchemaWiringError,
    _check_c8_routing_matches_bindings,
    derive_routes,
    generate_wiring_bundle,
)

LEDGER = [
    {"name": "情報提供", "kind": ACTION_ABSTRACT, "parent": None},
    {"name": "口頭伝達", "kind": ACTION_CONCRETE, "parent": "情報提供"},
    {"name": "データ送信", "kind": ACTION_CONCRETE, "parent": "情報提供"},
]


@pytest.fixture
def child_registry() -> Registry:
    """子の具体的な行為(口頭伝達)の判定を層1に持つ登録簿。

    `entry_points` が「あおい × 口頭伝達」なので、これが D76 で作り直す対象の行に
    なる。データ送信は台帳にあるが判定が無いので、行も作られない。
    """
    provenance = make_provenance("既存規程 第0条").to_json()
    return Registry(
        decision_tables=[
            {
                "decision_table_id": "dt_verbal",
                "decision_id": "decision_verbal",
                "decision_name": "口頭伝達可否判定",
                "dmn_model": "verbal_disclosure",
                "entry_points": [{"actor": "あおい", "action": "口頭伝達"}],
                "hit_policy": "FIRST",
                "input_columns": ["birth_match"],
                "output_columns": ["decision", "reason_code"],
                "depends_on_decisions": [],
                "depends_on_inputs": ["birth_match"],
                "knowledge_sources": [],
                "provenance": provenance,
            }
        ],
        rule_rows=[
            {
                "row_id": "id_verbal_1",
                "decision_table_id": "dt_verbal",
                "position": 1,
                "conditions": {"birth_match": "一致"},
                "outputs": {"decision": "許可", "reason_code": "ok"},
                "provenance": provenance,
                "history": [],
            }
        ],
        fact_registry=[
            {
                **passthrough_fact("birth_match", ["一致", "不一致"], "patient.birth").to_json(),
                "defined_by": "既存規程 第0条",
                "used_by": ["dt_verbal"],
                "provenance": provenance,
                "history": [],
            }
        ],
        action_ledger=[dict(e) for e in LEDGER],
    )


def _with_action(ir, name: str):
    return dataclasses.replace(
        ir, action=dataclasses.replace(ir.action, normalized=name)
    )


# --------------------------------------------------------------------------
# D76: 抽象的な Action は自分の行を作らない
# --------------------------------------------------------------------------


def test_abstract_action_makes_no_route_of_its_own(simple_ir, child_registry):
    """抽象的な Action(情報提供)の IR は、自分の行を作らず子孫の行を作り直す。"""
    ir = _with_action(simple_ir, "情報提供")
    routes = derive_routes(ir, child_registry)
    assert [(r["actor"], r["action"]) for r in routes] == [("あおい", "口頭伝達")]
    # 自分の判定は、子の行の「継承する判定」として入る(D75)
    assert routes[0]["inherited_decisions"] == [
        {"dmn_model": "simple", "decision_id": "decision_simple"}
    ]


def test_concrete_action_keeps_its_own_route(simple_ir, child_registry):
    """具体的な Action なら自分の行を作る(段が concrete のときは v1.8 と同じ)。"""
    ir = _with_action(simple_ir, "口頭伝達")
    routes = derive_routes(ir, child_registry)
    assert [(r["actor"], r["action"]) for r in routes] == [("あおい", "口頭伝達")]
    assert routes[0]["origin"] == "target"
    # 親(情報提供)の判定は登録簿に無いので、継承する判定は空になる
    assert routes[0]["inherited_decisions"] == []


def test_abstract_action_without_any_descendant_row_still_declares_its_facts(
    simple_ir, empty_registry
):
    """子孫の行がまだ無い抽象的な Action でも、自分の事実はバインディング設定に出る。

    子の具体的な行為の条文がこれから来る場合、ルーティング表の行は作れない。その
    ときに事実まで落とすと、あとで子の行を作り直すときに何を集めるのか分からなく
    なる。C8 はこの差分だけを許す。
    """
    registry = empty_registry.copy()
    registry.action_ledger = [{"name": "テスト操作", "kind": ACTION_ABSTRACT}]
    bundle = generate_wiring_bundle(simple_ir, registry=registry)
    assert bundle.routing_table["routes"] == []
    assert [f["variable"] for f in bundle.bindings["facts"]] == ["role", "environment"]


# --------------------------------------------------------------------------
# D75: 継承する判定の事実
# --------------------------------------------------------------------------


def test_inherited_decision_facts_are_collected(simple_ir, child_registry):
    """子の行の required_facts に、継承する判定の事実が入る(D75)。"""
    ir = _with_action(simple_ir, "情報提供")
    bundle = generate_wiring_bundle(ir, registry=child_registry)
    route = bundle.routing_table["routes"][0]
    # 自分の判定の birth_match と、継承する判定(この IR)の role・environment
    assert route["required_facts"] == ["birth_match", "role", "environment"]
    assert [f["variable"] for f in bundle.bindings["facts"]] == [
        "birth_match",
        "role",
        "environment",
    ]


def test_c8_catches_an_inherited_fact_missing_from_the_bindings():
    """継承する判定の事実がバインディング設定から漏れたら C8 が止める(D75)。"""
    routing = {
        "routes": [
            {
                "actor": "あおい",
                "action": "口頭伝達",
                "dmn_model": "verbal_disclosure",
                "decision_id": "decision_verbal",
                "inherited_decisions": [
                    {"dmn_model": "simple", "decision_id": "decision_simple"}
                ],
                "required_facts": ["birth_match", "role"],
            }
        ]
    }
    bindings = {"dmn_model": "verbal_disclosure", "facts": [{"variable": "birth_match"}]}
    with pytest.raises(SchemaWiringError, match="C8検証失敗"):
        _check_c8_routing_matches_bindings(routing, bindings)


# --------------------------------------------------------------------------
# D74: 合成の規則
# --------------------------------------------------------------------------


def test_composition_ignores_the_parents_permission():
    """継承側の「許可」は取り入れない(D74・D60)。"""
    own = {"decision": "禁止", "reason_code": "own_ng"}
    inherited = [{"decision_id": "d_parent", "result": {"decision": "許可"}}]
    out = compose_decisions(own, inherited)
    assert out["decision"] == "禁止"
    assert out["_composed_from"] == "own"
    assert out["_evaluated"][1]["taken"] is False


def test_composition_takes_the_parents_prohibition():
    """自分が許可でも、継承した禁止が勝つ(D74)。"""
    own = {"decision": "許可", "reason_code": "own_ok"}
    inherited = [
        {"decision_id": "d_parent", "result": {"decision": "禁止", "reason_code": "p_ng"}}
    ]
    out = compose_decisions(own, inherited)
    assert (out["decision"], out["reason_code"]) == ("禁止", "p_ng")
    assert out["_composed_from"] == "d_parent"


def test_composition_prefers_the_strictest_of_three():
    """禁止 ＞ 条件付き許可 ＞ 許可 の順で最も厳しいものを採る(D75)。"""
    own = {"decision": "条件付き許可", "reason_code": "own_cond"}
    inherited = [
        {"decision_id": "d_a", "result": {"decision": "許可"}},
        {"decision_id": "d_b", "result": {"decision": "禁止", "reason_code": "b_ng"}},
    ]
    assert compose_decisions(own, inherited)["reason_code"] == "b_ng"
    # 継承側が条件付き許可だけなら、自分の条件付き許可が残る
    only_conditional = [
        {"decision_id": "d_a", "result": {"decision": "条件付き許可", "reason_code": "a"}}
    ]
    assert compose_decisions(own, only_conditional)["decision"] == "条件付き許可"


def test_composition_escalates_when_any_decision_escalates():
    """知らない値でエスカレーションになった判定があれば、人の確認に回す(D46)。"""
    own = {"decision": "許可"}
    inherited = [
        {
            "decision_id": "d_parent",
            "result": {"decision": ESCALATE_DECISION, "reason_code": "unknown_fact_value"},
        }
    ]
    assert compose_decisions(own, inherited)["decision"] == ESCALATE_DECISION


def test_composition_without_own_decision_is_permitted_when_nothing_restricts():
    """自分の判定が無く、継承側も制限しないなら許可になる。"""
    out = compose_decisions(None, [{"decision_id": "d", "result": {"decision": "許可"}}])
    assert (out["decision"], out["reason_code"]) == ("許可", "no_restriction")


def test_evaluate_route_evaluates_both_models(simple_ir, child_registry, tmp_path):
    """evaluate_route が自分の判定と継承する判定の両方を評価する(D75)。"""
    from src.stage3_generate.dmn_engine import DmnModel
    from src.stage3_generate.registry import dmn_path

    ir = _with_action(simple_ir, "情報提供")
    bundle = generate_wiring_bundle(ir, registry=child_registry)
    # 親(この IR)の DMN を書き出す。子の判定の DMN はテスト用に同じ内容で代用する
    # (継承の合成を見るので、子の結論は許可で十分)
    parent = dmn_path("simple", tmp_path)
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_text(bundle.dmn_xml, encoding="utf-8")

    class _AlwaysAllow:
        def evaluate(self, decision_id, facts):
            return {"decision": "許可", "reason_code": "child_ok"}

    def model_of(dmn_model):
        if dmn_model == "simple":
            return DmnModel(str(parent))
        return _AlwaysAllow()

    route = bundle.routing_table["routes"][0]
    out = evaluate_route(
        route,
        {"birth_match": "一致", "role": "開発担当", "environment": "本番"},
        model_of=model_of,
        bindings=bundle.bindings,
    )
    # 子は許可だが、親(simple_ir の表)が禁止を返すので禁止になる
    assert out["decision"] == "禁止"
    assert out["_composed_from"] == "decision_simple"


# --------------------------------------------------------------------------
# 階層のたどり方
# --------------------------------------------------------------------------


def test_ancestors_and_descendants_follow_the_ledger():
    assert action_ancestors(LEDGER, "口頭伝達") == ["情報提供"]
    assert action_descendants(LEDGER, "情報提供") == ["口頭伝達", "データ送信"]
    assert action_descendants(LEDGER, "口頭伝達") == []


def test_ancestors_stops_on_a_cycle():
    """親子に環があっても止まる(手の記入の誤りで無限に回らない)。"""
    looped = [
        {"name": "A", "kind": ACTION_CONCRETE, "parent": "B"},
        {"name": "B", "kind": ACTION_ABSTRACT, "parent": "A"},
    ]
    assert action_ancestors(looped, "A") == ["B"]
    assert action_descendants(looped, "A") == ["B"]
