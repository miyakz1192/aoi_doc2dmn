"""受け入れ経路(レベル2・中間判定への行追加・列の追加)のテスト。

第17回 HANDOFF タスク5。初期登録した**あおいの登録簿**
(`registry/registry.json` と `registry/dmn/aoi_transmission.dmn`)を入力にして、
次の3つを確かめる。

| テスト | 条文 | 確かめること |
| --- | --- | --- |
| 中間判定への行追加 | 第901条 | 既存モデルへの行の挿入(D15)、D16 の作り直し、C11 |
| レベル2 | 第902条 | 送信可否判定への行の挿入(`final.action=add_rows`)と C7(D15) |
| 列の追加 | 第903条 | 既存の全行が「-」で埋まること、required_facts に新しい列が入ること(D19) |
| 最終判定は変えない | 第904条 | `final.action=no_change`(D30)、ルートが1本にまとまること |

`referenced_facts` は D25 の基準(IR が書いた行と表で使う既存 fact)で書く。とくに
第902条は、条文が使う fact が contact_consent だけなので1件だけになる(v1.2 では
最終判定の全 fact 7件を列挙していた)。

各テストに、挿入後の DMN を評価するシナリオを2つ置く。1つは追加した行・列に当たる
ケース、もう1つは**既定の行(ワイルドカード)に落ちる**ケースである(設計書6.2・U2)。

## IR は「テスト用」であることが分かる名前にしてある

HANDOFF タスク5の指示どおり、正解 IR と混同しないよう次の規約にしている。

- `clause_ref` は「(テスト用) 架空規程 第9xx条」
- 新しく作る判定・決定表・行の ID は `test_` で始める
- `registered_by` は `pytest`

実在の条文ではないので、`testdata/ir_gold/` に置く正解 IR とは別物である。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_generate.bootstrap_aoi import DMN_MODEL  # noqa: E402
from src.stage3_generate.dmn_engine import DmnModel  # noqa: E402
from src.stage3_generate.ir_schema import (  # noqa: E402
    ActionRef,
    AtomicFactLayer,
    ClauseIR,
    CompoundFactApplication,
    DecisionTarget,
    FactDef,
    FactUsage,
    FinalLayer,
    IntermediateChange,
    LevelDetermination,
    NewDecisionTableDraft,
    Provenance,
    RuleRowDraft,
    SourceRef,
    ValueAddition,
    build_review_gates,
)
from src.stage3_generate.registry import Registry  # noqa: E402
from src.stage3_generate.wiring_generator import (  # noqa: E402
    existing_facts_used_by_ir,
    generate_wiring_bundle,
)

from tests.conftest import make_axis  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "registry" / "registry.json"
DMN_DIR = REPO_ROOT / "registry" / "dmn"
APPROVER = "テスト承認者"

# あおいの DMN にある既存の判定(初期登録済み)
DECISION_IDENTITY = "decision_identity"
DT_IDENTITY = "dt_identity"
DECISION_TRANSMISSION = "decision_transmission"
DT_TRANSMISSION = "dt_transmission"
# 既存の最終判定の呼び出し口(層1の entry_points)
EXISTING_ROUTE = {"actor": "あおい", "action": "外部送信"}


@pytest.fixture
def aoi_registry() -> Registry:
    """初期登録したあおいの登録簿。無ければテストを飛ばす。"""
    if not REGISTRY_PATH.exists() or not (DMN_DIR / f"{DMN_MODEL}.dmn").exists():
        pytest.skip(
            "あおいの初期登録が未実施です。"
            "`python3 -m src.stage3_generate.bootstrap_aoi` を実行してください。"
        )
    registry = Registry.load(REGISTRY_PATH)
    if registry.find_decision_table(decision_id=DECISION_TRANSMISSION) is None:
        pytest.skip("登録簿に送信可否判定がありません(初期登録をやり直してください)。")
    return registry


def _assemble_test_ir(
    *,
    clause_ref: str,
    action_normalized: str,
    action_is_new: bool,
    target: DecisionTarget,
    facts: FactUsage,
    level: LevelDetermination,
    new_tables: list[NewDecisionTableDraft],
    row_additions: list[RuleRowDraft],
) -> ClauseIR:
    axis1, axis2 = make_axis("ハード制約"), make_axis("宣言的")
    action = ActionRef(
        surface="(テスト用)",
        normalized=action_normalized,
        parent="情報開示",
        is_new=action_is_new,
        approved_by=APPROVER if action_is_new else None,
    )
    return ClauseIR(
        clause_ref=clause_ref,
        clause_text="(テスト用) 受け入れ経路の確認のための架空の条文。",
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
        # D31: option_taken は導出するので、手で書くのは拒否の印だけ
        compound_fact_application=CompoundFactApplication(
            rejected_in_primitive=False, rationale="(テスト用)"
        ),
        provenance=Provenance(
            origin="regulation_clause",
            clause_ref=clause_ref,
            registered_by="pytest",
            registered_at="2026-10-23T00:00:00+00:00",
        ),
        review_gates=build_review_gates(axis1, axis2, action, facts, level),
    )


def _rules_of(dmn_xml: str, decision_table_id: str) -> list[list[str]]:
    """生成した DMN XML から、指定した決定表の全行のセル(入力)を取り出す。"""
    from xml.etree import ElementTree as ET

    ns = {"d": "https://www.omg.org/spec/DMN/20240513/MODEL/"}
    root = ET.fromstring(dmn_xml)
    for table in root.iter("{https://www.omg.org/spec/DMN/20240513/MODEL/}decisionTable"):
        if table.get("id") != decision_table_id:
            continue
        return [
            [
                (e.find("d:text", ns).text or "").strip()
                for e in rule.findall("d:inputEntry", ns)
            ]
            for rule in table.findall("d:rule", ns)
        ]
    raise AssertionError(f"決定表 {decision_table_id} が DMN XML にありません")


def _evaluate(bundle, tmp_path, decision_id: str, facts: dict) -> dict:
    paths = bundle.write_all(tmp_path, DMN_MODEL)
    return DmnModel(str(paths["dmn"])).evaluate(decision_id, facts)


# --------------------------------------------------------------------------
# 1. 中間判定(本人確認判定)への行追加
# --------------------------------------------------------------------------

VERBAL_DECISION_ID = "test_decision_verbal"
VERBAL_TABLE_ID = "test_dt_verbal"


@pytest.fixture
def intermediate_row_ir() -> ClauseIR:
    """本人確認判定に行を1本足し、その出力を参照する最終判定を新しく作る IR。

    reference 実装の第14条(口頭伝達)と同じ形。D17 により、新しい最終判定は
    参照先(本人確認判定)と同じ DMN モデル `aoi_transmission` に入れる。
    """
    new_table = NewDecisionTableDraft(
        decision_table_id=VERBAL_TABLE_ID,
        decision_id=VERBAL_DECISION_ID,
        decision_name="テスト口頭伝達可否判定",
        input_columns=["本人確認判定.identity_level"],
        rule_rows=[
            RuleRowDraft(
                row_id="test_verbal_r1",
                decision_table_id=VERBAL_TABLE_ID,
                position=1,
                conditions={"本人確認判定.identity_level": '"高","中"'},
                outputs={"decision": "許可", "reason_code": "test_identity_ok"},
            ),
            RuleRowDraft(
                row_id="test_verbal_r2",
                decision_table_id=VERBAL_TABLE_ID,
                position=2,
                conditions={"本人確認判定.identity_level": "-"},
                outputs={"decision": "禁止", "reason_code": "test_identity_short"},
            ),
        ],
    )
    # 氏名・生年月日が一致し電話が不一致のとき、確認レベルを「中」にする行。
    # 既存の行(氏名一致・生年月日不一致)より前に入れないと当たらないので position=4。
    added_row = RuleRowDraft(
        row_id="test_identity_r_phone_mismatch",
        decision_table_id=DT_IDENTITY,
        position=4,
        conditions={
            "name_match": "一致",
            "birth_match": "一致",
            "phone_match": "不一致",
        },
        outputs={"identity_result": "済", "identity_level": "中"},
    )
    return _assemble_test_ir(
        clause_ref="(テスト用) 架空規程 第901条",
        action_normalized="テスト口頭伝達",
        action_is_new=True,
        target=DecisionTarget(
            dmn_model=DMN_MODEL, entry_decision_id=VERBAL_DECISION_ID
        ),
        facts=FactUsage(
            referenced_facts=["name_match", "birth_match", "phone_match"],
            # phone_match の possible_values に「不一致」が無いので足す(D14)
            value_additions={
                "phone_match": [ValueAddition(value="不一致", approved_by=APPROVER)]
            },
        ),
        level=LevelDetermination(
            atomic_fact=AtomicFactLayer(covered=True),
            intermediate=[
                IntermediateChange(
                    table_id=DT_IDENTITY,
                    relation="exact_match",
                    action="add_row",
                    # D36: 本人確認判定は「済」も「中」も既に出している
                    # (id_r2・id_r3)ので、足す出力値は無い
                    new_output_values={},
                )
            ],
            final=FinalLayer(
                compared_table_id=None, relation=None, action="new_table"
            ),
        ),
        new_tables=[new_table],
        row_additions=[added_row],
    )


def test_intermediate_row_option_taken_is_derived_per_table(intermediate_row_ir):
    """D31: 1つの IR が既存の表への行追加と新しい表を両方作ることを導出で表す。"""
    assert intermediate_row_ir.option_taken == {
        VERBAL_TABLE_ID: "new_table_row",
        DT_IDENTITY: "existing_table_row_addition",
    }


def test_intermediate_row_referenced_facts_match_d25(intermediate_row_ir):
    """D25: IR が書いた行で使う既存 fact(3件)が referenced_facts と一致する。"""
    assert existing_facts_used_by_ir(intermediate_row_ir) == {
        "name_match",
        "birth_match",
        "phone_match",
    }
    assert set(intermediate_row_ir.facts.referenced_facts) == {
        "name_match",
        "birth_match",
        "phone_match",
    }


def test_intermediate_row_is_inserted_at_the_declared_position(
    intermediate_row_ir, aoi_registry
):
    """D15: 既存モデルの本人確認判定に、position の位置で行が挿入される。"""
    bundle = generate_wiring_bundle(
        intermediate_row_ir, registry=aoi_registry, dmn_dir=DMN_DIR
    )
    rules = _rules_of(bundle.dmn_xml, DT_IDENTITY)
    assert len(rules) == 7, "6行 + 追加1行"
    # 4行目(0起点で3)が追加した行
    assert rules[3] == ['"一致"', '"一致"', '"不一致"']
    # 元の4行目は5行目に押し出される(FIRST の順序が保たれる)
    assert rules[4] == ['"一致"', '"不一致"', "-"]
    # 新しい最終判定も同じモデルに入る(D17)
    assert f'id="{VERBAL_DECISION_ID}"' in bundle.dmn_xml
    assert 'id="decision_transmission"' in bundle.dmn_xml


def test_d16_rebuilds_every_affected_final_decision(intermediate_row_ir, aoi_registry):
    """D16: 中間判定を使う最終判定が、すべてルーティング表に出る。

    本人確認判定に行を足したので、それを使う送信可否判定のルートも作り直す。
    既存の最終判定の actor・action は層1の entry_points から引く。
    """
    bundle = generate_wiring_bundle(
        intermediate_row_ir, registry=aoi_registry, dmn_dir=DMN_DIR
    )
    routes = bundle.routing_table["routes"]
    assert len(routes) == 2, "IR の起点 + 影響を受ける既存の最終判定"

    # 1本目は IR 自身のルート
    assert routes[0]["actor"] == "あおい"
    assert routes[0]["action"] == "テスト口頭伝達"
    assert routes[0]["decision_id"] == VERBAL_DECISION_ID
    assert routes[0]["required_facts"] == ["name_match", "birth_match", "phone_match"]

    # 2本目は層1の entry_points から作り直した既存のルート
    assert routes[1]["actor"] == EXISTING_ROUTE["actor"]
    assert routes[1]["action"] == EXISTING_ROUTE["action"]
    assert routes[1]["decision_id"] == DECISION_TRANSMISSION
    # 送信可否判定は情報分類判定も使うので、そちらの fact も集める必要がある
    assert set(routes[1]["required_facts"]) == {
        "name_match",
        "birth_match",
        "phone_match",
        "has_medical",
        "has_pii",
        "identifiable",
        "contact_consent",
    }

    # バインディング設定は全ルートの和集合(設計書4.3)
    assert {f["variable"] for f in bundle.bindings["facts"]} == set(
        routes[0]["required_facts"]
    ) | set(routes[1]["required_facts"])


def test_intermediate_row_value_addition_lands_in_registry_and_bindings(
    intermediate_row_ir, aoi_registry
):
    """D14: 追加した値が層3と履歴に承認者付きで残り、バインディングにも出る。"""
    bundle = generate_wiring_bundle(
        intermediate_row_ir, registry=aoi_registry, dmn_dir=DMN_DIR
    )
    layer3 = {f["variable"]: f for f in bundle.registry["fact_registry"]}
    assert "不一致" in layer3["phone_match"]["possible_values"]
    history = layer3["phone_match"]["history"][-1]
    assert history["change"] == "value_addition"
    assert history["added_values"] == [{"value": "不一致", "approved_by": APPROVER}]

    bound = {f["variable"]: f for f in bundle.bindings["facts"]}
    assert "不一致" in bound["phone_match"]["possible_values"]
    # 承認済みなので new_fact_unapproved は立たない(D14)
    assert bundle.registry["review"]["gates"] == []


@pytest.mark.parametrize(
    "name,facts,expected_decision,expected_reason",
    [
        (
            "追加した行に当たる(電話だけ不一致 → 確認レベル中 → 許可)",
            {"name_match": "一致", "birth_match": "一致", "phone_match": "不一致"},
            "許可",
            "test_identity_ok",
        ),
        (
            "既定の行(ワイルドカード)に落ちる(氏名不一致 → 不一致検出 → 禁止)",
            {"name_match": "不一致", "birth_match": "一致", "phone_match": "一致"},
            "禁止",
            "test_identity_short",
        ),
    ],
)
def test_intermediate_row_dmn_scenarios(
    intermediate_row_ir,
    aoi_registry,
    tmp_path,
    name,
    facts,
    expected_decision,
    expected_reason,
):
    """挿入後の DMN を評価する。2件目は既定の行に落ちるケース(U2)。"""
    bundle = generate_wiring_bundle(
        intermediate_row_ir, registry=aoi_registry, dmn_dir=DMN_DIR
    )
    result = _evaluate(bundle, tmp_path, VERBAL_DECISION_ID, facts)
    assert result["decision"] == expected_decision, name
    assert result["reason_code"] == expected_reason, name


# --------------------------------------------------------------------------
# 2. レベル2(送信可否判定への行追加)
# --------------------------------------------------------------------------


@pytest.fixture
def level2_ir() -> ClauseIR:
    """既存の最終判定(送信可否判定)に行を足す IR(`final.action=add_rows`)。

    連絡同意が「保留」のときは送信を禁止する行を、同意なしの行(tr_r1)の直後に
    入れる。位置が後ろだと、感度「低」の行(tr_r7 = 許可)に先に当たってしまう。
    """
    added_row = RuleRowDraft(
        row_id="test_transmission_r_consent_pending",
        decision_table_id=DT_TRANSMISSION,
        position=2,
        conditions={
            "情報分類判定.sensitivity": "-",
            "本人確認判定.identity_level": "-",
            "contact_consent": "保留",
        },
        outputs={"decision": "禁止", "reason_code": "test_consent_pending"},
    )
    return _assemble_test_ir(
        clause_ref="(テスト用) 架空規程 第902条",
        # 既存のルート(あおい×外部送信)に行を足すので、新しい Action ではない
        action_normalized=EXISTING_ROUTE["action"],
        action_is_new=False,
        target=DecisionTarget(
            dmn_model=DMN_MODEL, entry_decision_id=DECISION_TRANSMISSION
        ),
        # D25: この条文が使う既存 fact は contact_consent だけ(足した行の条件の
        # うち「-」でない列は contact_consent の1列)。v1.2 は最終判定の全 fact
        # 7件の列挙を強いていた
        facts=FactUsage(
            referenced_facts=["contact_consent"],
            value_additions={
                "contact_consent": [ValueAddition(value="保留", approved_by=APPROVER)]
            },
        ),
        level=LevelDetermination(
            atomic_fact=AtomicFactLayer(covered=True),
            intermediate=[],
            final=FinalLayer(
                compared_table_id=DT_TRANSMISSION,
                relation="exact_match",
                action="add_rows",
            ),
        ),
        new_tables=[],
        row_additions=[added_row],
    )


def test_level2_referenced_facts_are_only_the_clause_fact(level2_ir, aoi_registry):
    """D25 の主な確認点: レベル2でも、条文が使う fact だけを列挙すれば通る。

    最終判定(送信可否判定)の required_facts は7件だが、この条文が言及したのは
    連絡同意だけである。7件の列挙なしでクロス検証が通ることを確かめる。
    """
    assert existing_facts_used_by_ir(level2_ir) == {"contact_consent"}
    assert level2_ir.facts.referenced_facts == ["contact_consent"]
    bundle = generate_wiring_bundle(level2_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    # ルーティング表とバインディング設定には、集めるべき7件すべてが出る
    assert len(bundle.routing_table["routes"][0]["required_facts"]) == 7
    assert len(bundle.bindings["facts"]) == 7


def test_level2_option_taken_is_existing_table_row_addition(level2_ir):
    """D31: 既存の表に行を足すだけなので existing_table_row_addition。"""
    assert level2_ir.option_taken == {DT_TRANSMISSION: "existing_table_row_addition"}


def test_level2_row_is_inserted_and_c7_passes(level2_ir, aoi_registry):
    """D15・C7: add_rows でも既存モデルに挿入され、起点の判定が DMN にある。"""
    bundle = generate_wiring_bundle(level2_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    rules = _rules_of(bundle.dmn_xml, DT_TRANSMISSION)
    assert len(rules) == 8, "7行 + 追加1行"
    assert rules[1] == ["-", "-", '"保留"']
    # C7(生成後の検証)が通っている = 起点の判定が DMN XML と層1にある
    assert f'id="{DECISION_TRANSMISSION}"' in bundle.dmn_xml
    assert level2_ir.level_determination.outcome == "level2_row_addition"


def test_level2_route_is_not_duplicated(level2_ir, aoi_registry):
    """既存のルートと同じ actor・action なら、ルーティング表の行は1本にまとまる。"""
    bundle = generate_wiring_bundle(level2_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    routes = bundle.routing_table["routes"]
    assert len(routes) == 1
    assert routes[0]["actor"] == EXISTING_ROUTE["actor"]
    assert routes[0]["action"] == EXISTING_ROUTE["action"]
    assert routes[0]["decision_id"] == DECISION_TRANSMISSION


@pytest.mark.parametrize(
    "name,facts,expected_decision,expected_reason",
    [
        (
            "追加した行に当たる(同意保留 → 公開情報でも禁止)",
            {
                "name_match": "一致",
                "birth_match": "一致",
                "phone_match": "一致",
                "has_medical": "含まない",
                "has_pii": "含まない",
                "identifiable": "不可能_公開",
                "contact_consent": "保留",
            },
            "禁止",
            "test_consent_pending",
        ),
        (
            "既定の行(ワイルドカード)に落ちる(公開情報・同意 → tr_r7 で許可)",
            {
                "name_match": "一致",
                "birth_match": "一致",
                "phone_match": "一致",
                "has_medical": "含まない",
                "has_pii": "含まない",
                "identifiable": "不可能_公開",
                "contact_consent": "同意",
            },
            "許可",
            "公開情報",
        ),
    ],
)
def test_level2_dmn_scenarios(
    level2_ir,
    aoi_registry,
    tmp_path,
    name,
    facts,
    expected_decision,
    expected_reason,
):
    """挿入後の DMN を評価する。2件目は既定の行に落ちるケース(U2)。

    1件目が「禁止」になるのは、追加した行が tr_r7(感度低 → 許可)より前にあるため。
    position を守って挿入していることの確認でもある。
    """
    bundle = generate_wiring_bundle(level2_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    result = _evaluate(bundle, tmp_path, DECISION_TRANSMISSION, facts)
    assert result["decision"] == expected_decision, name
    assert result["reason_code"] == expected_reason, name


# --------------------------------------------------------------------------
# 3. 列の追加(add_column)
# --------------------------------------------------------------------------

ADDR_DECISION_ID = "test_decision_address"
ADDR_TABLE_ID = "test_dt_address"


@pytest.fixture
def add_column_ir() -> ClauseIR:
    """本人確認判定に住所照合の列を足す IR(`intermediate.action=add_column`)。"""
    new_fact = FactDef(
        variable="address_match",
        primitive="fuzzy_match_name",
        possible_values=["一致", "不一致"],
        sources={
            "claimed": SourceRef(from_="action_request", path="claimed_address"),
            "registered": SourceRef(from_="master", path="patient.address"),
        },
        approved_by=APPROVER,
    )
    new_table = NewDecisionTableDraft(
        decision_table_id=ADDR_TABLE_ID,
        decision_id=ADDR_DECISION_ID,
        decision_name="テスト住所確認可否判定",
        input_columns=["本人確認判定.identity_level"],
        rule_rows=[
            RuleRowDraft(
                row_id="test_address_r1",
                decision_table_id=ADDR_TABLE_ID,
                position=1,
                conditions={"本人確認判定.identity_level": '"高"'},
                outputs={"decision": "許可", "reason_code": "test_addr_ok"},
            ),
            RuleRowDraft(
                row_id="test_address_r2",
                decision_table_id=ADDR_TABLE_ID,
                position=2,
                conditions={"本人確認判定.identity_level": "-"},
                outputs={"decision": "禁止", "reason_code": "test_addr_short"},
            ),
        ],
    )
    # 氏名が一致し、生年月日・電話が未提供でも、住所が一致すれば確認レベル「中」
    added_row = RuleRowDraft(
        row_id="test_identity_r_address",
        decision_table_id=DT_IDENTITY,
        position=4,
        conditions={
            "name_match": "一致",
            "birth_match": "未提供",
            "phone_match": "未提供",
            "address_match": "一致",
        },
        outputs={"identity_result": "済", "identity_level": "中"},
    )
    return _assemble_test_ir(
        clause_ref="(テスト用) 架空規程 第903条",
        action_normalized="テスト住所確認",
        action_is_new=True,
        target=DecisionTarget(dmn_model=DMN_MODEL, entry_decision_id=ADDR_DECISION_ID),
        facts=FactUsage(
            new_facts=[new_fact],
            referenced_facts=["name_match", "birth_match", "phone_match"],
        ),
        level=LevelDetermination(
            atomic_fact=AtomicFactLayer(covered=True),
            intermediate=[
                IntermediateChange(
                    table_id=DT_IDENTITY,
                    relation="intersecting_non_subset",
                    action="add_column",
                    # D29: 足す行の確認レベル「中」は本人確認判定に既にある値
                    # (r2・r3)なので、足す出力値は無い
                    new_output_values={},
                    new_input_columns=["address_match"],
                )
            ],
            final=FinalLayer(
                compared_table_id=None, relation=None, action="new_table"
            ),
        ),
        new_tables=[new_table],
        row_additions=[added_row],
    )


def test_add_column_referenced_facts_exclude_the_new_fact(add_column_ir):
    """D25: 足した列(address_match)は new_facts なので referenced_facts に入らない。"""
    assert existing_facts_used_by_ir(add_column_ir) == {
        "name_match",
        "birth_match",
        "phone_match",
    }
    assert set(add_column_ir.facts.referenced_facts) == {
        "name_match",
        "birth_match",
        "phone_match",
    }
    assert add_column_ir.option_taken == {
        ADDR_TABLE_ID: "new_table_row",
        DT_IDENTITY: "existing_table_row_addition",
    }


def test_add_column_fills_existing_rows_with_wildcard(add_column_ir, aoi_registry):
    """D19: 足した列は、既存の全行が「-」で埋まる。"""
    bundle = generate_wiring_bundle(add_column_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    rules = _rules_of(bundle.dmn_xml, DT_IDENTITY)
    assert len(rules) == 7
    assert all(len(cells) == 4 for cells in rules), "全行が4列になる"
    # 追加した行(4行目)だけが新しい列に値を持つ
    assert rules[3] == ['"一致"', '"未提供"', '"未提供"', '"一致"']
    added_column_cells = [cells[3] for i, cells in enumerate(rules) if i != 3]
    assert added_column_cells == ["-"] * 6
    # inputData も増える
    assert 'name="address_match"' in bundle.dmn_xml


def test_add_column_updates_registry_layer1_and_layer2(add_column_ir, aoi_registry):
    """D19: 層1の input_columns に足され、層2の既存の行も「-」で埋まる。"""
    bundle = generate_wiring_bundle(add_column_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    layer1 = {
        e["decision_table_id"]: e for e in bundle.registry["decision_tables"]
    }
    assert layer1[DT_IDENTITY]["input_columns"] == [
        "name_match",
        "birth_match",
        "phone_match",
        "address_match",
    ]
    assert layer1[DT_IDENTITY]["history"][-1]["change"] == "column_addition"

    existing_rows = [
        r
        for r in bundle.registry["rule_rows"]
        if r["decision_table_id"] == DT_IDENTITY and r["row_id"].startswith("id_r")
    ]
    assert existing_rows, "初期登録の行がある"
    assert all(r["conditions"]["address_match"] == "-" for r in existing_rows)

    # 新しく作った最終判定の層1には output_columns と entry_points が入る(D12・D16)
    assert layer1[ADDR_TABLE_ID]["output_columns"] == ["decision", "reason_code"]
    assert layer1[ADDR_TABLE_ID]["entry_points"] == [
        {"actor": "あおい", "action": "テスト住所確認"}
    ]


def test_add_column_appears_in_required_facts(add_column_ir, aoi_registry):
    """D19: 足した列が required_facts に入る(実行時に集める対象になる)。"""
    bundle = generate_wiring_bundle(add_column_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    route = bundle.routing_table["routes"][0]
    assert route["decision_id"] == ADDR_DECISION_ID
    assert route["required_facts"] == [
        "name_match",
        "birth_match",
        "phone_match",
        "address_match",
    ]
    assert "address_match" in {f["variable"] for f in bundle.bindings["facts"]}


@pytest.mark.parametrize(
    "name,facts,expected_decision,expected_reason",
    [
        (
            "既定の行(ワイルドカード)に落ちる(3要素一致 → 既存の1行目 → 確認レベル高)",
            {
                "name_match": "一致",
                "birth_match": "一致",
                "phone_match": "一致",
                "address_match": "不一致",
            },
            "許可",
            "test_addr_ok",
        ),
        (
            "追加した行に当たる(住所だけ一致 → 確認レベル中 → 禁止)",
            {
                "name_match": "一致",
                "birth_match": "未提供",
                "phone_match": "未提供",
                "address_match": "一致",
            },
            "禁止",
            "test_addr_short",
        ),
    ],
)
def test_add_column_dmn_scenarios(
    add_column_ir,
    aoi_registry,
    tmp_path,
    name,
    facts,
    expected_decision,
    expected_reason,
):
    """挿入後の DMN を評価する。1件目は既存の行の「-」に当たるケース(U2)。"""
    bundle = generate_wiring_bundle(add_column_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    result = _evaluate(bundle, tmp_path, ADDR_DECISION_ID, facts)
    assert result["decision"] == expected_decision, name
    assert result["reason_code"] == expected_reason, name


# --------------------------------------------------------------------------
# 4. 最終判定は変えない(no_change)
# --------------------------------------------------------------------------


@pytest.fixture
def no_change_ir() -> ClauseIR:
    """中間判定にだけ行を足し、最終判定は変えない IR(`final.action=no_change`。D30)。

    条文の例: 「氏名と生年月日が一致すれば、電話番号が一致しなくても確認レベルを
    中とする」。本人確認判定に行を1本足すだけで、新しい最終判定は作らない。

    `target.entry_decision_id` には影響を受ける既存の最終判定(送信可否判定)を入れ、
    actor・action はその `entry_points`(あおい × 外部送信)と揃える(C9条件7・8)。
    電話番号の列は「-」なので、D25 の基準では phone_match は「IR が使う既存 fact」
    に入らない。
    """
    added_row = RuleRowDraft(
        row_id="test_identity_r_birth_only",
        decision_table_id=DT_IDENTITY,
        position=3,
        conditions={"name_match": "一致", "birth_match": "一致", "phone_match": "-"},
        outputs={"identity_result": "済", "identity_level": "中"},
    )
    return _assemble_test_ir(
        clause_ref="(テスト用) 架空規程 第904条",
        action_normalized=EXISTING_ROUTE["action"],
        action_is_new=False,
        target=DecisionTarget(
            dmn_model=DMN_MODEL, entry_decision_id=DECISION_TRANSMISSION
        ),
        facts=FactUsage(referenced_facts=["name_match", "birth_match"]),
        level=LevelDetermination(
            atomic_fact=AtomicFactLayer(covered=True),
            intermediate=[
                IntermediateChange(
                    table_id=DT_IDENTITY,
                    relation="proper_subset",
                    action="add_row",
                    # 確認レベル「中」は既にある値なので、足す出力値は無い
                    new_output_values={},
                )
            ],
            final=FinalLayer(compared_table_id=None, relation=None, action="no_change"),
        ),
        new_tables=[],
        row_additions=[added_row],
    )


def test_no_change_outcome_and_option_taken(no_change_ir):
    """D30・D31: outcome は level2_intermediate_only、導出は行追加だけ。"""
    assert no_change_ir.level_determination.outcome == "level2_intermediate_only"
    assert no_change_ir.option_taken == {DT_IDENTITY: "existing_table_row_addition"}
    # D25: 「-」の列(phone_match)は IR が使う既存 fact に入らない
    assert existing_facts_used_by_ir(no_change_ir) == {"name_match", "birth_match"}


def test_no_change_routing_table_has_one_row(no_change_ir, aoi_registry):
    """D30: IR 自身のルートと、D16 で洗い出した既存のルートが1行にまとまる。

    どちらも「あおい × 外部送信 → 送信可否判定」なので、同じ actor・action・
    decision_id の行は1つにする(設計書4.1)。
    """
    bundle = generate_wiring_bundle(no_change_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    routes = bundle.routing_table["routes"]
    assert len(routes) == 1
    assert routes[0]["actor"] == EXISTING_ROUTE["actor"]
    assert routes[0]["action"] == EXISTING_ROUTE["action"]
    assert routes[0]["decision_id"] == DECISION_TRANSMISSION
    # 送信可否判定を評価するのに集める事実は7件すべて
    assert set(routes[0]["required_facts"]) == {
        "name_match",
        "birth_match",
        "phone_match",
        "has_medical",
        "has_pii",
        "identifiable",
        "contact_consent",
    }
    # 新しい決定表は作らないので、層1は増えない
    assert len(bundle.registry["decision_tables"]) == len(aoi_registry.decision_tables)


def test_no_change_inserts_the_row_into_the_intermediate_table(
    no_change_ir, aoi_registry
):
    """中間判定には行が入る(最終判定は変えない)。"""
    bundle = generate_wiring_bundle(no_change_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    identity = _rules_of(bundle.dmn_xml, DT_IDENTITY)
    assert len(identity) == 7, "6行 + 追加1行"
    assert identity[2] == ['"一致"', '"一致"', "-"]
    assert len(_rules_of(bundle.dmn_xml, DT_TRANSMISSION)) == 7, "送信可否判定は7行のまま"


@pytest.mark.parametrize(
    "name,facts,expected_decision,expected_reason",
    [
        (
            "追加した行に当たる(氏名・生年月日一致・電話不一致 → 確認レベル中 → 許可)",
            {
                "name_match": "一致",
                "birth_match": "一致",
                "phone_match": "不一致",
                "has_medical": "含まない",
                "has_pii": "含む",
                "identifiable": "可能",
                "contact_consent": "同意",
            },
            "許可",
            "本人確認充足",
        ),
        (
            "既定の行(ワイルドカード)に落ちる(氏名不一致 → id_r5 → 不一致検出 → 禁止)",
            {
                "name_match": "不一致",
                "birth_match": "一致",
                "phone_match": "一致",
                "has_medical": "含まない",
                "has_pii": "含む",
                "identifiable": "可能",
                "contact_consent": "同意",
            },
            "禁止",
            "本人未確認",
        ),
    ],
)
def test_no_change_dmn_scenarios(
    no_change_ir,
    aoi_registry,
    tmp_path,
    name,
    facts,
    expected_decision,
    expected_reason,
):
    """挿入後の DMN を、送信可否判定まで通して評価する。

    1件目は、行を足さなければ id_r6(情報不足)に落ちて「禁止・本人未確認」に
    なるケースである(足した行が効いていることの確認)。2件目は本人確認判定の
    既定の行(ワイルドカード)に落ちるケース(U2)。
    """
    bundle = generate_wiring_bundle(no_change_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    result = _evaluate(bundle, tmp_path, DECISION_TRANSMISSION, facts)
    assert result["decision"] == expected_decision, name
    assert result["reason_code"] == expected_reason, name


# --------------------------------------------------------------------------
# 5. バインディング設定の sources_undefined(D33)
# --------------------------------------------------------------------------


def test_bindings_mark_facts_whose_sources_are_undefined(level2_ir, aoi_registry):
    """D33: 初期登録で取得元が分からなかった fact に sources_undefined を付ける。

    空の sources をそのまま出すと、取得元があるのか無いのかが読み取れない。
    埋めると創作になるので、空のまま印を付けて出す(埋めるのは U4 のとき)。
    """
    bundle = generate_wiring_bundle(level2_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    assert len(bundle.bindings["facts"]) == 7
    for fact in bundle.bindings["facts"]:
        assert fact["sources"] == {}, fact["variable"]
        assert fact["sources_undefined"] is True, fact["variable"]


def test_bindings_do_not_mark_facts_that_have_sources(add_column_ir, aoi_registry):
    """D33: 取得元が分かっている fact には印を付けない。"""
    bundle = generate_wiring_bundle(add_column_ir, registry=aoi_registry, dmn_dir=DMN_DIR)
    bound = {f["variable"]: f for f in bundle.bindings["facts"]}
    # この条文で定義した fact は sources を持つ
    assert set(bound["address_match"]["sources"]) == {"claimed", "registered"}
    assert "sources_undefined" not in bound["address_match"]
    # 初期登録分には印が付く
    assert bound["name_match"]["sources_undefined"] is True


def test_existing_model_keeps_its_namespace(intermediate_row_ir, aoi_registry):
    """D39: 既存の DMN モデルの namespace は変えない。

    あおいのモデルの namespace は `…/dmn/aoi` で、新しく作るモデルに当てる規則
    (`…/dmn/<dmn_model>` = `…/dmn/aoi_transmission`)とは食い違う。namespace は
    実行エンジンや利用側がモデルを識別する名前なので、後から変えると参照が切れる。
    新しい最終判定を同じモデルに足す場合も、元の namespace のままにする。
    """
    bundle = generate_wiring_bundle(
        intermediate_row_ir, registry=aoi_registry, dmn_dir=DMN_DIR
    )
    assert 'namespace="http://gijinka.example.com/dmn/aoi"' in bundle.dmn_xml
    assert (
        'namespace="http://gijinka.example.com/dmn/aoi_transmission"'
        not in bundle.dmn_xml
    )
    # 新しい判定は同じ definitions の中に入っている(D17)
    assert f'id="{VERBAL_DECISION_ID}"' in bundle.dmn_xml
