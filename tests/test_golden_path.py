"""ゴールデンパス(特権アカウント条文)の v2 を固定するテスト。

第15回 HANDOFF タスク5の受け入れ条件:

- クロス検証 C1〜C11 をすべて通過する
- DMN 評価シナリオが 4/4 一致する
- `review_gates` で `new_action_unapproved` と `new_fact_unapproved` の2つが立つ

加えて、設計書2章の決定事項が生成物に効いていること(D1・D3・D4・D5・D7・D8)も
固定し、v1 へ戻る変更を検出できるようにする。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage2_classify.vocab_flags import check_vocab_flag  # noqa: E402
from src.stage3_generate.dmn_engine import DmnModel  # noqa: E402
from src.stage3_generate.golden_path import (  # noqa: E402
    CLAUSE_ID,
    DECISION_ID,
    build_golden_path_ir,
)
from src.stage3_generate.ir_schema import AxisResult  # noqa: E402
from src.stage3_generate.registry import Registry  # noqa: E402
from src.stage3_generate.run_golden_path import SCENARIOS  # noqa: E402
from src.stage3_generate.wiring_generator import generate_wiring_bundle  # noqa: E402

CLAUSE_TEXT = "システム開発を担当する者が本番環境において特権/管理者権限で操作を行うことを禁止する。"


@pytest.fixture
def golden_bundle(tmp_path):
    """--use-oracle 相当(②を呼ばずオラクル値を使う)で4点セットを生成する。"""
    flagged, _ = check_vocab_flag(CLAUSE_TEXT)
    axis = lambda label: AxisResult(  # noqa: E731
        label=label,
        confidence=1.0,
        rationale="(oracle)",
        evidence_quote=CLAUSE_TEXT,
        quote_verified=True,
        vocab_flagged=flagged,
    )
    ir = build_golden_path_ir(
        axis("ハード制約"),
        axis("宣言的"),
        clause_text=CLAUSE_TEXT,
        registered_at="2026-10-22T00:00:00+00:00",
    )
    bundle = generate_wiring_bundle(ir, registry=Registry.empty())
    return ir, bundle


def test_clause_id_is_the_golden_path_clause():
    assert CLAUSE_ID == "ipa_secguide_v4_6_7_1"


def test_cross_validation_passes(golden_bundle):
    """C1〜C11 を通過して4点セットが揃う(例外が出ないこと自体が検証)。"""
    _, bundle = golden_bundle
    assert bundle.dmn_xml.startswith("<?xml")
    assert bundle.bindings["facts"]
    assert bundle.routing_table["routes"]
    assert bundle.registry["decision_tables"]


def test_review_gates_are_exactly_the_two_expected(golden_bundle):
    """立つゲートは new_action_unapproved と new_fact_unapproved の2つだけ。"""
    ir, bundle = golden_bundle
    assert ir.review_gates.should_review is True
    assert ir.review_gates.gate_names() == [
        "new_action_unapproved",
        "new_fact_unapproved",
    ]
    assert bundle.registry["review"]["gates"] == [
        "new_action_unapproved",
        "new_fact_unapproved",
    ]


def test_dmn_scenarios_all_match(golden_bundle, tmp_path):
    """DMN 評価シナリオが 4/4 一致する。"""
    _, bundle = golden_bundle
    paths = bundle.write_all(tmp_path, "privileged_operation")
    model = DmnModel(str(paths["dmn"]))
    for scenario in SCENARIOS:
        result = model.evaluate(DECISION_ID, scenario["facts"])
        for key, expected in scenario["expected"].items():
            assert result.get(key) == expected, f"{scenario['name']}: {key}"


def test_facts_are_in_the_ir_not_an_external_constant(golden_bundle):
    """D1: fact の定義が IR の中にある(v1 は BINDINGS 定数を外から渡していた)。"""
    ir, bundle = golden_bundle
    assert [f.variable for f in ir.facts.new_facts] == [
        "operator_role",
        "target_environment",
        "requested_privilege",
    ]
    # バインディング設定は IR の facts から作られる
    assert [f["variable"] for f in bundle.bindings["facts"]] == [
        "operator_role",
        "target_environment",
        "requested_privilege",
    ]


def test_primitive_is_passthrough(golden_bundle):
    """D3: 区分の値なので primitive は passthrough(v1 は exact_match と誤宣言)。"""
    _, bundle = golden_bundle
    assert {f["primitive"] for f in bundle.bindings["facts"]} == {"passthrough"}


def test_possible_values_contain_only_clause_values(golden_bundle):
    """D4: possible_values は条文に出てくる値だけ。

    v1 は「運用担当」「その他」「検証環境」「開発環境」「一般」を含んでいたが、
    いずれも条文に書かれていない(レビューアの業務知識の混入)。
    """
    _, bundle = golden_bundle
    by_var = {f["variable"]: f["possible_values"] for f in bundle.bindings["facts"]}
    assert by_var["operator_role"] == ["システム開発担当"]
    assert by_var["target_environment"] == ["本番環境"]
    assert by_var["requested_privilege"] == ["特権"]

    out_of_clause = {"運用担当", "その他", "検証環境", "開発環境", "一般"}
    for values in by_var.values():
        assert not (set(values) & out_of_clause)


def test_sources_use_from_path_form(golden_bundle):
    """D5: sources は {from, path} 形式(v1 は 'action_request.operator.role' の文字列)。"""
    _, bundle = golden_bundle
    for fact in bundle.bindings["facts"]:
        assert set(fact["sources"].keys()) == {"value"}
        source = fact["sources"]["value"]
        assert source["from"] == "action_request"
        assert "path" in source and "." not in source["from"]


def test_routing_table_identifies_the_decision(golden_bundle):
    """D8: ルーティング表から呼ぶ判定と DMN モデルを一意に特定できる。"""
    _, bundle = golden_bundle
    route = bundle.routing_table["routes"][0]
    assert route["actor"] == "あおい"
    assert route["action"] == "特権操作"
    assert route["dmn_model"] == "privileged_operation"
    assert route["decision_id"] == "decision_privileged_operation"
    assert route["required_facts"] == [
        "operator_role",
        "target_environment",
        "requested_privilege",
    ]


def test_registry_layer3_records_fact_provenance(golden_bundle):
    """D2: 層3(fact 台帳)に defined_by と used_by が入る。"""
    _, bundle = golden_bundle
    layer3 = {f["variable"]: f for f in bundle.registry["fact_registry"]}
    assert set(layer3) == {"operator_role", "target_environment", "requested_privilege"}
    for entry in layer3.values():
        assert entry["defined_by"] == "IPA情報セキュリティ関連規程 6.7 特権アカウント(第3項)"
        assert entry["used_by"] == ["dt_privileged_operation"]


def test_registry_layer1_records_dmn_model(golden_bundle):
    """D8: 層1に dmn_model が入る。"""
    _, bundle = golden_bundle
    assert bundle.registry["decision_tables"][0]["dmn_model"] == "privileged_operation"


def test_level_outcome_is_derived_not_stored(golden_bundle):
    """D6: outcome は final.action から導出される(値として持たない)。"""
    ir, _ = golden_bundle
    assert ir.level_determination.final.action == "new_table"
    assert ir.level_determination.outcome == "level1_new_table"
    # dataclass のフィールドではないこと
    assert "outcome" not in {f.name for f in ir.level_determination.__dataclass_fields__.values()}


def test_dmn_namespace_unchanged_from_v1(golden_bundle):
    """namespace は v1 と同じ値のままにする(Kogito 実機確認の変数を増やさない)。"""
    _, bundle = golden_bundle
    assert 'namespace="http://gijinka.example.com/dmn/privileged_operation"' in bundle.dmn_xml


# --------------------------------------------------------------------------
# v1.2 の変更(第17回 HANDOFF タスク5)
# --------------------------------------------------------------------------


def test_compound_fact_option_is_derived_as_new_table_row(golden_bundle):
    """D18・D31: 新しく作る表の行なので new_table_row。導出で出す。

    v1.1 までは選択肢が無く existing_table_row_addition を名乗っていた(誤ラベル)。
    v1.3 では IR に手で書かず、決定表ごとに導出する(D31)。IR が手で書くのは
    「一致モジュール側で AND を組もうとして拒否された」印だけ。
    """
    ir, _ = golden_bundle
    assert ir.option_taken == {"dt_privileged_operation": "new_table_row"}
    assert ir.compound_fact_application.rejected_in_primitive is False
    # 導出なので dataclass のフィールドではない
    assert "option_taken" not in {
        f.name for f in ir.compound_fact_application.__dataclass_fields__.values()
    }


def test_sources_undefined_is_not_set_for_clause_facts(golden_bundle):
    """D33: sources が入っている fact には sources_undefined を付けない。

    印が付くのは、初期登録で取得元が分からなかった fact(あおいの7件)だけである。
    """
    _, bundle = golden_bundle
    assert all("sources_undefined" not in f for f in bundle.bindings["facts"])


def test_knowledge_sources_drop_the_unsupported_registry(golden_bundle):
    """D22: 根拠の無い ks_privileged_account_registry を消す。

    これを参照していた exact_match の registered 引数が D3 で消えたため、
    条文にも出典付き context にも根拠が無くなった(設計書1.4)。
    """
    ir, bundle = golden_bundle
    assert ir.new_decision_tables[0].knowledge_sources == ["ks_action_request"]
    assert "ks_privileged_account_registry" not in bundle.dmn_xml
    layer1 = bundle.registry["decision_tables"]
    entry = next(e for e in layer1 if e["decision_table_id"] == "dt_privileged_operation")
    assert entry["knowledge_sources"] == ["ks_action_request"]


def test_registry_layer1_has_output_columns_and_entry_points(golden_bundle):
    """D12・D16: 層1に output_columns と entry_points が入る。"""
    _, bundle = golden_bundle
    entry = next(
        e
        for e in bundle.registry["decision_tables"]
        if e["decision_table_id"] == "dt_privileged_operation"
    )
    assert entry["output_columns"] == ["decision", "reason_code"]
    # 最終判定(この IR の起点)なので、呼び出し口が入る
    assert entry["entry_points"] == [{"actor": "あおい", "action": "特権操作"}]


def test_new_primitive_gate_is_not_raised(golden_bundle):
    """D20: covered=True なので new_primitive_required は立たない。"""
    ir, _ = golden_bundle
    assert ir.level_determination.atomic_fact.covered is True
    assert ir.review_gates.new_primitive_required is False


def test_facts_are_unapproved_by_default(golden_bundle):
    """D14: 既定では各 FactDef の approved_by は None で、ゲートが立つ。"""
    ir, bundle = golden_bundle
    assert [f.approved_by for f in ir.facts.new_facts] == [None, None, None]
    assert ir.facts.unapproved_fact_names() == [
        "operator_role",
        "target_environment",
        "requested_privilege",
    ]
    assert all(f["approved_by"] is None for f in bundle.bindings["facts"])


def test_gates_drop_after_approval(tmp_path):
    """D14: action と fact に承認者を入れると、2つのゲートが下りる。

    v1.1 の `new_fact_unapproved` は「新語彙あり」で立っていたため、新しい fact を
    持つ条文では承認しても下がらなかった。
    """
    flagged, _ = check_vocab_flag(CLAUSE_TEXT)
    axis = lambda label: AxisResult(  # noqa: E731
        label=label,
        confidence=1.0,
        rationale="(oracle)",
        evidence_quote=CLAUSE_TEXT,
        quote_verified=True,
        vocab_flagged=flagged,
    )
    ir = build_golden_path_ir(
        axis("ハード制約"),
        axis("宣言的"),
        clause_text=CLAUSE_TEXT,
        action_approved_by="Kazuhiro",
        facts_approved_by="Kazuhiro",
        registered_at="2026-10-23T00:00:00+00:00",
    )
    assert ir.review_gates.gate_names() == []
    assert ir.review_gates.should_review is False

    # 承認しても4点セットの生成とクロス検証は通る
    bundle = generate_wiring_bundle(ir, registry=Registry.empty())
    assert bundle.registry["review"]["gates"] == []
    assert bundle.registry["review"]["should_review"] is False
    assert all(f["approved_by"] == "Kazuhiro" for f in bundle.bindings["facts"])
