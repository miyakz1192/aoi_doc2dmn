"""事実の抽出の評価の単体テスト。前段設計書10.4節(P40・P42)。

| 確かめること | 根拠 |
| --- | --- |
| 正解の値は facts 欄と決定表の行から求める | 10.4.1節・D25 |
| 誤りの重さは6分類(重大3・中3) | P40 |
| 合格の条件は「すり抜けた重大な誤りが0件」 | P40・10.4.2節 |
| 教師ありで正解の fact を層3に足す(「その他」を含む) | P42・D45 |
| 評価の判断を含む値は評価から外し、件数だけ数える | FU2 |
| オラクルで通したときの上限と、その理由 | パートB の確認 |
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_frontstage.fact_extractor import (  # noqa: E402
    ConditionValue,
    FactExtractionResult,
    FactExtractionTrace,
    NewFactProposal,
)
from src.stage3_frontstage.fact_ledger import FactLedger, ValueMatch  # noqa: E402
from src.stage3_frontstage.run_fact_eval import (  # noqa: E402
    MIDDLE_KINDS,
    SEVERE_KINDS,
    gold_values,
    judge,
    load_items,
    run,
    teach,
)
from src.stage3_generate.ir_schema import (  # noqa: E402
    OTHER_VALUE,
    FactUsage,
    ReviewGates,
)
from src.stage3_generate.registry import Registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def items() -> list[dict]:
    return load_items(ROOT / "testdata" / "ir_gold")


@pytest.fixture(scope="module")
def oracle_run() -> dict:
    """オラクル(正解を返す偽の LLM)で5件を通した結果。Ollama は要らない。"""
    return run(
        ROOT / "testdata" / "ir_gold",
        ROOT / "registry" / "registry.json",
        use_oracle=True,
        verbose=False,
    )


def fake_result(
    *,
    values: list[tuple[str, str]],
    new: bool = True,
    matches: list[tuple[str, str]] | None = None,
    gates: ReviewGates | None = None,
) -> FactExtractionResult:
    """手で作った出力(突き合わせの単体テスト用)。

    `values` は (fact の名前, 値) の並び(新規候補)、`matches` は 3a で寄せた
    (fact の名前, 値)。
    """
    conditions = [
        ConditionValue(quote=value, value=value, kind="状況", in_clause=True)
        for _fact, value in [*values, *(matches or [])]
    ]
    proposals = []
    for fact, value in values:
        found = next((p for p in proposals if p.variable == fact), None)
        if found is None:
            proposals.append(
                NewFactProposal(variable=fact, primitive="passthrough", values=[value])
            )
        else:
            found.values.append(value)
    trace = FactExtractionTrace(
        conditions=conditions,
        proposals=proposals,
        matches=[
            ValueMatch(value=value, matches=[fact])
            for fact, value in (matches or [])
        ],
    )
    return FactExtractionResult(
        facts=FactUsage(), trace=trace, gates=gates or ReviewGates()
    )


# --------------------------------------------------------------------------
# 正解の値の求め方(10.4.1節)
# --------------------------------------------------------------------------


def test_the_gold_values_come_from_the_new_facts(items):
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    values = gold_values(golden)
    assert [(g.variable, g.value, g.is_new) for g in values] == [
        ("operator_role", "システム開発担当", True),
        ("target_environment", "本番環境", True),
        ("requested_privilege", "特権", True),
    ]


def test_the_gold_values_of_an_existing_fact_come_from_the_rows(items):
    """D25 の向き: 既存の fact が取る値は、IR が書いた行から求める。"""
    art14 = next(i["gold"] for i in items if i["gold"]["gold_id"] == "aoi_johokanri_art14")
    values = gold_values(art14)
    assert [(g.variable, g.value, g.is_new) for g in values] == [
        ("birth_match", "一致", False),
        ("phone_match", "一致", False),
    ]
    # 根拠の語句は evidence.fact_mapping から引く
    assert values[0].quote == "当該患者の生年月日"


def test_the_gold_values_are_normalized_the_same_way_as_the_output(items):
    """D56: 全角数字は半角にしてから突き合わせる。"""
    art21 = next(i["gold"] for i in items if i["gold"]["gold_id"] == "mhlw_r7dec_art21_3")
    values = [g.value for g in gold_values(art21)]
    assert "18歳未満の者" in values
    assert "産後1年を経過しない女性労働者" in values


# --------------------------------------------------------------------------
# 誤りの分類(P40)
# --------------------------------------------------------------------------


def test_a_value_the_clause_does_not_have_is_a_severe_addition(items):
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            ("operator_role", "システム開発担当"),
            ("target_environment", "本番環境"),
            ("requested_privilege", "特権"),
            ("time_window", "深夜"),
        ]
    )
    judged = judge(1, golden, result)
    kinds = [e["kind"] for e in judged.errors]
    assert kinds == ["condition_added"]
    assert "condition_added" in SEVERE_KINDS
    assert judged.verdict == "severe"
    assert judged.slipped is True  # ゲートを立てていないので、すり抜け


def test_a_different_value_for_the_same_condition_is_a_severe_mistake(items):
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            ("operator_role", "システム開発担当"),
            ("target_environment", "本番環境"),
            # 「特権」ではなく「管理者権限」にした(同じ条件で値が違う)
            ("requested_privilege", "管理者権限"),
        ]
    )
    judged = judge(1, golden, result)
    assert [e["kind"] for e in judged.errors] == ["value_mistaken"]
    assert judged.errors[0]["gold_value"] == "特権"


def test_putting_a_value_into_another_existing_fact_is_a_severe_wrong_reuse(items):
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            ("target_environment", "本番環境"),
            ("requested_privilege", "特権"),
        ],
        matches=[("contact_consent", "システム開発担当")],
    )
    judged = judge(1, golden, result)
    assert [e["kind"] for e in judged.errors] == ["wrong_reuse"]
    assert judged.errors[0]["gold_fact"] == "operator_role"


def test_a_missing_value_is_a_middle_drop(items):
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            ("operator_role", "システム開発担当"),
            ("target_environment", "本番環境"),
        ]
    )
    judged = judge(1, golden, result)
    assert [e["kind"] for e in judged.errors] == ["condition_dropped"]
    assert "condition_dropped" in MIDDLE_KINDS
    assert judged.verdict == "middle"
    assert judged.slipped is False  # 中の誤りはすり抜けに数えない(P40)


def test_merging_two_columns_into_one_is_a_middle_grouping_error(items):
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            # 3つの値を1つの列にまとめた
            ("operation_condition", "システム開発担当"),
            ("operation_condition", "本番環境"),
            ("operation_condition", "特権"),
        ]
    )
    judged = judge(1, golden, result)
    assert {e["kind"] for e in judged.errors} == {"grouping_differs"}
    assert judged.verdict == "middle"


def test_a_value_with_a_judgment_word_is_excluded_from_the_evaluation(items):
    """FU2: 評価の判断を含む条件(D51)は評価から外し、件数だけ数える。"""
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            ("operator_role", "システム開発担当"),
            ("target_environment", "本番環境"),
            ("requested_privilege", "特権"),
            ("operation_manner", "正当な理由なし"),
        ]
    )
    judged = judge(1, golden, result)
    assert judged.errors == []
    assert judged.correct is True
    assert judged.excluded_values == [
        {"value": "正当な理由なし", "fact": "operation_manner", "words": ["正当な理由"]}
    ]


# --------------------------------------------------------------------------
# 教師あり(P42)
# --------------------------------------------------------------------------


def test_teaching_adds_the_gold_facts_with_the_reserved_word(items):
    """P42・D45: 正解の fact を層3に足す。passthrough には「その他」が付く。"""
    registry = Registry.load(ROOT / "registry" / "registry.json")
    ledger = FactLedger.from_json(registry.fact_registry)
    before = len(ledger.entries)
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    log = teach(ledger, golden)
    assert len(ledger.entries) == before + 3
    entry = ledger.find("operator_role")
    assert entry is not None
    assert entry.possible_values == ["システム開発担当", OTHER_VALUE]
    assert entry.defined_by == golden["ir"]["clause_ref"]
    assert len(log) == 3


def test_teaching_a_clause_that_only_reuses_facts_adds_nothing(items):
    registry = Registry.load(ROOT / "registry" / "registry.json")
    ledger = FactLedger.from_json(registry.fact_registry)
    art14 = next(i["gold"] for i in items if i["gold"]["gold_id"] == "aoi_johokanri_art14")
    log = teach(ledger, art14)
    assert ledger.names() == [e["variable"] for e in registry.fact_registry]
    assert log == ["新しい fact・値は無い(既存の fact を使う条文)"]


# --------------------------------------------------------------------------
# オラクルで通したとき(パートB の確認)
# --------------------------------------------------------------------------


def test_the_oracle_run_has_no_slipped_severe_error(oracle_run):
    """P40 の合格の条件: ゲートをすり抜けた重大な誤りが0件。"""
    assert oracle_run["summary"]["slipped"] == 0
    assert oracle_run["summary"]["items"] == 5
    assert oracle_run["summary"]["conditions_verbatim"] == (
        oracle_run["summary"]["conditions_total"]
    )


def test_the_oracle_cannot_reach_five_of_five_and_why(oracle_run):
    """**手順どおりでは正解 IR 5件のうち2件に届かない**(第49回 報告 要判断1・2)。

    どちらも P37(値の完全一致で寄せ、LLM の判定では寄せない)の帰結である。

    1. `ipa_secguide_v4_personnel_2` の「許可なし」は、第11条で層3に入った
       `usage_authorization` の値と**完全に一致する**ので、手順3a が必ずそこへ寄せる。
       正解は別の列(`recipient_authorization`)なので、**重大な誤り(誤った再利用)**
       になる。ゲート(`fact_possible_duplicate`)は立つのですり抜けではない
    2. `aoi_johokanri_art14` の「一致」は比較型の fact の値で、
       `name_match`・`birth_match`・`phone_match` の3つが同じ値を持つ。P37 は一致の
       相手を passthrough の fact に限っているので再利用できず、**取りこぼし(中)**
       になる。手順3b は正解を提案するが、提案では寄せない(P37)
    """
    summary = oracle_run["summary"]
    assert summary["correct"] == 3
    by_item = {i["gold_id"]: i for i in oracle_run["items"]}
    personnel = by_item["ipa_secguide_v4_personnel_2"]
    assert [e["kind"] for e in personnel["errors"]] == ["wrong_reuse"]
    assert personnel["errors"][0]["fact"] == "usage_authorization"
    assert personnel["errors"][0]["gold_fact"] == "recipient_authorization"
    assert "fact_possible_duplicate" in personnel["gates"]
    art14 = by_item["aoi_johokanri_art14"]
    assert [e["kind"] for e in art14["errors"]] == ["reuse_missed", "reuse_missed"]
    assert art14["gates"] == ["fact_reuse_suggested"]
    # 提案は正解の列を当てている(受け入れれば救える。P37)
    assert summary["suggestions"] == 2
    assert summary["suggestion_hits"] == 2


def test_the_oracle_run_teaches_the_layer3_in_order(oracle_run):
    """P42: 1件ずつ正解を層3に足すので、後の条文は前の条文の fact を見られる。"""
    names = [e["variable"] for e in oracle_run["ledger_after"]]
    # 初期登録の7件 + 正解 IR が定義する11件
    assert len(names) == 18
    assert names[:7] == [
        "name_match",
        "birth_match",
        "phone_match",
        "has_medical",
        "has_pii",
        "identifiable",
        "contact_consent",
    ]
    assert "recipient_authorization" in names
