"""保留評価セット(testdata/action_gold/action_gold_holdout_v1.json)の仕組みのテスト。

第33回 HANDOFF パートC。保留用の正解は、設計を固めてから**1回だけ**流すもので、
結果を見てプロンプト・規則・正解のどれも直さない(P20・5.4節)。

**正解の値(expected・expected_alternatives・definition・ledger_updates)は
web Claude の追補で受け取る。**ファイルにあるのは条文・出典・context(入力)と
出発点の台帳だけで、ここで確かめるのは仕組みである。

| 確かめること | 根拠 |
| --- | --- |
| split が holdout で、開発用は dev | P20・4.2節 |
| 14件が候補データの条文を逐語で写したもので、手続き的な1件を外してある | 5.4節・6.6節(v0.6) |
| 出発点の台帳が、開発用18件(v3)を足し終えた状態と一致する | 第33回4.1節 |
| 正解の値が無い・承認されていない項目があるかぎり評価が止まる | 第33回4.3節 |
| 複数の行為を含む条文の判定(どれか1つと一致すれば正しい) | P25 |
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_frontstage.action_ledger import ActionEntry, ActionLedger  # noqa: E402
from src.stage3_frontstage.run_action_eval import (  # noqa: E402
    HoldoutNotApproved,
    check_holdout_is_approved,
    expectations,
    load_ledger,
    nearest_expectation,
    teach,
)

GOLD_DIR = Path(__file__).resolve().parents[1] / "testdata" / "action_gold"
HOLDOUT_PATH = GOLD_DIR / "action_gold_holdout_v1.json"
DEV_PATH = GOLD_DIR / "action_gold_v3.json"
REGISTRY_PATH = Path(__file__).resolve().parents[1] / "registry" / "registry.json"


@pytest.fixture(scope="module")
def holdout() -> dict:
    return json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dev() -> dict:
    return json.loads(DEV_PATH.read_text(encoding="utf-8"))


def test_the_two_sets_declare_their_split(holdout, dev):
    """P20: 開発用と保留用を `_meta.split` で区別する。"""
    assert holdout["_meta"]["split"] == "holdout"
    assert dev["_meta"]["split"] == "dev"


def test_holdout_has_fourteen_items_without_the_procedural_one(holdout):
    """5.4節(v0.6): 15件から手続き的な1件(転送の後に履歴を残す)を外して14件。"""
    ids = [i["clause_id"] for i in holdout["items"]]
    assert ids == [
        "ipa_secguide_v4_6_2_1_item1",
        "ipa_secguide_v4_6_2_1_item2",
        "ipa_secguide_v4_phys_1_2_level2",
        "ipa_secguide_v4_phys_1_2_level3_carry_in",
        "ipa_secguide_v4_personnel_6_1_2",
        "ipa_secguide_v4_2_3_3_store",
        "ipa_secguide_v4_2_3_3_wifi",
        "ipa_secguide_v4_5_6_share",
        "ipa_secguide_v4_5_6_outdoor",
        "ipa_secguide_v4_phys_3_phone",
        "ipa_secguide_v4_phys_3_photo",
        "mhlw_r7dec_art11_3",
        "mhlw_r7dec_art16_1",
        "mhlw_r7dec_art58_3_5",
    ]
    assert "ipa_secguide_v4_5_6_transfer_log" not in ids
    assert holdout["_meta"]["dropped"]["clause_id"] == "ipa_secguide_v4_5_6_transfer_log"
    assert [i["order"] for i in holdout["items"]] == list(range(1, 15))


def test_the_clause_text_comes_verbatim_from_the_candidates(holdout):
    """条文は候補データから逐語で写したものであること(入力)。"""
    cands = {
        c["candidate_id"]: c
        for c in json.loads(
            (GOLD_DIR.parent / "candidates" / "candidates_round3.json").read_text(
                encoding="utf-8"
            )
        )["candidates"]
    }
    for item in holdout["items"]:
        candidate = cands[item["clause_id"]]
        assert item["text"] == candidate["text_raw"], item["clause_id"]
        assert item["source"]["locator"] == candidate["source"]["locator"]


def test_no_answer_value_is_filled_in_yet(holdout):
    """正解の値は追補で受け取る。ファイルには入力と出発点の台帳しか無いこと。"""
    assert holdout["_meta"]["status"] == "skeleton"
    for item in holdout["items"]:
        assert item["expected"] is None, item["clause_id"]
        for key in ("expected_alternatives", "definition", "ledger_updates", "note"):
            assert key not in item, (item["clause_id"], key)
        assert item["review"] == {"approved_by": None, "reviewed_at": None}


def test_the_starting_ledger_is_the_dev_set_taught_to_the_end(holdout, dev):
    """4.1節: 出発点の台帳は、開発用18件(v3)の正解をすべて足し終えた状態(11件)。

    `ledger_seed` はファイルに固定してあるので、action_gold_v3 から作り直して
    一致することをここで確かめる。
    """
    ledger = load_ledger(dev, REGISTRY_PATH)
    for item in dev["items"]:
        teach(ledger, item)
    rebuilt = ledger.to_json()
    assert len(rebuilt) == 11
    assert rebuilt == holdout["ledger_seed"]


def test_the_starting_ledger_uses_the_v06_names(holdout):
    """D82: 出発点の台帳は v0.6 の名前で、提供の子に送信・伝達・共有がある。"""
    ledger = ActionLedger.from_json(holdout["ledger_seed"])
    assert ledger.find("提供").kind == "abstract"
    for child in ("送信", "伝達", "共有"):
        assert ledger.find(child).parent == "提供", child
    assert ledger.find("取り出し").parent is None
    for old in ("外部送信", "データ送信", "特権操作", "情報提供"):
        assert ledger.find(old) is None


def test_the_items_that_need_context_have_it_verbatim_with_a_locator(holdout):
    """4.1節: context は入力。表の見出し・箇条書きの親文を逐語・出典つきで入れる。"""
    with_context = {
        i["clause_id"]: i["context"] for i in holdout["items"] if i.get("context")
    }
    assert sorted(with_context) == [
        "ipa_secguide_v4_2_3_3_store",
        "ipa_secguide_v4_phys_1_2_level2",
        "ipa_secguide_v4_phys_1_2_level3_carry_in",
    ]
    for context in with_context.values():
        for entry in context:
            assert entry["basis"] == "verbatim"
            assert entry["locator"]
            assert entry["text"].strip()


# --------------------------------------------------------------------------
# 承認前に止まる(4.3節)
# --------------------------------------------------------------------------


def _filled(holdout: dict, *, approved: bool) -> dict:
    """テストのために、全件に仮の正解の値(と承認者)を入れた写しを作る。"""
    out = copy.deepcopy(holdout)
    for item in out["items"]:
        item["expected"] = {"surface": "x", "normalized": "x", "is_new": True,
                            "core_verb": "x", "kind": "concrete", "parent": None}
        item["review"]["approved_by"] = "Kazuhiro" if approved else None
    return out


def test_a_holdout_set_without_answer_values_stops_the_evaluation(holdout):
    """今の状態(正解の値が無い)では評価を流さない。"""
    with pytest.raises(HoldoutNotApproved, match="正解の値が入っていない"):
        check_holdout_is_approved(holdout)


def test_an_unapproved_holdout_set_stops_the_evaluation(holdout):
    """4.3節: 値が入っていても、未承認の項目が1件でもあれば流さない。"""
    with pytest.raises(HoldoutNotApproved, match="承認されていない"):
        check_holdout_is_approved(_filled(holdout, approved=False))
    approved = _filled(holdout, approved=True)
    check_holdout_is_approved(approved)
    # 1件でも欠ければまた止まり、その clause_id が出る
    approved["items"][7]["review"]["approved_by"] = None
    with pytest.raises(HoldoutNotApproved, match="ipa_secguide_v4_5_6_share"):
        check_holdout_is_approved(approved)
    # 値が1件でも欠ければ止まる
    approved = _filled(holdout, approved=True)
    approved["items"][0]["expected"] = None
    with pytest.raises(HoldoutNotApproved, match="正解の値"):
        check_holdout_is_approved(approved)


def test_the_dev_set_is_not_stopped_by_the_holdout_check(dev):
    """4.3節の検査は保留用だけに当てる。"""
    check_holdout_is_approved(dev)


# --------------------------------------------------------------------------
# 複数の行為を含む条文(P25)。正解の値ではなく、合成の項目で仕組みだけを確かめる
# --------------------------------------------------------------------------

# 前段設計書 PU3 の例「複写し、又は持ち出してはならない」を使った合成の項目
ALT_ITEM = {
    "order": 1,
    "clause_id": "synthetic_copy_or_carry_out",
    "clause_ref": "テスト規程 第1条",
    "text": "書類を複写し、又は持ち出してはならない。",
    "expected": {"surface": "書類を複写", "normalized": "複写", "is_new": True,
                 "core_verb": "複写", "kind": "concrete", "parent": None,
                 "definition": {"text": "写しを作ること", "status": "provisional",
                                "basis": [], "general_terms": []}},
    "expected_alternatives": [
        {"surface": "持ち出", "normalized": "持出し", "is_new": True,
         "core_verb": "持ち出", "kind": "concrete", "parent": None,
         "definition": {"text": "物を、置かれている場所の外へ持って出ること",
                        "status": "provisional", "basis": [], "general_terms": []}},
    ],
    "review": {"approved_by": "pytest"},
}


def test_teaching_an_item_with_alternatives_adds_every_action():
    """P25: 教師ありでは、並べた正解の Action をすべて台帳に足す。"""
    ledger = ActionLedger.from_json([])
    teach(ledger, ALT_ITEM)
    assert ledger.names() == ["複写", "持出し"]
    # 並びの要素ごとの definition を使う
    assert ledger.find("持出し").definition == "物を、置かれている場所の外へ持って出ること"


def test_expectations_returns_the_line_in_order():
    """P25: `expectations()` は代表 → 残りの順に並びを返す。並びでなければ1件。"""
    assert [e["normalized"] for e in expectations(ALT_ITEM)] == ["複写", "持出し"]
    single = {k: v for k, v in ALT_ITEM.items() if k != "expected_alternatives"}
    assert [e["normalized"] for e in expectations(single)] == ["複写"]


def test_the_nearest_expectation_is_the_one_in_the_same_line():
    """P25: 誤りの分類は、出力に最も近い正解(親子関係にあるもの)と比べて決める。"""
    ledger = ActionLedger.from_json([])
    ledger.add(ActionEntry(name="移動", definition="物の場所を変えること", kind="abstract"))
    ledger.add(ActionEntry(name="複写", definition="写しを作ること"))
    ledger.add(ActionEntry(name="持出し", definition="外へ持って出ること", parent="移動"))
    ledger.add(ActionEntry(name="放置", definition="置いたままにすること"))
    options = expectations(ALT_ITEM)
    # 持出しの親(移動)を選んだ → 同じ系統の持出しと比べる
    assert nearest_expectation(options, "移動", ledger)["normalized"] == "持出し"
    # どちらとも親子関係にない → 並びの先の方と比べる
    assert nearest_expectation(options, "放置", ledger)["normalized"] == "複写"
