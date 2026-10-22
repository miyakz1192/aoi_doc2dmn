"""クロス検証 C1〜C10 の失敗テスト。

設計書5章「各検証には、わざと違反させた IR で SchemaWiringError になることを確かめる
失敗テストを付け、リポジトリにコミットする」に対応する。v1 では C1〜C3 の失敗テストが
手元で確認されただけでコミットされていなかった。

各テストは、正常に通る IR(`simple_ir` / `intermediate_ir`)を1箇所だけ壊して
`SchemaWiringError` になることを確かめる。壊す前の IR が通ることも
`test_fixtures_pass_all_checks` で押さえ、「もともと落ちていただけ」を防ぐ。
"""
from __future__ import annotations

import dataclasses

import pytest

from src.stage3_generate.ir_schema import (
    AtomicFactLayer,
    FinalLayer,
    IntermediateChange,
    LevelDetermination,
    RuleRowDraft,
    SourceRef,
)
from src.stage3_generate.wiring_generator import SchemaWiringError, generate_wiring_bundle


# --------------------------------------------------------------------------
# 前提: 壊す前のフィクスチャは通る
# --------------------------------------------------------------------------


def test_fixtures_pass_all_checks(simple_ir, empty_registry, intermediate_ir, seeded_registry):
    """壊す前の2つのフィクスチャが C1〜C10 をすべて通ること。"""
    simple = generate_wiring_bundle(simple_ir, registry=empty_registry)
    assert simple.routing_table["routes"][0]["required_facts"] == ["role", "environment"]

    nested = generate_wiring_bundle(intermediate_ir, registry=seeded_registry)
    # ドット参照をたどって中間判定の先の事実まで集められている(設計書4.1)
    assert nested.routing_table["routes"][0]["required_facts"] == [
        "birth_match",
        "phone_match",
    ]


# --------------------------------------------------------------------------
# C1: fact の不足
# --------------------------------------------------------------------------


def test_c1_missing_fact_definition(simple_ir, empty_registry):
    """required_facts の fact 定義が new_facts にも層3にも無ければ落ちる。"""
    simple_ir.facts.new_facts = [
        f for f in simple_ir.facts.new_facts if f.variable != "environment"
    ]
    with pytest.raises(SchemaWiringError, match="C1検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


# --------------------------------------------------------------------------
# C2: fact の余剰
# --------------------------------------------------------------------------


def test_c2_unused_new_fact(simple_ir, empty_registry):
    """どの決定表からも参照されない新規 fact があれば落ちる。"""
    from tests.conftest import passthrough_fact

    simple_ir.facts.new_facts.append(passthrough_fact("unused", ["x"], "a.b"))
    with pytest.raises(SchemaWiringError, match="C2検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c2_allows_fact_used_only_by_row_addition(simple_ir, seeded_registry):
    """到達しない既存表への行追加で使う fact は C2 で弾かれない。

    C2 の基準を required_facts にすると誤って落ちる。基準は all_fact_keys()。
    """
    from tests.conftest import passthrough_fact

    simple_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_identity_2",
            decision_table_id="dt_identity",
            position=2,
            conditions={"extra_flag": "有"},
            outputs={"identity_level": "中"},
        )
    ]
    simple_ir.facts.new_facts.append(passthrough_fact("extra_flag", ["有"], "a.flag"))
    simple_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_identity",
            relation="proper_subset",
            action="add_row",
            new_output_values=["中"],
        )
    ]
    bundle = generate_wiring_bundle(simple_ir, registry=seeded_registry)
    # extra_flag は required_facts(エントリ判定から到達する事実)には入らない
    assert "extra_flag" not in bundle.routing_table["routes"][0]["required_facts"]


# --------------------------------------------------------------------------
# C3: 複合 fact の拒否
# --------------------------------------------------------------------------


def test_c3_rejected_compound_in_primitive(simple_ir, empty_registry):
    """一致プリミティブ側で AND を組む案は拒否される(複合fact禁止原則)。"""
    simple_ir.compound_fact_application = dataclasses.replace(
        simple_ir.compound_fact_application,
        option_taken="rejected_compound_in_primitive",
    )
    with pytest.raises(SchemaWiringError, match="C3検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


# --------------------------------------------------------------------------
# C4: sources と primitive の引数名
# --------------------------------------------------------------------------


def test_c4_sources_key_mismatch(simple_ir, empty_registry):
    """passthrough の引数名は value。別のキーなら落ちる。"""
    simple_ir.facts.new_facts[0].sources = {
        "subject": SourceRef(from_="action_request", path="operator.role")
    }
    with pytest.raises(SchemaWiringError, match="C4検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c4_unknown_primitive(simple_ir, empty_registry):
    """引数名の定義が無い未知の primitive は検証できないので落ちる。"""
    simple_ir.facts.new_facts[0].primitive = "handmade_primitive"
    with pytest.raises(SchemaWiringError, match="C4検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


# --------------------------------------------------------------------------
# C5: 決定表の値と possible_values
# --------------------------------------------------------------------------


def test_c5_value_not_in_possible_values(simple_ir, empty_registry):
    """決定表が参照する値が possible_values に無ければ落ちる(死んだ行)。

    v1 で検出できなかったズレ。possible_values から「開発担当」を抜くと、
    1行目は実行時に一度もヒットしなくなる。
    """
    simple_ir.facts.new_facts[0].possible_values = ["運用担当"]
    with pytest.raises(SchemaWiringError, match="C5検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c5_unsupported_feel_cell(simple_ir, empty_registry):
    """範囲・比較などの未対応FEELをセルに書くと落ちる。"""
    simple_ir.new_decision_tables[0].rule_rows[0].conditions["role"] = ">= 3"
    with pytest.raises(SchemaWiringError, match="C5検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c5_accepts_feel_literal_list(simple_ir, empty_registry):
    """`"a", "b"` 形式のリテラル列は分解して照合する。"""
    simple_ir.facts.new_facts[0].possible_values = ["開発担当", "運用担当"]
    simple_ir.new_decision_tables[0].rule_rows[0].conditions["role"] = '"開発担当", "運用担当"'
    bundle = generate_wiring_bundle(simple_ir, registry=empty_registry)
    assert bundle.dmn_xml  # 生成できる

    # 列挙した値のうち1つでも possible_values に無ければ落ちる
    simple_ir.facts.new_facts[0].possible_values = ["開発担当"]
    with pytest.raises(SchemaWiringError, match="C5検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c5_value_additions_are_allowed(simple_ir, seeded_registry):
    """value_additions で足した値は possible_values 扱いになる。"""
    simple_ir.facts.new_facts = [
        f for f in simple_ir.facts.new_facts if f.variable != "role"
    ]
    simple_ir.facts.referenced_facts = ["birth_match"]
    simple_ir.facts.value_additions = {"birth_match": ["開発担当"]}
    for table in simple_ir.new_decision_tables:
        table.input_columns = ["birth_match", "environment"]
        table.depends_on_inputs = ["birth_match", "environment"]
        for row in table.rule_rows:
            row.conditions = {
                "birth_match": row.conditions.pop("role"),
                "environment": row.conditions["environment"],
            }
    bundle = generate_wiring_bundle(simple_ir, registry=seeded_registry)
    bound = {f["variable"]: f for f in bundle.bindings["facts"]}
    assert "開発担当" in bound["birth_match"]["possible_values"]


# --------------------------------------------------------------------------
# C6: 参照 fact の実在
# --------------------------------------------------------------------------


def test_c6_referenced_fact_not_in_registry(intermediate_ir, seeded_registry):
    """層3に無い fact を referenced_facts に書くと落ちる。"""
    intermediate_ir.facts.referenced_facts = ["birth_match", "no_such_fact"]
    with pytest.raises(SchemaWiringError, match="C6検証失敗"):
        generate_wiring_bundle(intermediate_ir, registry=seeded_registry)


def test_c6_value_addition_key_not_in_registry(intermediate_ir, seeded_registry):
    """層3に無い fact に value_additions を書くと落ちる。"""
    intermediate_ir.facts.value_additions = {"no_such_fact": ["x"]}
    with pytest.raises(SchemaWiringError, match="C6検証失敗"):
        generate_wiring_bundle(intermediate_ir, registry=seeded_registry)


# --------------------------------------------------------------------------
# C7: 判定先の実在
# --------------------------------------------------------------------------


def test_c7_entry_decision_not_generated(simple_ir, empty_registry):
    """target.entry_decision_id の決定表が生成物に無ければ落ちる。

    final.action=new_table なのに entry と違う decision_id の表しか作らない場合、
    C9条件1で先に捕まる。ここでは C9 を通る形(action=add_rows)にしたうえで、
    登録簿にも IR にも entry の決定表が無い状態を作る。
    """
    simple_ir.level_determination = LevelDetermination(
        atomic_fact=AtomicFactLayer(covered=True),
        intermediate=[],
        final=FinalLayer(
            compared_table_id="dt_simple", relation="exact_match", action="add_rows"
        ),
    )
    simple_ir.rule_row_additions = list(simple_ir.new_decision_tables[0].rule_rows)
    simple_ir.new_decision_tables = []
    # 登録簿が空なので entry の決定表はどこにも無い -> C9条件2 で停止する
    with pytest.raises(SchemaWiringError, match="C9検証失敗|C7検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


# --------------------------------------------------------------------------
# C8: 集める事実の一致
# --------------------------------------------------------------------------


def test_c8_is_structurally_guaranteed(simple_ir, empty_registry):
    """C8 はルーティング表とバインディング設定を同じ required_facts から作るため、
    IR の側から壊すことができない(構造的に保証される)。

    そのため「違反させる失敗テスト」は作れない。ここでは代わりに、両者が実際に
    一致していることを固定し、将来どちらかの導出だけを変えた場合に気付けるようにする。
    """
    bundle = generate_wiring_bundle(simple_ir, registry=empty_registry)
    routed = set(bundle.routing_table["routes"][0]["required_facts"])
    bound = {f["variable"] for f in bundle.bindings["facts"]}
    assert routed == bound


# --------------------------------------------------------------------------
# C9: レベル判定の整合(設計書3.7の5条件)
# --------------------------------------------------------------------------


def test_c9_cond1_new_table_without_entry_decision(simple_ir, empty_registry):
    """条件1: new_table なのに entry の決定表が new_decision_tables に無い。"""
    simple_ir.new_decision_tables[0].decision_id = "decision_other"
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件1\\)"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c9_cond2_add_rows_without_compared_table(simple_ir, empty_registry):
    """条件2: add_rows なのに compared_table_id が None。"""
    simple_ir.level_determination = LevelDetermination(
        atomic_fact=AtomicFactLayer(covered=True),
        intermediate=[],
        final=FinalLayer(compared_table_id=None, relation=None, action="add_rows"),
    )
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件2\\)"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c9_cond2_add_rows_without_row_additions(simple_ir, seeded_registry):
    """条件2: add_rows で compared_table_id は正しいが、追加行が無い。"""
    simple_ir.target.entry_decision_id = "decision_identity"
    simple_ir.new_decision_tables = []
    simple_ir.rule_row_additions = []
    simple_ir.facts.new_facts = []
    simple_ir.level_determination = LevelDetermination(
        atomic_fact=AtomicFactLayer(covered=True),
        intermediate=[],
        final=FinalLayer(
            compared_table_id="dt_identity", relation="exact_match", action="add_rows"
        ),
    )
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件2\\)"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def test_c9_cond3_add_row_without_matching_output_value(simple_ir, seeded_registry):
    """条件3: intermediate.action=add_row だが new_output_values が行の出力に無い。"""
    simple_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_identity_2",
            decision_table_id="dt_identity",
            position=2,
            conditions={"birth_match": "一致"},
            outputs={"identity_level": "高"},  # 「中」ではない
        )
    ]
    simple_ir.facts.referenced_facts = ["birth_match"]
    simple_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_identity",
            relation="proper_subset",
            action="add_row",
            new_output_values=["中(氏名なし・2要素一致)"],
        )
    ]
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件3\\)"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def test_c9_cond4_intermediate_table_not_in_registry(simple_ir, empty_registry):
    """条件4: intermediate の table_id が層1に実在しない。"""
    simple_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_nonexistent",
            relation="proper_subset",
            action="reuse_value",
        )
    ]
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件4\\)"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c9_cond4_compared_table_not_in_registry(simple_ir, empty_registry):
    """条件4: final の compared_table_id が層1に実在しない。"""
    simple_ir.level_determination.final = FinalLayer(
        compared_table_id="dt_nonexistent",
        relation="proper_subset",
        action="new_table",
    )
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件4\\)"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c9_cond5_uncovered_without_new_primitives(simple_ir, empty_registry):
    """条件5: covered=False なのに new_primitives が空。"""
    simple_ir.level_determination.atomic_fact = AtomicFactLayer(
        covered=False, new_primitives=[]
    )
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件5\\)"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


# --------------------------------------------------------------------------
# C10: ドット参照の解決
# --------------------------------------------------------------------------


def test_c10_dot_reference_decision_missing(intermediate_ir, empty_registry):
    """参照先の判定が IR にも層1にも無ければ落ちる。

    登録簿を空にすると C9条件4(compared_table_id の実在)が先に発火するため、
    compared_table_id を None にして C9 を通した状態で C10 だけを破る。
    """
    intermediate_ir.facts.referenced_facts = []
    intermediate_ir.level_determination.final = FinalLayer(
        compared_table_id=None, relation=None, action="new_table"
    )
    with pytest.raises(SchemaWiringError, match="C10検証失敗"):
        generate_wiring_bundle(intermediate_ir, registry=empty_registry)


def test_c10_dot_reference_output_missing(intermediate_ir, seeded_registry):
    """参照先の判定は在るが、その出力名を持たなければ落ちる。"""
    for table in intermediate_ir.new_decision_tables:
        table.input_columns = ["本人確認判定.no_such_output"]
        for row in table.rule_rows:
            row.conditions = {"本人確認判定.no_such_output": "-"}
    with pytest.raises(SchemaWiringError, match="C10検証失敗"):
        generate_wiring_bundle(intermediate_ir, registry=seeded_registry)


# --------------------------------------------------------------------------
# required_facts の導出(設計書4.1)
# --------------------------------------------------------------------------


def test_required_facts_cycle_raises_schema_error(intermediate_ir, seeded_registry):
    """ドット参照が循環していても RecursionError ではなく SchemaWiringError になる。"""
    seeded_registry.decision_tables[0]["input_columns"] = ["口頭伝達可否判定.decision"]
    with pytest.raises(SchemaWiringError):
        generate_wiring_bundle(intermediate_ir, registry=seeded_registry)


def test_required_facts_unknown_entry_decision(simple_ir, empty_registry):
    """entry の判定が見つからない場合も SchemaWiringError(KeyError を外に出さない)。

    C9条件1を通すために new_table ではなく add_rows にし、登録簿に compared の表を
    置いて C9条件2 を通した状態で、entry だけが存在しない形を作る。
    """
    simple_ir.target.entry_decision_id = "decision_missing"
    with pytest.raises(SchemaWiringError):
        generate_wiring_bundle(simple_ir, registry=empty_registry)
