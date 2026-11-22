"""第32回の生データで指標を計算し直す処理のテスト(第33回タスクB5)。

LLM を回さずに、`docs/eval/action_eval_20261108.json` の Trace から v0.5 の指標を
作れること、P24(括弧の扱い)の効果が出ることを固定する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_frontstage.recompute_metrics import recompute  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def metrics() -> dict:
    result = json.loads(
        (ROOT / "docs" / "eval" / "action_eval_20261108.json").read_text(encoding="utf-8")
    )
    gold = json.loads(
        (ROOT / "testdata" / "action_gold" / "action_gold_v2.json").read_text(
            encoding="utf-8"
        )
    )
    return recompute(result, gold)


def test_the_design_check_gates_leave_the_false_alarm_count(metrics):
    """P21: action_abstract_selected を誤報から外す。"""
    assert metrics["false_alarm_gates_v04"]["action_abstract_selected"] == 2
    assert "action_abstract_selected" not in metrics["false_alarm_gates"]
    assert metrics["false_alarm_gates"] == {
        "action_surface_unverified": 1,
        "action_verb_mismatch": 3,
    }


def test_how_many_items_went_to_a_human(metrics):
    """P21: 第32回は18件中15件が人に回り、自動で通ったのは3件だった。"""
    assert metrics["items"] == 18
    assert metrics["routed_to_human"] == 15
    assert metrics["automatic"] == 3
    assert metrics["routed_with_suspicion"] == 9
    assert metrics["routed_design_check_only"] == 6


def test_the_precision_of_the_suspicion_gates(metrics):
    """P21: 疑いのゲートが立った9件のうち、本当に誤りだったのは5件。"""
    assert metrics["suspicion_precision"] == {"raised": 9, "really_wrong": 5}
    by_gate = metrics["suspicion_precision_by_gate"]
    assert by_gate["action_verb_mismatch"] == {"raised": 7, "really_wrong": 4}
    assert by_gate["action_surface_unverified"] == {"raised": 1, "really_wrong": 0}
    assert by_gate["action_possible_duplicate"] == {"raised": 1, "really_wrong": 1}


def test_the_tier_of_two_new_candidates_was_wrong(metrics):
    """P23: 新規候補7件のうち、段が正解と一致したのは5件。"""
    assert metrics["new_action_kind_total"] == 7
    assert metrics["new_action_kind_matches"] == 5
    wrong = {r["clause_id"]: r for r in metrics["rows"] if r["kind_matches"] is False}
    assert set(wrong) == {"ipa_secguide_v4_personnel_2", "mhlw_r7dec_art21_3"}
    assert wrong["ipa_secguide_v4_personnel_2"]["proposal_kind"] == "concrete"
    assert wrong["ipa_secguide_v4_personnel_2"]["expected_kind"] == "abstract"


def test_p24_removes_the_only_surface_gate_of_round32(metrics):
    """P24: 括弧を除いてから照合すると、action_surface_unverified が0件になる。"""
    assert metrics["surface_unverified_before"] == 1
    assert metrics["surface_unverified_after"] == 0
    assert metrics["surface_in_clause_before"] == 17
    assert metrics["surface_in_clause_after"] == 18
    assert metrics["surface_ok_before"] == 16
    assert metrics["surface_ok_after"] == 17
    changed = [
        r["clause_id"]
        for r in metrics["rows"]
        if r["surface_in_clause_before"] != r["surface_in_clause_after"]
    ]
    assert changed == ["mhlw_r7dec_art21_3"]


def test_reclassifying_round32_with_the_v3_answers():
    """追補 タスクE3: 第32回の出力を v3 の正解で分類し直す(LLM は回さない)。

    D82 で名前を読み替えても、第32回の4分類は1件も変わらない。項目9 は v3 で正解が
    既存の送信になったが、出力(提供=親)は v2 と同じく過剰適用のままである。
    """
    from src.stage3_frontstage.recompute_metrics import reclassify

    result = json.loads(
        (ROOT / "docs" / "eval" / "action_eval_20261108.json").read_text(encoding="utf-8")
    )
    gold_v3 = json.loads(
        (ROOT / "testdata" / "action_gold" / "action_gold_v3.json").read_text(
            encoding="utf-8"
        )
    )
    out = reclassify(result, gold_v3, ROOT / "registry" / "registry.json")
    assert out["slipped"] == 0
    assert out["by_verdict"] == {
        "correct": 12, "mis_merge": 2, "under_applied": 0, "over_applied": 2,
        "missed": 1,
        # P32(v0.8): 新規どうしは中心の動詞だけで比べる。項目4 は正解も出力も
        # 「労働指示」だが、v3 の正解の動詞が「従事」(使役の直しは v4 で入れた)、
        # 出力が「従事させ」なので、P28 で不一致になる。v3 の正解が P28 より前の
        # ものであることによる見かけの1件で、v4 の正解で分類し直すと正しいに戻る
        # (`test_reclassifying_round32_with_the_v4_answers`)
        "new_mismatch": 1,
    }
    item9 = out["rows"][8]
    assert (item9["expected"], item9["expected_is_new"], item9["actual"]) == ("送信", False, "提供")
    assert item9["verdict_v3"] == "over_applied"
    item4 = out["rows"][3]
    assert item4["clause_id"] == "mhlw_r7dec_art21_3"
    assert (item4["verdict_v2"], item4["verdict_v3"]) == ("correct", "new_mismatch")


def test_reclassifying_round32_with_the_v4_answers():
    """P32: v4 の正解(使役を直した中心の動詞。タスクC1)なら、第32回は変わらない。"""
    from src.stage3_frontstage.recompute_metrics import reclassify

    result = json.loads(
        (ROOT / "docs" / "eval" / "action_eval_20261108.json").read_text(encoding="utf-8")
    )
    gold_v4 = json.loads(
        (ROOT / "testdata" / "action_gold" / "action_gold_v4.json").read_text(
            encoding="utf-8"
        )
    )
    out = reclassify(result, gold_v4, ROOT / "registry" / "registry.json")
    assert out["slipped"] == 0
    assert out["by_verdict"]["correct"] == 13
    assert out["by_verdict"]["new_mismatch"] == 0
    assert out["changed"] == []


def test_the_new_mismatch_is_only_for_answers_that_are_all_new():
    """P32: 正解に既存の Action があれば、新規にしたのは取りこぼしに数える。

    第37回・第39回の項目10(正解は既存の「送信」、出力は新規の「暗号化」)が、
    v0.7 の「新規の取り違え」から「取りこぼし」に変わる(第40回 Q2')。
    """
    from src.stage3_frontstage.recompute_metrics import reclassify

    result = json.loads(
        (ROOT / "docs" / "eval" / "action_eval_dev2_20261121_v07.json").read_text(
            encoding="utf-8"
        )
    )
    gold = json.loads(
        (ROOT / "testdata" / "action_gold" / "action_gold_dev2_v1.json").read_text(
            encoding="utf-8"
        )
    )
    out = reclassify(result, gold, ROOT / "registry" / "registry.json")
    assert out["slipped"] == 0
    # 名前の違いだけだった2件(離脱/離席・連行/同伴)は正しいに戻る
    assert out["by_verdict"]["correct"] == 12
    assert out["by_verdict"]["new_mismatch"] == 0
    assert out["by_verdict"]["missed"] == 1
    changed = {r["clause_id"]: (r["verdict_v2"], r["verdict_v3"]) for r in out["rows"]
               if r["verdict_v2"] != r["verdict_v3"]}
    assert changed == {
        "ipa_secguide_v4_5_6_outdoor": ("new_mismatch", "missed"),
        "mhlw_r7dec_art11_3": ("new_mismatch", "correct"),
        "mhlw_r7dec_art58_3_5": ("new_mismatch", "correct"),
    }
