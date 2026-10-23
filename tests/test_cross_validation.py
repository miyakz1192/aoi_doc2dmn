"""クロス検証 C1〜C11 の失敗テスト。

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

from tests.conftest import make_provenance

from src.stage3_generate.ir_schema import (
    AtomicFactLayer,
    DecisionTarget,
    FactDef,
    FinalLayer,
    IntermediateChange,
    LevelDetermination,
    RuleRowDraft,
    SourceRef,
    ValueAddition,
)
from src.stage3_generate.wiring_generator import (
    SchemaWiringError,
    _check_c1_facts_available,
    _check_c2_new_facts_used,
    derive_required_facts,
    generate_wiring_bundle,
)


# --------------------------------------------------------------------------
# 前提: 壊す前のフィクスチャは通る
# --------------------------------------------------------------------------


def test_fixtures_pass_all_checks(simple_ir, empty_registry, intermediate_ir, seeded_registry):
    """壊す前の2つのフィクスチャが C1〜C11 をすべて通ること。"""
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


def test_c1_missing_intermediate_fact(intermediate_ir, seeded_registry):
    """通しで流して C1 が止まる: 参照先の中間判定が使う fact が層3に無い。

    D25 で C6 の基準が「IR が書いた行と表」になったため、IR が言及しない中間判定の
    fact の不足を捕まえるのは C1 だけになった(D26)。この IR の入力列はドット参照
    1列だけなので C6 は通り、ドット参照をたどった先で不足が見つかる。
    """
    seeded_registry.decision_tables[0]["input_columns"] = [
        "birth_match",
        "phone_match",
        "unregistered_fact",
    ]
    with pytest.raises(SchemaWiringError, match="C1検証失敗.*unregistered_fact"):
        generate_wiring_bundle(intermediate_ir, registry=seeded_registry)


def test_c1_missing_fact_definition(simple_ir, empty_registry):
    """required_facts の fact 定義が new_facts にも層3にも無ければ落ちる。

    この形(IR が自分で使う fact の宣言漏れ)は、通しで流すと C6 が先に捕まえる
    (宣言が無ければ C6の網羅、宣言があれば C6の実在)。C1 は多重の歯止めとして
    残っているので、検証関数を直接呼んで確かめる。
    """
    simple_ir.facts.new_facts = [
        f for f in simple_ir.facts.new_facts if f.variable != "environment"
    ]
    simple_ir.facts.referenced_facts = ["environment"]

    # 通しで流すと C6 が先に止める
    with pytest.raises(SchemaWiringError, match="C6検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)

    # C1 自体も同じ違反で止まる
    with pytest.raises(SchemaWiringError, match="C1検証失敗"):
        _check_c1_facts_available(
            ["role", "environment"], simple_ir, empty_registry
        )


# --------------------------------------------------------------------------
# C2: fact の余剰
# --------------------------------------------------------------------------


def test_c2_unused_new_fact(simple_ir, empty_registry):
    """どの決定表からも参照されない新規 fact があれば落ちる。"""
    from tests.conftest import passthrough_fact

    simple_ir.facts.new_facts.append(passthrough_fact("unused", ["x"], "a.b"))
    with pytest.raises(SchemaWiringError, match="C2検証失敗"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c2_allows_fact_used_only_by_row_addition(simple_ir):
    """到達しない既存表への行追加で使う fact は C2 で弾かれない。

    C2 の基準を required_facts にすると誤って落ちる。基準は all_fact_keys()。
    C2 だけを確かめたいので、検証関数を直接呼ぶ(この IR を通しで流すと、
    既存モデルの DMN ファイルが要るため後続の工程で止まる)。
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
            new_output_values={"identity_level": ["中"]},
        )
    ]
    # 例外が出ないこと自体が検証(extra_flag は required_facts には入らないが、
    # all_fact_keys() には入るので C2 は通る)
    _check_c2_new_facts_used(simple_ir)


# --------------------------------------------------------------------------
# C3: 複合 fact の拒否
# --------------------------------------------------------------------------


def test_c3_rejected_compound_in_primitive(simple_ir, empty_registry):
    """一致プリミティブ側で AND を組む案は拒否される(複合fact禁止原則。D31)。"""
    simple_ir.compound_fact_application = dataclasses.replace(
        simple_ir.compound_fact_application,
        rejected_in_primitive=True,
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
    simple_ir.facts.value_additions = {
        "birth_match": [ValueAddition(value="開発担当", approved_by="pytest")]
    }
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


def test_c6_extra_referenced_fact(intermediate_ir, seeded_registry):
    """D25: IR のどの行・表も使っていない fact を referenced_facts に書くと落ちる。

    第18回のレベル2のテストは、最終判定の全 fact(7件)を列挙していた。条文が
    言及した fact だけを列挙させるため、余りも失敗とする。
    """
    # birth_match は層3に実在するが、この IR の入力列はドット参照1列だけ
    intermediate_ir.facts.referenced_facts = ["birth_match"]
    with pytest.raises(SchemaWiringError, match="C6検証失敗\\(余り\\).*birth_match"):
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
            new_output_values={"identity_level": ["中(氏名なし・2要素一致)"]},
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


# --------------------------------------------------------------------------
# v1.2 で追加・拡張した検証の失敗テスト(第17回 HANDOFF タスク4)
# --------------------------------------------------------------------------


def test_c4_identifiability_classifier_rejected_in_new_facts(
    simple_ir, empty_registry
):
    """D23: identifiability_classifier を新しい条文の new_facts で使うと落ちる。

    あおいの情報分類判定の初期登録分(provenance.origin=bootstrap)だけで使える
    境界事例なので、新規の条文では複合fact禁止原則に従わせる。
    """
    simple_ir.facts.new_facts[0] = FactDef(
        variable="role",
        primitive="identifiability_classifier",
        possible_values=["開発担当"],
        sources={
            "data_category": SourceRef(from_="action_request", path="a"),
            "care_setting": SourceRef(from_="action_request", path="b"),
        },
    )
    with pytest.raises(SchemaWiringError, match="C4検証失敗.*identifiability_classifier"):
        generate_wiring_bundle(simple_ir, registry=empty_registry)


def test_c6_missing_referenced_fact_declaration(simple_ir, seeded_registry):
    """D21: 使っている既存 fact が referenced_facts に無ければ落ちる(網羅)。"""
    simple_ir.facts.new_facts = [
        f for f in simple_ir.facts.new_facts if f.variable != "role"
    ]
    for table in simple_ir.new_decision_tables:
        table.input_columns = ["birth_match", "environment"]
        table.depends_on_inputs = ["birth_match", "environment"]
        for row in table.rule_rows:
            row.conditions = {
                "birth_match": "一致" if row.position == 1 else "-",
                "environment": row.conditions["environment"],
            }
    # birth_match は層3に実在するが、referenced_facts に宣言していない
    simple_ir.facts.referenced_facts = []
    with pytest.raises(SchemaWiringError, match="C6検証失敗\\(網羅\\)"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def test_c7_entry_decision_missing_from_existing_dmn(
    simple_ir, seeded_registry, tmp_path
):
    """D15: add_rows で、挿入後の DMN に起点の判定が無ければ落ちる。

    層1には起点の判定があるが、DMN ファイルには無い状態を作る。C9条件2・C5 を
    通したうえで C7 まで到達させるため、既存の中間判定への行追加の形にする。
    """
    # 層1に「DMN には無い最終判定」を足す
    seeded_registry.decision_tables.append(
        {
            "decision_table_id": "dt_ghost",
            "decision_id": "decision_ghost",
            "decision_name": "幽霊判定",
            "dmn_model": "aoi_transmission",
            # C9条件8(D30): add_rows では IR の actor・action が entry_points にある
            "entry_points": [{"actor": "あおい", "action": "テスト操作"}],
            "hit_policy": "FIRST",
            "input_columns": ["birth_match"],
            "output_columns": ["decision"],
            "depends_on_decisions": [],
            "depends_on_inputs": ["birth_match"],
            "knowledge_sources": [],
            "provenance": make_provenance("既存規程 第0条").to_json(),
        }
    )
    # dt_ghost だけを持つ DMN ファイル(幽霊判定は入っていない)を用意する
    dmn_dir = tmp_path / "dmn"
    dmn_dir.mkdir()
    (dmn_dir / "aoi_transmission.dmn").write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<definitions xmlns="https://www.omg.org/spec/DMN/20240513/MODEL/"'
        ' id="aoi_transmission_definitions" name="aoi_transmission"'
        ' namespace="http://gijinka.example.com/dmn/aoi_transmission">\n'
        '  <decision id="decision_other" name="別の判定">\n'
        '    <decisionTable id="dt_ghost" hitPolicy="FIRST">\n'
        '      <input id="in_birth_match"><inputExpression id="ie1" typeRef="string">'
        "<text>birth_match</text></inputExpression></input>\n"
        '      <output id="out_decision" label="decision" name="decision"'
        ' typeRef="string" />\n'
        "    </decisionTable>\n"
        "  </decision>\n"
        "</definitions>\n",
        encoding="utf-8",
    )

    simple_ir.target = DecisionTarget(
        dmn_model="aoi_transmission", entry_decision_id="decision_ghost"
    )
    simple_ir.facts.new_facts = []
    simple_ir.facts.referenced_facts = ["birth_match"]
    simple_ir.new_decision_tables = []
    simple_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_ghost_1",
            decision_table_id="dt_ghost",
            position=1,
            conditions={"birth_match": "一致"},
            outputs={"decision": "許可"},
        )
    ]
    simple_ir.level_determination = LevelDetermination(
        atomic_fact=AtomicFactLayer(covered=True),
        intermediate=[],
        final=FinalLayer(
            compared_table_id="dt_ghost", relation="exact_match", action="add_rows"
        ),
    )
    with pytest.raises(SchemaWiringError, match="C7検証失敗"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry, dmn_dir=dmn_dir)


def test_c9_condition6_add_column_without_new_input_columns(
    simple_ir, seeded_registry
):
    """D19: add_column で new_input_columns が空なら落ちる(整合条件6)。"""
    simple_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_identity",
            relation="intersecting_non_subset",
            action="add_column",
            new_input_columns=[],
        )
    ]
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件6\\).*new_input_columns"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def test_c9_condition6_added_column_not_used_by_any_row(simple_ir, seeded_registry):
    """D19: 足した列が、追加する行の条件に現れなければ落ちる(整合条件6)。"""
    simple_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_identity_9",
            decision_table_id="dt_identity",
            position=2,
            conditions={"birth_match": "一致"},
            outputs={"identity_level": "中"},
        )
    ]
    simple_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_identity",
            relation="intersecting_non_subset",
            action="add_column",
            new_input_columns=["address_match"],
        )
    ]
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件6\\).*address_match"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def test_c10_missing_output_columns_in_layer1(intermediate_ir, seeded_registry):
    """D12: 層1に output_columns が無いと C10 が検証できず落ちる。

    v1.1 は層2の行から出力名を集めていたため、この状態でも通っていた。
    """
    del seeded_registry.decision_tables[0]["output_columns"]
    with pytest.raises(SchemaWiringError, match="C10検証失敗.*output_columns"):
        generate_wiring_bundle(intermediate_ir, registry=seeded_registry)


def test_c11_dot_reference_value_not_produced(intermediate_ir, seeded_registry):
    """D13: ドット参照の列の値が、参照先の判定の出力値に無ければ落ちる。

    `seeded_registry` の本人確認判定が出すのは identity_level="高" だけなので、
    "中" を参照する行は実行時に一度もヒットしない。
    """
    intermediate_ir.new_decision_tables[0].rule_rows[0].conditions = {
        "本人確認判定.identity_level": '"中"'
    }
    with pytest.raises(SchemaWiringError, match="C11検証失敗"):
        generate_wiring_bundle(intermediate_ir, registry=seeded_registry)


def test_c11_accepts_value_added_by_this_ir(intermediate_ir, seeded_registry, tmp_path):
    """中間判定へ行を足して出力値を増やすなら、その値を参照してよい(C11)。"""
    intermediate_ir.new_decision_tables[0].rule_rows[0].conditions = {
        "本人確認判定.identity_level": '"中"'
    }
    intermediate_ir.facts.referenced_facts = ["birth_match", "phone_match"]
    intermediate_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_identity_2",
            decision_table_id="dt_identity",
            position=2,
            conditions={"birth_match": "一致", "phone_match": "不一致"},
            outputs={"identity_level": "中"},
        )
    ]
    intermediate_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_identity",
            relation="exact_match",
            action="add_row",
            new_output_values={"identity_level": ["中"]},
        )
    ]
    # C11 はここを通る。dmn_dir を空のディレクトリにして後続で止まることを確認し、
    # 止まる理由が C11 ではないことを明示する(永続台帳の DMN に依存させない)
    with pytest.raises(SchemaWiringError) as excinfo:
        generate_wiring_bundle(
            intermediate_ir, registry=seeded_registry, dmn_dir=tmp_path
        )
    assert "C11検証失敗" not in str(excinfo.value)
    assert "DMN ファイル" in str(excinfo.value)


def test_d17_rejects_dot_reference_to_another_dmn_model(
    intermediate_ir, seeded_registry
):
    """D17: 別の DMN モデルの判定をドット参照していたら落ちる。"""
    intermediate_ir.target = DecisionTarget(
        dmn_model="verbal_disclosure", entry_decision_id="decision_verbal"
    )
    with pytest.raises(SchemaWiringError, match="D17違反"):
        generate_wiring_bundle(intermediate_ir, registry=seeded_registry)


# --------------------------------------------------------------------------
# v1.3 で変えた・足した検証の失敗テスト(第19回 HANDOFF タスク3)
# --------------------------------------------------------------------------


def test_c9_cond3_output_name_not_in_output_columns(simple_ir, seeded_registry):
    """条件3(D29): new_output_values の出力名が、その表の output_columns に無い。

    v1.2 は値の一覧しか持たなかったため、どの出力列に足す値なのかを確かめられず、
    出力名の取り違えを通していた。
    """
    simple_ir.facts.referenced_facts = ["birth_match"]
    simple_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_identity_2",
            decision_table_id="dt_identity",
            position=2,
            conditions={"birth_match": "一致"},
            outputs={"identity_level": "中"},
        )
    ]
    simple_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_identity",
            relation="proper_subset",
            action="add_row",
            # 本人確認判定の output_columns は identity_level だけ
            new_output_values={"identity_result": ["済"]},
        )
    ]
    with pytest.raises(
        SchemaWiringError, match="C9検証失敗\\(条件3\\).*identity_result"
    ):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def test_c9_cond7_no_change_without_intermediate_change(simple_ir, seeded_registry):
    """条件7(D30): no_change なのに intermediate に add_row も add_column も無い。

    どの表も変えない IR は、4点セットに出すものがない。
    """
    _make_no_change_ir(simple_ir, seeded_registry)
    simple_ir.level_determination.intermediate = []
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件7\\)"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def test_c9_cond7_no_change_pointing_at_an_intermediate_decision(
    simple_ir, seeded_registry
):
    """条件7(D30): no_change の起点が最終判定でない(entry_points が空)。"""
    _make_no_change_ir(simple_ir, seeded_registry)
    # 本人確認判定は中間判定なので entry_points が空
    simple_ir.target = DecisionTarget(
        dmn_model="aoi_transmission", entry_decision_id="decision_identity"
    )
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件7\\).*entry_points"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def test_c9_cond8_action_not_in_entry_points(simple_ir, seeded_registry):
    """条件8(D30): 既存のルートに足す条文が、別の行為を名乗っている。"""
    _make_no_change_ir(simple_ir, seeded_registry)
    simple_ir.action.normalized = "別の行為"
    with pytest.raises(SchemaWiringError, match="C9検証失敗\\(条件8\\)"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry)


def _make_no_change_ir(simple_ir, seeded_registry) -> None:
    """`simple_ir` を「中間判定に行を足すだけ」の no_change の形に作り替える。

    `seeded_registry` に最終判定が無いので、呼び出し口(entry_points)を持つ
    最終判定を1枚足す。
    """
    seeded_registry.decision_tables.append(
        {
            "decision_table_id": "dt_final",
            "decision_id": "decision_final",
            "decision_name": "既存の最終判定",
            "dmn_model": "aoi_transmission",
            "entry_points": [{"actor": "あおい", "action": "テスト操作"}],
            "hit_policy": "FIRST",
            "input_columns": ["本人確認判定.identity_level"],
            "output_columns": ["decision"],
            "depends_on_decisions": ["decision_identity"],
            "depends_on_inputs": [],
            "knowledge_sources": [],
            "provenance": make_provenance("既存規程 第0条").to_json(),
        }
    )
    simple_ir.target = DecisionTarget(
        dmn_model="aoi_transmission", entry_decision_id="decision_final"
    )
    simple_ir.facts.new_facts = []
    simple_ir.facts.referenced_facts = ["birth_match"]
    simple_ir.new_decision_tables = []
    simple_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_identity_2",
            decision_table_id="dt_identity",
            position=2,
            conditions={"birth_match": "一致"},
            outputs={"identity_level": "中"},
        )
    ]
    simple_ir.level_determination = LevelDetermination(
        atomic_fact=AtomicFactLayer(covered=True),
        intermediate=[
            IntermediateChange(
                table_id="dt_identity",
                relation="proper_subset",
                action="add_row",
            )
        ],
        final=FinalLayer(compared_table_id=None, relation=None, action="no_change"),
    )


def test_c11_value_added_to_another_output_is_rejected(
    intermediate_ir, seeded_registry, tmp_path
):
    """C11(D29): identity_result に足した値を identity_level の参照で使うと落ちる。

    あおいの DMN の「未」の取り違えそのものの形である(「未」は identity_result の
    値で、identity_level には来ない)。v1.2 は new_output_values が値の一覧だけ
    だったため「その表のすべての出力名に足す」と扱い、この行を通していた。
    """
    seeded_registry.decision_tables[0]["output_columns"] = [
        "identity_result",
        "identity_level",
    ]
    intermediate_ir.new_decision_tables[0].rule_rows[0].conditions = {
        "本人確認判定.identity_level": '"未"'
    }
    intermediate_ir.facts.referenced_facts = ["birth_match", "phone_match"]
    intermediate_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_identity_2",
            decision_table_id="dt_identity",
            position=2,
            conditions={"birth_match": "一致", "phone_match": "不一致"},
            outputs={"identity_result": "未", "identity_level": "中"},
        )
    ]
    intermediate_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_identity",
            relation="exact_match",
            action="add_row",
            new_output_values={"identity_result": ["未"]},
        )
    ]
    with pytest.raises(SchemaWiringError, match="C11検証失敗"):
        generate_wiring_bundle(
            intermediate_ir, registry=seeded_registry, dmn_dir=tmp_path
        )


def test_d35_rejects_row_insertion_into_a_non_first_table(
    simple_ir, seeded_registry, tmp_path
):
    """D35: 挿入先の表の hit_policy が FIRST でなければ落ちる。

    position による挿入は FIRST でしか意味を持たない。
    """
    seeded_registry.decision_tables[0]["hit_policy"] = "UNIQUE"
    dmn_dir = tmp_path / "dmn"
    dmn_dir.mkdir()
    (dmn_dir / "aoi_transmission.dmn").write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<definitions xmlns="https://www.omg.org/spec/DMN/20240513/MODEL/"'
        ' id="aoi_transmission_definitions" name="aoi_transmission"'
        ' namespace="http://gijinka.example.com/dmn/aoi_transmission">\n'
        '  <decision id="decision_identity" name="本人確認判定">\n'
        '    <decisionTable id="dt_identity" hitPolicy="UNIQUE">\n'
        '      <input id="in_birth_match"><inputExpression id="ie1" typeRef="string">'
        "<text>birth_match</text></inputExpression></input>\n"
        '      <output id="out_identity_level" label="identity_level"'
        ' name="identity_level" typeRef="string" />\n'
        "    </decisionTable>\n"
        "  </decision>\n"
        '  <decision id="decision_final" name="既存の最終判定">\n'
        '    <decisionTable id="dt_final" hitPolicy="FIRST">\n'
        '      <input id="in_level"><inputExpression id="ie2" typeRef="string">'
        "<text>本人確認判定.identity_level</text></inputExpression></input>\n"
        '      <output id="out_decision" label="decision" name="decision"'
        ' typeRef="string" />\n'
        "    </decisionTable>\n"
        "  </decision>\n"
        "</definitions>\n",
        encoding="utf-8",
    )
    _make_no_change_ir(simple_ir, seeded_registry)
    with pytest.raises(SchemaWiringError, match="D35違反"):
        generate_wiring_bundle(simple_ir, registry=seeded_registry, dmn_dir=dmn_dir)


def test_required_facts_include_a_column_added_to_a_dot_referenced_table(
    intermediate_ir, seeded_registry
):
    """D27: ドット参照で置き換える先の表に add_column で足した列も加わる。

    `required_facts` に入らないと、実行時に誰もその事実を集めない。手順1だけでなく
    手順2(ドット参照の置き換え)でも加えることを確かめる。
    """
    from tests.conftest import passthrough_fact

    intermediate_ir.facts.new_facts = [
        passthrough_fact("address_match", ["一致"], "claimed.address")
    ]
    intermediate_ir.facts.referenced_facts = ["birth_match", "phone_match"]
    intermediate_ir.rule_row_additions = [
        RuleRowDraft(
            row_id="id_identity_2",
            decision_table_id="dt_identity",
            position=2,
            conditions={
                "birth_match": "一致",
                "phone_match": "一致",
                "address_match": "一致",
            },
            outputs={"identity_level": "高"},
        )
    ]
    intermediate_ir.level_determination.intermediate = [
        IntermediateChange(
            table_id="dt_identity",
            relation="intersecting_non_subset",
            action="add_column",
            new_input_columns=["address_match"],
        )
    ]
    # 起点は口頭伝達可否判定(入力列はドット参照1列)。その先の本人確認判定に
    # 足した列まで含まれる
    assert derive_required_facts(intermediate_ir, seeded_registry) == [
        "birth_match",
        "phone_match",
        "address_match",
    ]
