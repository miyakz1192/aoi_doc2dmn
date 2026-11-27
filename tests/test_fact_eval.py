"""事実の抽出の評価の単体テスト。前段設計書10.4節(P40・P42)。

| 確かめること | 根拠 |
| --- | --- |
| 正解の値は facts 欄と決定表の行から求める | 10.4.1節・D25 |
| 誤りの重さは6分類(重大3・中3) | P40 |
| 合格の条件は「すり抜けた重大な誤りが0件」 | P40・10.4.2節 |
| 教師ありで正解の fact を層3に足す(「その他」を含む) | P42・D45 |
| 評価の判断を含む値は評価から外し、件数だけ数える | FU2 |
| オラクルで通したときの上限と、その理由 | パートB の確認 |
| 表記の違い(中)の分け方 | **P40 改訂【v0.13】** |
| 重大な誤りで疑いのゲートが無い件(参考指標) | Q2(第50回) |
| 済んだ結果を LLM なしで数え直す | 第50回パートB3 |
"""
from __future__ import annotations

import json
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
    reclassify,
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
    """根拠の語句が正解と重ならない値の違いは、これまでどおり重大(P40)。"""
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            ("operator_role", "システム開発担当"),
            ("target_environment", "本番環境"),
            # 「特権」ではなく「特別権限」にした(正解の根拠の語句に無い書き方)
            ("requested_privilege", "特別権限"),
        ]
    )
    judged = judge(1, golden, result)
    assert [e["kind"] for e in judged.errors] == ["value_mistaken"]
    assert judged.errors[0]["gold_value"] == "特権"
    assert "value_mistaken" in SEVERE_KINDS


def test_the_same_basis_quote_with_another_wording_is_only_a_notation_difference(items):
    """P40 改訂【v0.13】: 根拠の語句が同じで同じ列なら「表記の違い(中)」。

    正解 IR は「特権/管理者権限」という並列の前半を値に採り、「『管理者権限』を別の
    値として足すかは要レビュー」と注記している。つまり、どちらの書き方にするかは人が
    承認するときに決まることなので、重大な「値の取り違え」にしない。
    """
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            ("operator_role", "システム開発担当"),
            ("target_environment", "本番環境"),
            ("requested_privilege", "管理者権限"),
        ]
    )
    judged = judge(1, golden, result)
    assert [e["kind"] for e in judged.errors] == ["notation_differs"]
    assert judged.errors[0]["gold_value"] == "特権"
    assert "notation_differs" in MIDDLE_KINDS
    assert judged.verdict == "middle"


def test_a_notation_difference_needs_the_column_to_be_one_to_one(items):
    """表記の違いの条件3: その列が別の正解の列にも結びついていたら重大のまま。"""
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[
            # 1つの列に、別の正解の列の値(本番環境)と、書き方の違う値を混ぜた
            ("target_environment", "本番環境"),
            ("target_environment", "管理者権限"),
        ]
    )
    judged = judge(1, golden, result)
    kinds = [e["kind"] for e in judged.errors]
    assert "value_mistaken" in kinds
    assert "notation_differs" not in kinds


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


def test_the_oracle_reaches_four_of_five_after_the_p37_revision(oracle_run):
    """**手順どおりで正解 IR 5件のうち4件に届く**(P37 改訂【v0.13】の効き方)。

    第49回(v0.12)は 3/5 だった。届かなかった2件のうち1件が、寄せ先を同じ行為の
    fact に限ることで直った。

    1. `ipa_secguide_v4_personnel_2` の「許可なし」は、第11条(行為「使用」)で層3に
       入った `usage_authorization` の値と完全に一致するが、この条文の行為は「提供」
       なので**寄せない**。値が一致したことは記録に残り、新規候補の値が既存の fact と
       同じなので `fact_possible_duplicate`(疑い)で人に見せる
    2. `aoi_johokanri_art14` の「一致」は比較型の fact の値で、比較型は手順3a の相手に
       しない(Q1)。**取りこぼし(中)** が2件残る。手順3b は正解の列を当てているので、
       提案の受け入れ(PU14)が入れば救える
    """
    summary = oracle_run["summary"]
    assert summary["correct"] == 4
    assert summary["slipped"] == 0
    assert summary["slipped_strict"] == 0
    by_item = {i["gold_id"]: i for i in oracle_run["items"]}
    personnel = by_item["ipa_secguide_v4_personnel_2"]
    assert personnel["errors"] == []
    assert "fact_possible_duplicate" in personnel["gates"]
    # 値は一致したが別の行為の fact なので寄せていない(P37 改訂)
    assert [h["facts"] for h in personnel["out_of_scope_hits"]] == [
        ["usage_authorization"]
    ]
    assert summary["out_of_scope_matches"] == 1
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


# --------------------------------------------------------------------------
# 参考指標と数え直し(第50回パートB)
# --------------------------------------------------------------------------


def test_a_severe_error_with_only_a_design_check_gate_is_counted_strictly(items):
    """参考指標(Q2): 重大な誤りで**疑いの**ゲートが1つも立たない件を数える。

    `new_fact_unapproved`(設計上の確認)は新しい fact を提案すればほぼ毎件立つので、
    それだけで止まっている件は「人が気づける」とは言いにくい。合格の条件は P40 の
    まま(`slipped`)で、この指標は併記する。
    """
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[("time_window", "深夜")],
        gates=ReviewGates(new_fact_unapproved=True),
    )
    judged = judge(1, golden, result)
    assert judged.verdict == "severe"
    # P40 の合格の条件ではすり抜けではない(ゲートは1本立っている)
    assert judged.slipped is False
    # 参考指標では数える(疑いのゲートが1つも無い)
    assert judged.slipped_strict is True


def test_a_severe_error_with_a_suspicion_gate_is_not_counted_strictly(items):
    golden = next(i["gold"] for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_7_1")
    result = fake_result(
        values=[("time_window", "深夜")],
        gates=ReviewGates(fact_value_unverified=True),
    )
    judged = judge(1, golden, result)
    assert judged.slipped is False
    assert judged.slipped_strict is False


def test_reclassifying_the_round49_result_moves_four_values_to_notation(items):
    """パートB3: 第49回の結果を LLM なしで数え直す。

    重大な「値の取り違え」4件(#3 の「営業秘密」と #4 の3件)は、どれも根拠の語句が
    正解と重なり同じ列に入っているので「表記の違い(中)」に移る。残る重大な誤りは
    #3 の誤った再利用と #5 の条件の付け足しの2件である。
    """
    before = json.loads(
        (ROOT / "docs" / "eval" / "fact_eval_irgold_20261125_v012.json").read_text(
            encoding="utf-8"
        )
    )
    assert before["summary"]["errors_by_kind"]["value_mistaken"] == 4
    after = reclassify(before)
    summary = after["summary"]
    assert summary["errors_by_kind"]["value_mistaken"] == 0
    assert summary["errors_by_kind"]["notation_differs"] == 4
    assert summary["severe_errors"] == 2
    assert summary["slipped"] == 0
    # 参考指標では、#3 が疑いのゲート無しの重大な誤りとして残る
    assert summary["slipped_strict_items"] == ["ipa_secguide_v4_personnel_2"]
    # 出力そのものは変えていない(値の数は同じ)
    assert summary["output_values"] == before["summary"]["output_values"]
    assert summary["gold_values"] == before["summary"]["gold_values"]


def test_reclassifying_counts_the_suggestions_by_column(items):
    """パートC3: 提案の当たりは列の単位で数える。

    第49回は、正解の列(`birth_match`・`phone_match`)を当てていたのに、渡した値が
    正解の値「一致」ではなかったため値の単位では誤報2と数えていた。
    """
    before = json.loads(
        (ROOT / "docs" / "eval" / "fact_eval_irgold_20261125_v012.json").read_text(
            encoding="utf-8"
        )
    )
    assert before["summary"]["suggestion_hits"] == 0
    after = reclassify(before)
    assert after["summary"]["suggestions"] == 2
    assert after["summary"]["suggestion_hits"] == 2
    assert after["summary"]["suggestion_false_alarms"] == 0
    assert [r["answer"] for r in after["summary"]["suggestion_rows"]] == [
        "birth_match",
        "phone_match",
    ]


def test_the_eval_uses_the_wiring_so_step_3a_is_scoped(oracle_run):
    """評価は配線付きの台帳で流す(P37 改訂が効いていることの確かめ)。

    配線を読み忘れると 3a が v0.12 の挙動(層3の全体が相手)に戻ってしまうので、
    寄せ先の範囲が記録に残っていることを確かめる。
    """
    by_item = {i["gold_id"]: i for i in oracle_run["items"]}
    # 第11条(使用)を教えた後の提供の条文で、寄せ先の範囲が空である
    personnel = by_item["ipa_secguide_v4_personnel_2"]
    assert personnel["reuse_scope_facts"] == []
    assert personnel["out_of_scope_hits"]
    # 教師ありで層1も育っている(行為「使用」の表が入っている)
    taught = [line for e in oracle_run["ledger_log"] for line in e["taught"]]
    assert any("行為「使用」の判定" in line for line in taught)
