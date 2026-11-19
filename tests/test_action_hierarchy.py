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

from src.stage3_generate.ir_schema import RuleRowDraft
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
    strictness,
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


def test_composition_escalates_only_when_nothing_prohibits():
    """知らない値のエスカレーションは、禁止が無いときだけ結論になる(D46・D77)。

    v1.9 の実装はエスカレーションを禁止より上に置いていたので、継承した禁止が
    エスカレーションに塗り替えられていた。D77 で禁止を最上位にした。
    """
    own = {"decision": "許可"}
    escalating = {
        "decision_id": "d_parent",
        "result": {"decision": ESCALATE_DECISION, "reason_code": "unknown_fact_value"},
    }
    assert compose_decisions(own, [escalating])["decision"] == ESCALATE_DECISION
    # 禁止があれば禁止が勝つ
    prohibiting = {"decision_id": "d_other", "result": {"decision": "禁止", "reason_code": "ng"}}
    assert compose_decisions(own, [escalating, prohibiting])["decision"] == "禁止"


def test_composition_prefers_prohibition_over_own_escalation():
    """自分がエスカレーション、継承が禁止 → 禁止(D77)。"""
    own = {"decision": ESCALATE_DECISION, "reason_code": "unknown_fact_value"}
    inherited = [{"decision_id": "d_parent", "result": {"decision": "禁止", "reason_code": "p_ng"}}]
    out = compose_decisions(own, inherited)
    assert (out["decision"], out["reason_code"], out["_composed_from"]) == (
        "禁止",
        "p_ng",
        "d_parent",
    )


def test_composition_prefers_escalation_over_own_conditional_permission():
    """自分が条件付き許可、継承がエスカレーション → エスカレーション(D77)。"""
    own = {"decision": "条件付き許可", "reason_code": "own_cond"}
    inherited = [
        {
            "decision_id": "d_parent",
            "result": {"decision": ESCALATE_DECISION, "reason_code": "unknown_fact_value"},
        }
    ]
    assert compose_decisions(own, inherited)["decision"] == ESCALATE_DECISION


def test_composition_treats_an_unknown_decision_as_escalation():
    """知らない結論はエスカレーションと同じ厳しさで、禁止には負ける(D77)。"""
    assert strictness("なんだこれ") == strictness(ESCALATE_DECISION)
    own = {"decision": "なんだこれ", "reason_code": "weird"}
    inherited = [{"decision_id": "d_parent", "result": {"decision": "禁止", "reason_code": "p_ng"}}]
    assert compose_decisions(own, inherited)["decision"] == "禁止"
    # 条件付き許可よりは厳しい(自分の側が知らない結論の場合)
    cond = [{"decision_id": "d_parent", "result": {"decision": "条件付き許可"}}]
    assert compose_decisions(own, cond)["decision"] == "なんだこれ"
    # 継承側の知らない結論は取り入れない(INHERITED_RESTRICTIVE は D74 のまま)
    weird = [{"decision_id": "d_parent", "result": {"decision": "なんだこれ"}}]
    assert compose_decisions({"decision": "許可"}, weird)["decision"] == "許可"


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


# --------------------------------------------------------------------------
# D79: v1.9 の実装判断の追認(第33回タスクA3)
# --------------------------------------------------------------------------


def test_descendant_rows_are_exempt_from_the_d17_check(simple_ir, child_registry):
    """D79 (a): 作り直した子孫の行は、IR とは別の DMN モデルにあってよい。

    D17(1つの IR は1つのモデルだけを触る)をそのまま当てると、子の判定が別の
    モデルにある普通の場合に継承が成立しない。例外にするのは `origin` が
    `descendant` の行だけで、自分の行(`target`)には今までどおり当てる。
    """
    ir = _with_action(simple_ir, "情報提供")
    routes = derive_routes(ir, child_registry)
    assert routes[0]["origin"] == "descendant"
    # 子の行のモデルは IR の target のモデルと違う
    assert routes[0]["dmn_model"] == "verbal_disclosure"
    assert ir.target.dmn_model == "simple"


def test_only_descendant_rows_are_exempt_from_d17(simple_ir, child_registry):
    """D79 (a): 例外は `origin == "descendant"` の行だけであること。

    自分の行(`target`)は定義上 IR の target.dmn_model になるので検査にかからない。
    検査が効くのは、IR が直した既存の表から作り直す行(`dependent`)である。段を
    concrete にして同じ登録簿を通すと、子孫の行が作られなくなり D17 で止まる。
    """
    registry = child_registry.copy()
    # 情報提供を具体的な行為にしてしまうと、子孫の行の作り直しが起きない
    registry.action_ledger = [
        {"name": "情報提供", "kind": ACTION_CONCRETE, "parent": None},
        {"name": "口頭伝達", "kind": ACTION_CONCRETE, "parent": "情報提供"},
    ]
    ir = _with_action(simple_ir, "情報提供")
    routes = derive_routes(ir, registry)
    assert [r["origin"] for r in routes] == ["target"]
    assert routes[0]["dmn_model"] == ir.target.dmn_model

    # 一方、IR が直した既存の表から作り直す行(`dependent`)が別のモデルにあると、
    # 今までどおり D17 で止まる
    ir = dataclasses.replace(
        _with_action(simple_ir, "情報提供"),
        rule_row_additions=[
            RuleRowDraft(
                row_id="id_verbal_2",
                decision_table_id="dt_verbal",
                position=2,
                conditions={"birth_match": "不一致"},
                outputs={"decision": "禁止", "reason_code": "ng"},
            )
        ],
    )
    with pytest.raises(SchemaWiringError, match="D17違反"):
        derive_routes(ir, registry)


def test_layer1_entry_points_also_carry_an_abstract_actions_decision(simple_ir, child_registry):
    """D79 (b): 層1の entry_points は抽象的な Action の判定も持つ。

    行を作らないのはルーティング表の段階だけで、「Action と判定の対応」としては
    抽象的な Action も層1に載る。載っていなければ、子の行は継承する判定を引けない。
    """
    registry = child_registry.copy()
    provenance = make_provenance("既存規程 第0条").to_json()
    registry.decision_tables = list(registry.decision_tables) + [
        {
            "decision_table_id": "dt_provision",
            "decision_id": "decision_provision",
            "decision_name": "情報提供可否判定",
            "dmn_model": "information_provision",
            # 抽象的な Action の entry_point
            "entry_points": [{"actor": "あおい", "action": "情報提供"}],
            "hit_policy": "FIRST",
            "input_columns": [],
            "output_columns": ["decision", "reason_code"],
            "depends_on_decisions": [],
            "depends_on_inputs": [],
            "knowledge_sources": [],
            "provenance": provenance,
        }
    ]
    assert registry.entry_points_of_action("情報提供") == [
        {
            "actor": "あおい",
            "action": "情報提供",
            "dmn_model": "information_provision",
            "decision_id": "decision_provision",
        }
    ]
    # 子(口頭伝達)の行が、その判定を継承する判定として引く
    routes = derive_routes(_with_action(simple_ir, "口頭伝達"), registry)
    assert routes[0]["inherited_decisions"] == [
        {"dmn_model": "information_provision", "decision_id": "decision_provision"}
    ]


def test_c8_allows_the_own_facts_of_an_ir_that_makes_no_row():
    """D79 (c): C8 は非対称。行を作らない IR の自分の事実は余分として許す。"""
    routing = {"routes": []}
    bindings = {"dmn_model": "simple", "facts": [{"variable": "role"}]}
    # 余分な事実を extra_facts として渡せば通る(行が無い IR の自分の事実)
    _check_c8_routing_matches_bindings(routing, bindings, extra_facts={"role"})
    # 渡さなければ止まる(バインディング設定にだけある事実は普通は誤り)
    with pytest.raises(SchemaWiringError, match="C8検証失敗"):
        _check_c8_routing_matches_bindings(routing, bindings)


def test_a_gold_ir_with_inherited_decisions_needs_a_composition_scenario(tmp_path):
    """D79 (e): 継承する判定を持つ正解IR には composition のシナリオを要求する。

    正解IR の一式を一時ディレクトリに写し、第14条から `"composition": true` の
    シナリオだけを外して全件を通す。継承が効くことを確かめるシナリオが無ければ、
    その1件が失敗になる。
    """
    import json
    import shutil

    from src.stage3_generate.run_ir_gold import GOLD_DIR_DEFAULT, run_all

    gold_dir = tmp_path / "ir_gold"
    shutil.copytree(GOLD_DIR_DEFAULT, gold_dir)
    path = gold_dir / "aoi_johokanri_art14.json"
    gold = json.loads(path.read_text(encoding="utf-8"))
    assert any(s.get("composition") for s in gold["scenarios"])
    gold["scenarios"] = [s for s in gold["scenarios"] if not s.get("composition")]
    path.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")

    results = {r.gold_id: r for r in run_all(gold_dir=gold_dir, verbose=False)}
    problems = results["aoi_johokanri_art14"].problems
    assert any("composition" in p for p in problems), problems
    # ほかの正解IR は通ったまま
    assert not results["ipa_secguide_v4_6_7_1"].problems


# --------------------------------------------------------------------------
# D81: 1つの actor × action に複数の判定
# --------------------------------------------------------------------------


class _TableByTarget:
    """対象(usage_target)が自分の対象なら規則を当て、外なら既定の行(許可)に落ちる表。

    D81 の「各判定の表は、自分の対象の外の要求を『その他』として既定の行に落とす」を
    DMN を使わずに再現する。
    """

    def __init__(self, decision_id, target, decision, reason):
        self.decision_id, self.target = decision_id, target
        self.decision, self.reason = decision, reason

    def evaluate(self, decision_id, facts):
        assert decision_id == self.decision_id
        if facts.get("usage_target") == self.target:
            return {"decision": self.decision, "reason_code": self.reason}
        return {"decision": "許可", "reason_code": "out_of_scope"}


def _two_tables_on_one_action():
    """「使用」に、施設・物品の表と社外 Wi-Fi の表が付いた状態(D81 の例)。"""
    tables = {
        "facility_use": _TableByTarget("decision_facility", "会社の物品", "条件付き許可", "need_permission"),
        "wifi_use": _TableByTarget("decision_wifi", "社外Wi-Fi", "禁止", "wifi_ng"),
    }
    routes = [
        {"actor": "あおい", "action": "使用", "dmn_model": "facility_use",
         "decision_id": "decision_facility", "inherited_decisions": []},
        {"actor": "あおい", "action": "使用", "dmn_model": "wifi_use",
         "decision_id": "decision_wifi", "inherited_decisions": []},
        {"actor": "あおい", "action": "送信", "dmn_model": "other",
         "decision_id": "decision_other", "inherited_decisions": []},
    ]
    return routes, tables.__getitem__


def test_one_action_can_carry_several_decisions():
    """D81: 同じ actor × action の行を判定ごとに集め、すべて評価して合成する。"""
    from src.stage3_generate.scenario_eval import evaluate_request, routes_for_request

    routes, model_of = _two_tables_on_one_action()
    assert [r["decision_id"] for r in routes_for_request(routes, "あおい", "使用")] == [
        "decision_facility",
        "decision_wifi",
    ]
    # 社外 Wi-Fi の利用: 施設の表は対象外で許可、Wi-Fi の表が禁止 → 禁止
    out = evaluate_request(
        routes, "あおい", "使用", {"usage_target": "社外Wi-Fi"}, model_of=model_of
    )
    assert (out["decision"], out["reason_code"]) == ("禁止", "wifi_ng")
    assert out["_composed_from"] == "decision_wifi"
    assert out["_routes"] == 2
    # 会社の物品の使用: Wi-Fi の表は対象外で許可、施設の表が条件付き許可 → 条件付き許可
    out = evaluate_request(
        routes, "あおい", "使用", {"usage_target": "会社の物品"}, model_of=model_of
    )
    assert (out["decision"], out["reason_code"]) == ("条件付き許可", "need_permission")
    # どちらの対象でもない: 両方とも既定の行の許可
    out = evaluate_request(
        routes, "あおい", "使用", {"usage_target": "その他"}, model_of=model_of
    )
    assert out["decision"] == "許可"
    assert [e["decision"] for e in out["_evaluated"]] == ["許可", "許可"]


def test_several_decisions_compose_with_the_inherited_ones():
    """D81: 複数の自分の判定と、継承する判定を一緒に D77 の順序で合成する。

    継承する判定は、同じ祖先の判定が複数の行に付いていても1回だけ評価する。
    """
    from src.stage3_generate.scenario_eval import evaluate_request

    routes, base_model_of = _two_tables_on_one_action()
    parent = {"dmn_model": "parent", "decision_id": "decision_parent"}
    for route in routes[:2]:
        route["inherited_decisions"] = [dict(parent)]

    calls: list[str] = []

    class _ParentEscalates:
        def evaluate(self, decision_id, facts):
            calls.append(decision_id)
            return {"decision": ESCALATE_DECISION, "reason_code": "unknown_fact_value"}

    def model_of(name):
        return _ParentEscalates() if name == "parent" else base_model_of(name)

    # 会社の物品: 自分は条件付き許可と許可、親はエスカレーション → エスカレーション
    out = evaluate_request(
        routes, "あおい", "使用", {"usage_target": "会社の物品"}, model_of=model_of
    )
    assert out["decision"] == ESCALATE_DECISION
    assert calls == ["decision_parent"]  # 2行に付いていても1回だけ
    # 社外 Wi-Fi: 自分の Wi-Fi の表が禁止 → 親のエスカレーションより禁止が勝つ(D77)
    out = evaluate_request(
        routes, "あおい", "使用", {"usage_target": "社外Wi-Fi"}, model_of=model_of
    )
    assert out["decision"] == "禁止"


def test_a_request_with_no_route_is_permitted():
    """D81・D79(d): 規制する判定が1つも無い行為は許可(no_restriction)。"""
    from src.stage3_generate.scenario_eval import evaluate_request

    routes, model_of = _two_tables_on_one_action()
    out = evaluate_request(routes, "あおい", "撮影", {}, model_of=model_of)
    assert (out["decision"], out["reason_code"]) == ("許可", "no_restriction")
    assert out["_routes"] == 0


def test_routing_keeps_one_row_per_decision_for_the_same_action(simple_ir, child_registry):
    """D81: 層1で同じ Action に判定が2つあれば、子孫の作り直しで行も2つになる。"""
    registry = child_registry.copy()
    second = dict(registry.decision_tables[0])
    second.update(
        decision_table_id="dt_verbal_2",
        decision_id="decision_verbal_2",
        decision_name="口頭伝達可否判定2",
    )
    registry.decision_tables = list(registry.decision_tables) + [second]
    routes = derive_routes(_with_action(simple_ir, "情報提供"), registry)
    assert [(r["action"], r["decision_id"]) for r in routes] == [
        ("口頭伝達", "decision_verbal"),
        ("口頭伝達", "decision_verbal_2"),
    ]
    # 2行とも、親(この IR)の判定を継承する
    for route in routes:
        assert route["inherited_decisions"] == [
            {"dmn_model": "simple", "decision_id": "decision_simple"}
        ]


def test_the_registry_builds_every_row_for_a_request(child_registry):
    """D81・D75: 実行時の行は層1から作る。判定ごとに1行、祖先の判定を継承する。"""
    registry = child_registry.copy()
    second = dict(registry.decision_tables[0])
    second.update(
        decision_table_id="dt_verbal_2",
        decision_id="decision_verbal_2",
        decision_name="口頭伝達可否判定2",
        dmn_model="verbal_disclosure_2",
    )
    parent = dict(registry.decision_tables[0])
    parent.update(
        decision_table_id="dt_provision",
        decision_id="decision_provision",
        decision_name="情報提供可否判定",
        dmn_model="information_provision",
        entry_points=[{"actor": "あおい", "action": "情報提供"}],
    )
    registry.decision_tables = list(registry.decision_tables) + [second, parent]
    rows = registry.routes_for_request("あおい", "口頭伝達")
    assert [(r["dmn_model"], r["decision_id"]) for r in rows] == [
        ("verbal_disclosure", "decision_verbal"),
        ("verbal_disclosure_2", "decision_verbal_2"),
    ]
    for row in rows:
        assert row["inherited_decisions"] == [
            {"dmn_model": "information_provision", "decision_id": "decision_provision"}
        ]
    # 別の actor の要求には行が無い
    assert registry.routes_for_request("別のエージェント", "口頭伝達") == []


def test_the_bundle_keeps_separate_rows_and_c8_covers_both(simple_ir, child_registry):
    """D81(追補 タスクE2): 4点セットのルーティング表も、別々の decision_id の行を
    まとめない。C8 は両方の行の事実を見る。
    """
    registry = child_registry.copy()
    second = dict(registry.decision_tables[0])
    second.update(
        decision_table_id="dt_verbal_2",
        decision_id="decision_verbal_2",
        decision_name="口頭伝達可否判定2",
        input_columns=["phone_match"],
        depends_on_inputs=["phone_match"],
    )
    registry.decision_tables = list(registry.decision_tables) + [second]
    registry.fact_registry = list(registry.fact_registry) + [
        {
            **passthrough_fact("phone_match", ["一致", "不一致"], "patient.phone").to_json(),
            "defined_by": "既存規程 第0条",
            "used_by": ["dt_verbal_2"],
            "provenance": make_provenance("既存規程 第0条").to_json(),
            "history": [],
        }
    ]
    bundle = generate_wiring_bundle(_with_action(simple_ir, "情報提供"), registry=registry)
    rows = bundle.routing_table["routes"]
    assert [(r["action"], r["decision_id"]) for r in rows] == [
        ("口頭伝達", "decision_verbal"),
        ("口頭伝達", "decision_verbal_2"),
    ]
    assert rows[0]["required_facts"] == ["birth_match", "role", "environment"]
    assert rows[1]["required_facts"] == ["phone_match", "role", "environment"]
    # バインディング設定は両方の行の事実を持つ(C8 を通っている)
    assert {f["variable"] for f in bundle.bindings["facts"]} == {
        "birth_match", "phone_match", "role", "environment"
    }
    # 2行目の事実がバインディング設定から漏れたら C8 が止める
    bindings = dict(bundle.bindings)
    bindings["facts"] = [f for f in bundle.bindings["facts"] if f["variable"] != "phone_match"]
    with pytest.raises(SchemaWiringError, match="C8検証失敗"):
        _check_c8_routing_matches_bindings(bundle.routing_table, bindings)
