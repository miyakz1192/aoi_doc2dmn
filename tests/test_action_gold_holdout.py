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


# --------------------------------------------------------------------------
# 第38回 パートC: 開発用の正解 v4(使役の動詞)と dev2(旧保留用を開発用に移す)
# --------------------------------------------------------------------------

V4_PATH = GOLD_DIR / "action_gold_v4.json"
DEV2_PATH = GOLD_DIR / "action_gold_dev2_v1.json"

# タスクC1: 使役の条文3件(第21条第3項・第25条第2項・第21条第4項)
CAUSATIVE_ITEMS = {
    "mhlw_r7dec_art21_3": ("従事", "従事させ"),
    "mhlw_r7dec_art25_2": ("就業", "就業させ"),
    "mhlw_r7dec_art21_4": ("従事", "従事させ"),
}


@pytest.fixture(scope="module")
def dev_v4() -> dict:
    return json.loads(V4_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dev2() -> dict:
    return json.loads(DEV2_PATH.read_text(encoding="utf-8"))


def test_v4_only_changes_the_core_verb_of_the_causative_clauses(dev_v4, dev):
    """タスクC1: v4 で変えるのは中心の動詞だけ(名前・段・親・is_new は v3 のまま)。"""
    assert dev_v4["_meta"]["split"] == "dev"
    assert len(dev_v4["items"]) == len(dev["items"]) == 18
    for new_item, old_item in zip(dev_v4["items"], dev["items"]):
        assert new_item["clause_id"] == old_item["clause_id"]
        new_exp, old_exp = new_item["expected"], old_item["expected"]
        pair = CAUSATIVE_ITEMS.get(new_item["clause_id"])
        if pair:
            before, after = pair
            assert old_exp["core_verb"] == before
            assert new_exp["core_verb"] == after
        else:
            assert new_exp["core_verb"] == old_exp["core_verb"], new_item["clause_id"]
        for key in ("normalized", "is_new", "kind", "parent", "surface"):
            assert new_exp[key] == old_exp[key], (new_item["clause_id"], key)


def test_the_causative_verb_is_verbatim_in_the_surface(dev_v4):
    """P28: 使役を付けた動詞も、正解の語句の中に逐語である。"""
    for item in dev_v4["items"]:
        for e in expectations(item):
            assert e["core_verb"] in e["surface"], (item["order"], e["core_verb"])


def test_a_causative_clause_does_not_match_the_plain_verb_in_the_ledger(dev_v4):
    """P28: v4 の使役の動詞は、台帳の使役でない動詞とは一致しない。"""
    from src.stage3_frontstage.action_normalizer import core_verbs_match

    assert core_verbs_match("従事させ", ["従事"])[0] is False
    assert core_verbs_match("就業させ", ["就業"])[0] is False
    # 台帳に育った「労働指示」の過去の動詞とは一致する
    ledger = load_ledger(dev_v4, REGISTRY_PATH)
    for item in dev_v4["items"]:
        teach(ledger, item)
    verbs = ledger.find("労働指示").core_verbs()
    # 第21条第3項・第25条第2項・第21条第4項の3件分(語句は違うので3件残る)
    assert verbs == ["従事させ", "就業させ", "従事させ"]
    assert core_verbs_match("従事させ", verbs)[0] is True


def test_dev2_is_the_old_holdout_moved_to_development(dev2, holdout):
    """タスクC2・W3: 旧保留用を写して split=dev にし、元のファイルは残す。"""
    assert dev2["_meta"]["split"] == "dev"
    assert holdout["_meta"]["split"] == "holdout"
    assert dev2["_meta"]["ledger_source"] == "seed"
    assert [i["clause_id"] for i in dev2["items"]] == [
        i["clause_id"] for i in holdout["items"]
    ]
    for new_item, old_item in zip(dev2["items"], holdout["items"]):
        assert new_item["text"] == old_item["text"]
        assert new_item["expected"] == old_item["expected"]
        assert new_item.get("expected_alternatives") == old_item.get(
            "expected_alternatives"
        )


def test_dev2_starts_from_the_v4_ledger(dev2, dev_v4):
    """タスクC2: dev2 の出発点は v4 の18件を足し終えた台帳(使役の直しが入る)。"""
    ledger = load_ledger(dev_v4, REGISTRY_PATH)
    for item in dev_v4["items"]:
        teach(ledger, item)
    assert ledger.to_json() == dev2["ledger_seed"]
    assert len(dev2["ledger_seed"]) == 11
    seeded = ActionLedger.from_json(dev2["ledger_seed"])
    assert seeded.find("労働指示").core_verbs() == ["従事させ", "就業させ", "従事させ"]


def test_dev2_is_not_stopped_by_the_holdout_check(dev2):
    """タスクC2: 承認前に止まる検査は split=holdout のときだけ働く。"""
    check_holdout_is_approved(dev2)
    broken = copy.deepcopy(dev2)
    broken["items"][0]["review"]["approved_by"] = None
    check_holdout_is_approved(broken)  # dev なので止まらない


def test_dev2_loads_its_ledger_from_the_seed(dev2):
    """`_meta.ledger_source` が seed なら、登録簿ではなく ledger_seed から作る。"""
    ledger = load_ledger(dev2, REGISTRY_PATH)
    assert len(ledger.entries) == 11
    assert ledger.find("提供").kind == "abstract"


# --------------------------------------------------------------------------
# 第40回 パートC: 新しい保留評価セット v2(付録B。Kazuhiro 承認 2026-11-22)
# --------------------------------------------------------------------------

HOLDOUT_V2_PATH = GOLD_DIR / "action_gold_holdout_v2.json"
CLAUSES_V2_PATH = (
    Path(__file__).resolve().parents[1] / "testdata" / "clauses" / "clauses_holdout_v2.json"
)
CANDIDATES_V4_PATH = (
    Path(__file__).resolve().parents[1]
    / "testdata" / "candidates" / "candidates_round4.json"
)


@pytest.fixture(scope="module")
def holdout2() -> dict:
    return json.loads(HOLDOUT_V2_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def clauses2() -> dict:
    return json.loads(CLAUSES_V2_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def candidates4() -> dict:
    return json.loads(CANDIDATES_V4_PATH.read_text(encoding="utf-8"))


def test_holdout_v2_declares_the_split_and_the_approval(holdout2):
    """パートC: split は holdout、status は approved、18件とも承認済み。"""
    meta = holdout2["_meta"]
    assert meta["split"] == "holdout"
    assert meta["status"] == "approved"
    assert meta["items"] == 18
    assert len(holdout2["items"]) == 18
    for item in holdout2["items"]:
        assert item["review"]["approved_by"] == "Kazuhiro", item["order"]
        assert item["review"]["reviewed_at"] == "2026-11-22", item["order"]
    check_holdout_is_approved(holdout2)  # 止まらない


def test_holdout_v2_is_in_the_candidate_order(holdout2, candidates4):
    """パートC: order と並びは candidates_round4.json の順(評価に入れる18件だけ)。"""
    excluded = {e["clause_id"] for e in holdout2["_meta"]["excluded"]}
    assert len(excluded) == 12
    wanted = [
        c["candidate_id"]
        for c in candidates4["candidates"]
        if c["candidate_id"] not in excluded
    ]
    assert [i["clause_id"] for i in holdout2["items"]] == wanted
    assert [i["order"] for i in holdout2["items"]] == list(range(1, 19))


def test_holdout_v2_copies_the_clause_text_verbatim(holdout2, candidates4):
    """パートC: 条文は候補ファイルから逐語で写す。"""
    by_id = {c["candidate_id"]: c for c in candidates4["candidates"]}
    for item in holdout2["items"]:
        cand = by_id[item["clause_id"]]
        assert item["text"] == cand["text_raw"], item["order"]
        assert item["source"]["locator"] == cand["source"]["locator"], item["order"]
        assert item["source"]["file"].endswith("candidates_round4.json")


def test_holdout_v2_surfaces_are_verbatim_without_modality(holdout2):
    """P14・P4: surface は条文の逐語で、様相の語を含まず、中心の動詞を含む。"""
    from src.stage3_frontstage.modality import find_modality

    for item in holdout2["items"]:
        for exp in expectations(item):
            assert exp["surface"] in item["text"], (item["order"], exp["surface"])
            assert find_modality(exp["surface"]) == [], (item["order"], exp["surface"])
            assert exp["core_verb"] in exp["surface"], (item["order"], exp["core_verb"])


def test_holdout_v2_new_actions_have_a_provisional_definition(holdout2):
    """パートC: 新規の Action の定義は provisional で、出典は付録B。"""
    new_count = 0
    for item in holdout2["items"]:
        for exp in expectations(item):
            if not exp["is_new"]:
                assert "definition" not in exp, (item["order"], exp["normalized"])
                continue
            new_count += 1
            definition = exp["definition"]
            assert definition["text"]
            assert definition["status"] == "provisional"
            assert "付録B" in definition["source"]
            assert definition["basis"][0]["basis"] == "verbatim"
            assert definition["basis"][0]["quote"] in item["text"]
            assert exp["kind"] == "concrete"
            assert exp["parent"] is None
    assert new_count == 14


def test_holdout_v2_starts_from_the_two_dev_sets_taught_to_the_end(
    holdout2, dev_v4, dev2
):
    """パートC: 出発点の台帳は v4(18件) → dev2(14件)を足し終えた状態(21件)。"""
    ledger = load_ledger(dev_v4, REGISTRY_PATH)
    for item in dev_v4["items"]:
        teach(ledger, item)
    for item in dev2["items"]:
        teach(ledger, item)
    rebuilt = ledger.to_json()
    assert len(rebuilt) == 21
    assert rebuilt == holdout2["ledger_seed"]


def test_holdout_v2_ledger_has_the_two_abstract_actions(holdout2):
    """付録B: 抽象は提供と取得。送信・伝達・共有は提供の子、取り出し・受信は取得の子。"""
    ledger = ActionLedger.from_json(holdout2["ledger_seed"])
    assert len(ledger.entries) == 21
    for name in ("提供", "取得"):
        assert ledger.find(name).kind == "abstract", name
    for child in ("送信", "伝達", "共有"):
        assert ledger.find(child).parent == "提供", child
    for child in ("取り出し", "受信"):
        assert ledger.find(child).parent == "取得", child
    # P28: 使役を付けたまま記録する(タスクC1)
    assert ledger.find("労働指示").core_verbs() == ["従事させ", "就業させ", "従事させ"]


def test_holdout_v2_loads_its_ledger_from_the_seed(holdout2):
    """`_meta.ledger_source` が seed なら、登録簿ではなく ledger_seed から作る。"""
    ledger = load_ledger(holdout2, REGISTRY_PATH)
    assert len(ledger.entries) == 21


def test_holdout_v2_stops_when_a_value_or_the_approval_is_missing(holdout2):
    """第33回4.3節: 承認の欄が欠けていたら、split=holdout では評価が止まる。"""
    broken = copy.deepcopy(holdout2)
    broken["items"][0]["review"]["approved_by"] = None
    with pytest.raises(HoldoutNotApproved):
        check_holdout_is_approved(broken)
    # 正解の値が入っていない形(仕組みだけのファイル)でも止まる
    missing = copy.deepcopy(holdout2)
    missing["items"][3]["expected"] = {}
    with pytest.raises(HoldoutNotApproved):
        check_holdout_is_approved(missing)


def test_holdout_v2_records_the_two_drafts_and_the_random_seed(holdout2):
    """パートC: 2案の一致率と乱数の種を記録として残す(運用メモ 3章)。"""
    review = holdout2["_meta"]["review_process"]
    assert review["agreement"] == "30件中20件が一致"
    assert review["random_seed"] == 20261122
    assert "Opus" in review["plan_a"]
    assert "Sonnet" in review["plan_b"]


def test_the_labels_cover_all_thirty_candidates(clauses2, candidates4, holdout2):
    """パートC2: 30件すべてに軸1・軸2のラベルを付ける(評価に入れない12件も)。"""
    assert len(clauses2["clauses"]) == 30
    assert [c["clause_id"] for c in clauses2["clauses"]] == [
        c["candidate_id"] for c in candidates4["candidates"]
    ]
    in_eval = [c for c in clauses2["clauses"] if c["in_action_eval"]]
    assert len(in_eval) == 18
    assert {c["clause_id"] for c in in_eval} == {i["clause_id"] for i in holdout2["items"]}
    for clause in clauses2["clauses"]:
        oracle = clause["oracle"]
        assert oracle["axis1"] in ("ハード制約", "判断ガイド")
        assert oracle["axis2"] in ("宣言的", "手続き的")
        assert oracle["cell_id"]
        assert clause["text_raw"] == clause["text_filled"]
    # 評価に入れる18件は、付録B 表1 のとおり全部 ハード制約 × 宣言的
    for clause in in_eval:
        assert (clause["oracle"]["axis1"], clause["oracle"]["axis2"]) == (
            "ハード制約",
            "宣言的",
        ), clause["clause_id"]


def test_the_excluded_twelve_keep_the_reason_from_the_appendix(clauses2, holdout2):
    """付録B 表2: 外す12件は、理由を正解ファイルに残し、ラベルは条文ファイルに入れる。"""
    excluded = {e["clause_id"]: e for e in holdout2["_meta"]["excluded"]}
    assert len(excluded) == 12
    by_id = {c["clause_id"]: c for c in clauses2["clauses"]}
    for clause_id, entry in excluded.items():
        assert entry["reason"]
        clause = by_id[clause_id]
        assert clause["in_action_eval"] is False
        assert (clause["oracle"]["axis1"], clause["oracle"]["axis2"]) == (
            entry["axis1"],
            entry["axis2"],
        ), clause_id


# --------------------------------------------------------------------------
# 第46回 パートA: 保留用 v2 を開発用 dev3 に移す(P20・Q1)
# --------------------------------------------------------------------------

DEV3_PATH = GOLD_DIR / "action_gold_dev3_v1.json"


@pytest.fixture(scope="module")
def dev3() -> dict:
    return json.loads(DEV3_PATH.read_text(encoding="utf-8"))


def test_dev3_is_the_holdout_v2_moved_to_development(dev3, holdout2):
    """パートA1・W3: 中身を写して split=dev にし、元のファイルは残す。

    条文・正解・承認の記録・出発点の台帳は1文字も変えない。変えてよいのは _meta だけ。
    """
    assert dev3["_meta"]["split"] == "dev"
    assert holdout2["_meta"]["split"] == "holdout"  # 元のファイルはそのまま
    assert dev3["_meta"]["version"] == "dev3_v1"
    assert dev3["items"] == holdout2["items"]
    assert dev3["ledger_seed"] == holdout2["ledger_seed"]
    assert len(dev3["items"]) == 18


def test_dev3_only_changes_the_meta_block(dev3, holdout2):
    """パートA1: _meta で変えたのは、split と記録のための欄だけ。"""
    allowed = {
        "purpose",
        "split",
        "split_note",
        "based_on_gold",
        "version",
        "holdout_run",
        "changes",
        "note",
    }
    changed = {
        key
        for key in set(dev3["_meta"]) | set(holdout2["_meta"])
        if dev3["_meta"].get(key) != holdout2["_meta"].get(key)
    }
    assert changed <= allowed, changed
    # 承認の記録(status・item ごとの review)は動かしていない
    assert dev3["_meta"]["status"] == holdout2["_meta"]["status"] == "approved"
    assert dev3["_meta"]["ledger_source"] == "seed"


def test_dev3_records_the_one_holdout_run(dev3):
    """パートA2: 第45回に1回だけ流したことを _meta に残す。"""
    run = dev3["_meta"]["holdout_run"]
    assert run["frozen_commit"] == "8b8529e"
    assert run["result_file"] == "docs/eval/action_eval_holdout_v2_20261123_v09.json"
    assert "15/18" in run["result"]
    assert "P20" in dev3["_meta"]["note"]


def test_dev3_is_not_stopped_by_the_holdout_check(dev3):
    """パートA4: 承認前に止まる検査は split=holdout のときだけ働く。"""
    check_holdout_is_approved(dev3)  # 止まらない
    broken = copy.deepcopy(dev3)
    broken["items"][0]["review"]["approved_by"] = None
    check_holdout_is_approved(broken)  # dev なので止まらない
    # 念のため、同じ壊し方を holdout のままやると止まる
    still_holdout = copy.deepcopy(dev3)
    still_holdout["_meta"]["split"] = "holdout"
    still_holdout["items"][0]["review"]["approved_by"] = None
    with pytest.raises(HoldoutNotApproved):
        check_holdout_is_approved(still_holdout)


def test_dev3_starts_from_the_two_dev_sets_taught_to_the_end(dev3, dev_v4, dev2):
    """パートA3: 出発点の台帳は v4(18件) → dev2(14件)を足し終えた状態(21件)。"""
    ledger = load_ledger(dev_v4, REGISTRY_PATH)
    for item in dev_v4["items"]:
        teach(ledger, item)
    for item in dev2["items"]:
        teach(ledger, item)
    assert ledger.to_json() == dev3["ledger_seed"]
    assert len(dev3["ledger_seed"]) == 21


def test_dev3_loads_its_ledger_from_the_seed(dev3):
    """`_meta.ledger_source` が seed なら、登録簿ではなく ledger_seed から作る。"""
    ledger = load_ledger(dev3, REGISTRY_PATH)
    assert len(ledger.entries) == 21
