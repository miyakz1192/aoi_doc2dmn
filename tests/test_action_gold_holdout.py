"""保留評価セット(testdata/action_gold/action_gold_holdout_v1.json)の仕組みのテスト。

第33回 HANDOFF パートC。保留用の正解は、設計を固めてから**1回だけ**流すもので、
結果を見てプロンプト・規則・正解のどれも直さない(P20・5.4節)。

正解の値は第33回追補 4章の表(Kazuhiro 承認 2026-11-20)のとおりに入れた。ここでは
仕組みに加えて、ファイルの値が追補の表と一致することを確かめる。

| 確かめること | 根拠 |
| --- | --- |
| split が holdout で、開発用は dev | P20・4.2節 |
| 14件が候補データの条文を逐語で写したもので、番号は第33回のまま(9は欠番) | 5.4節・6.6節(v0.6)・追補4章 |
| 正解が追補4章の表どおりで、全件承認済み | 追補4章 |
| 出発点の台帳が、開発用18件(v3)を足し終えた状態と一致する | 第33回4.1節 |
| 正解の値が無い・承認されていない項目が1件でもあれば評価が止まる | 第33回4.3節 |
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
    # 番号は第33回のまま(9は欠番。追補4章)
    assert [i["order"] for i in holdout["items"]] == [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15]
    # 外した10件を理由とともに記録する
    excluded = holdout["_meta"]["excluded"]
    assert len(excluded) == 10
    assert excluded[0]["clause_id"] == "ipa_secguide_v4_5_6_transfer_log"
    assert all(e["reason"] for e in excluded)


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


# 追補4章の表(番号 → (正解の並び[(名前, 新規か, 段, 親, 中心の動詞)], ledger_updates))
ADDENDUM = {
    1: ([("発行", True, "concrete", None, "発行")], None),
    2: ([("共有", False, "concrete", "提供", "共有"), ("発行", False, "concrete", None, "発行")], None),
    3: ([("操作", False, "concrete", None, "操作"), ("持出し", True, "concrete", None, "持出し")], None),
    4: ([("持込み", True, "concrete", None, "持込み")], None),
    5: ([("取り出し", False, "concrete", None, "ダウンロード")], None),
    6: ([("保存", True, "concrete", None, "保存")], None),
    7: ([("使用", False, "concrete", None, "利用")], None),
    8: ([("共有", False, "concrete", "提供", "共有")], None),
    10: ([("送信", False, "concrete", "提供", "送受信"), ("受信", True, "concrete", None, "送受信")], None),
    11: ([("伝達", False, "concrete", "提供", "伝え")], None),
    12: ([("撮影", True, "concrete", None, "撮影"), ("録音", True, "concrete", None, "録音")], None),
    13: ([("離席", True, "concrete", None, "離れ")], None),
    14: ([("取得", True, "abstract", None, "取得")], [("取り出し", "取得"), ("受信", "取得")]),
    15: ([("同伴", True, "concrete", None, "連れて行")], None),
}


def test_the_answers_match_the_addendum_table(holdout):
    """追補4章: 正解・段・親・中心の動詞・ledger_updates が表のとおりであること。"""
    for item in holdout["items"]:
        line, updates = ADDENDUM[item["order"]]
        got = [
            (e["normalized"], e["is_new"], e["kind"], e["parent"], e["core_verb"])
            for e in expectations(item)
        ]
        assert got == line, item["order"]
        assert [(u["name"], u["parent"]) for u in item.get("ledger_updates", [])] == (
            updates or []
        ), item["order"]
        # 新規の Action には定義がある(並びのときは要素ごとに)
        for e in expectations(item):
            if e["is_new"]:
                assert e["definition"]["text"], (item["order"], e["normalized"])


def test_every_item_is_approved_with_the_hard_declarative_label(holdout):
    """追補4章: 全件 Kazuhiro が 2026-11-20 に承認、ラベルはハード制約 × 宣言的。"""
    for item in holdout["items"]:
        assert item["review"] == {"approved_by": "Kazuhiro", "reviewed_at": "2026-11-20"}
        assert item["oracle_labels"] == {"axis1": "ハード制約", "axis2": "宣言的"}
    check_holdout_is_approved(holdout)  # 止まらない


def test_every_expected_surface_is_reachable(holdout):
    """P4・P24: 正解の語句は条文の部分文字列で、中心の動詞を含むこと。"""
    from src.stage3_frontstage.modality import strip_parentheses

    for item in holdout["items"]:
        clause = strip_parentheses(item["text"])
        for e in expectations(item):
            assert strip_parentheses(e["surface"]) in clause, (item["order"], e["surface"])
            assert e["core_verb"] in e["surface"], (item["order"], e["surface"])


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
    """追補4章: 項目3・4(表の見出し)と項目6(箇条書きの親文)に、逐語・出典つきで入れる。"""
    with_context = {
        i["clause_id"]: i["context"] for i in holdout["items"] if i.get("context")
    }
    assert sorted(with_context) == [
        "ipa_secguide_v4_2_3_3_store",
        "ipa_secguide_v4_phys_1_2_level2",
        "ipa_secguide_v4_phys_1_2_level3_carry_in",
    ]
    store = with_context["ipa_secguide_v4_2_3_3_store"]
    assert [c["text"] for c in store] == [
        "利用期間中は、利用するIT機器や電子媒体に以下に該当する機能がある場合には実行する。"
    ]
    for context in with_context.values():
        for entry in context:
            assert entry["basis"] == "verbatim"
            assert entry["locator"]
            assert entry["text"].strip()


# --------------------------------------------------------------------------
# 承認前に止まる(4.3節)
# --------------------------------------------------------------------------


def test_a_holdout_set_without_answer_values_stops_the_evaluation(holdout):
    """4.3節: 正解の値が1件でも欠ければ評価を流さない。"""
    broken = copy.deepcopy(holdout)
    broken["items"][0]["expected"] = None
    with pytest.raises(HoldoutNotApproved, match="正解の値が入っていない"):
        check_holdout_is_approved(broken)


def test_an_unapproved_holdout_set_stops_the_evaluation(holdout):
    """4.3節: 未承認の項目が1件でもあれば流さず、その clause_id を出す。"""
    broken = copy.deepcopy(holdout)
    broken["items"][7]["review"]["approved_by"] = None
    with pytest.raises(HoldoutNotApproved, match="ipa_secguide_v4_5_6_share"):
        check_holdout_is_approved(broken)


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


def test_the_axis_labels_are_in_testdata_clauses(holdout):
    """第33回4.2節: 承認後に軸1・軸2の正解ラベルを testdata/clauses/ に足す。"""
    path = GOLD_DIR.parent / "clauses" / "clauses_holdout_v1.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [c["clause_id"] for c in doc["clauses"]] == [i["clause_id"] for i in holdout["items"]]
    for clause, item in zip(doc["clauses"], holdout["items"]):
        assert clause["oracle"]["axis1"] == "ハード制約"
        assert clause["oracle"]["axis2"] == "宣言的"
        assert clause["text_raw"] == item["text"]
        assert clause["holdout_order"] == item["order"]
