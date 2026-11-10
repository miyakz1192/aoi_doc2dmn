"""保留評価セット(testdata/action_gold/action_gold_holdout_v1.json)のテスト。

第33回 HANDOFF パートC。保留用の正解は、設計を固めてから**1回だけ**流すもので、
結果を見てプロンプト・規則・正解のどれも直さない(P20・5.4節)。そのため、流す前に
確かめられることをここで固定する。

| 確かめること | 根拠 |
| --- | --- |
| 出発点の台帳が、開発用18件を足し終えた状態と一致する | 第33回4.1節 |
| 承認されていない項目があるかぎり評価が止まる | 第33回4.3節 |
| split が holdout で、開発用は dev | P20・4.2節 |
| 正解の語句が条文の部分文字列で、中心の動詞を含む | P4・5.1節 |
| 複数の行為を含む条文の判定(どれか1つと一致すれば正しい) | P25 |
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_frontstage.action_ledger import ActionLedger  # noqa: E402
from src.stage3_frontstage.modality import strip_parentheses  # noqa: E402
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
DEV_PATH = GOLD_DIR / "action_gold_v2.json"
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


def test_holdout_has_fifteen_items_in_the_handoff_order(holdout):
    """第33回4.1節の表の順に15件あること。"""
    assert [i["clause_id"] for i in holdout["items"]] == [
        "ipa_secguide_v4_6_2_1_item1",
        "ipa_secguide_v4_6_2_1_item2",
        "ipa_secguide_v4_phys_1_2_level2",
        "ipa_secguide_v4_phys_1_2_level3_carry_in",
        "ipa_secguide_v4_personnel_6_1_2",
        "ipa_secguide_v4_2_3_3_store",
        "ipa_secguide_v4_2_3_3_wifi",
        "ipa_secguide_v4_5_6_share",
        "ipa_secguide_v4_5_6_transfer_log",
        "ipa_secguide_v4_5_6_outdoor",
        "ipa_secguide_v4_phys_3_phone",
        "ipa_secguide_v4_phys_3_photo",
        "mhlw_r7dec_art11_3",
        "mhlw_r7dec_art16_1",
        "mhlw_r7dec_art58_3_5",
    ]
    assert [i["order"] for i in holdout["items"]] == list(range(1, 16))


def test_the_clause_text_comes_verbatim_from_the_candidates(holdout):
    """条文は候補データから逐語で写したものであること(作り直していない)。"""
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


def test_the_starting_ledger_is_the_dev_set_taught_to_the_end(holdout, dev):
    """4.1節: 出発点の台帳は、開発用18件の正解をすべて足し終えた状態(12件)。

    `ledger_seed` はファイルに固定してあるので、action_gold_v2 から作り直して
    一致することをここで確かめる。
    """
    ledger = load_ledger(dev, REGISTRY_PATH)
    for item in dev["items"]:
        teach(ledger, item)
    rebuilt = ledger.to_json()
    assert len(rebuilt) == 12
    assert rebuilt == holdout["ledger_seed"]


def test_the_starting_ledger_carries_the_hierarchy(holdout):
    """出発点の台帳に、開発用で育った階層が入っていること。"""
    ledger = ActionLedger.from_json(holdout["ledger_seed"])
    assert ledger.find("情報提供").kind == "abstract"
    for child in ("外部送信", "口頭伝達", "データ送信"):
        assert ledger.find(child).parent == "情報提供"
    # データ取り出しは、項目14で情報取得の子になる(それまでは親なし)
    assert ledger.find("データ取り出し").parent is None


def test_every_item_is_unapproved_until_kazuhiro_reviews_it(holdout):
    """4.3節: 承認まで review.approved_by は null。"""
    for item in holdout["items"]:
        assert item["review"]["approved_by"] is None, item["clause_id"]


def test_an_unapproved_holdout_set_stops_the_evaluation(holdout):
    """4.3節: 未承認の項目が1件でもあれば評価を流さない。"""
    with pytest.raises(HoldoutNotApproved, match="承認されていない"):
        check_holdout_is_approved(holdout)
    # 全件に承認者が入れば通る
    approved = json.loads(json.dumps(holdout))
    for item in approved["items"]:
        item["review"]["approved_by"] = "Kazuhiro"
    check_holdout_is_approved(approved)
    # 1件でも欠ければまた止まる
    approved["items"][7]["review"]["approved_by"] = None
    with pytest.raises(HoldoutNotApproved, match="ipa_secguide_v4_5_6_share"):
        check_holdout_is_approved(approved)


def test_the_dev_set_is_not_stopped_by_the_holdout_check(dev):
    """4.3節の検査は保留用だけに当てる(開発用は承認済みなので関係ない)。"""
    check_holdout_is_approved(dev)


def test_every_expected_surface_is_reachable(holdout):
    """P4・5.1節: 正解の語句は条文の部分文字列で、中心の動詞を含むこと。

    括弧は照合の前に取り除く(P24)。これを満たさない正解を置くと、前段が正しく
    答えても surface の3条件で落ちる。
    """
    for item in holdout["items"]:
        clause = strip_parentheses(item["text"])
        for expected in expectations(item):
            surface = expected["surface"]
            assert strip_parentheses(surface) in clause, (item["clause_id"], surface)
            assert expected["core_verb"] in surface, (item["clause_id"], surface)


def test_the_two_items_with_several_actions_declare_alternatives(holdout):
    """P25: 1文に複数の行為がある条文は、正解を並べる。"""
    with_alternatives = {
        i["clause_id"]: expectations(i)
        for i in holdout["items"]
        if i.get("expected_alternatives")
    }
    assert set(with_alternatives) == {
        "ipa_secguide_v4_6_2_1_item2",
        "ipa_secguide_v4_phys_1_2_level2",
    }
    assert [e["normalized"] for e in with_alternatives["ipa_secguide_v4_6_2_1_item2"]] == [
        "アカウント共用",
        "アカウント発行",
    ]
    assert [e["normalized"] for e in with_alternatives["ipa_secguide_v4_phys_1_2_level2"]] == [
        "機器操作",
        "持出し",
    ]


def test_item14_makes_the_data_export_a_child_of_the_new_abstract_action(holdout):
    """4.1節: 項目14で、データ取り出しを情報取得の子にする(適用漏れを作る)。"""
    item = holdout["items"][13]
    assert item["clause_id"] == "mhlw_r7dec_art16_1"
    assert item["expected"]["kind"] == "abstract"
    assert item["ledger_updates"] == [
        {
            "name": "データ取り出し",
            "parent": "情報取得",
            "note": "情報取得が台帳に入った時点で、データ取り出しをその子にする(第33回4.1節)",
        }
    ]


def test_the_items_that_need_context_have_it_verbatim_with_a_locator(holdout):
    """4.1節: context が要るのは項目3・4・6(表の見出し・箇条書きの親文)。"""
    with_context = {i["order"]: i["context"] for i in holdout["items"] if i.get("context")}
    assert sorted(with_context) == [3, 4, 6]
    for order, context in with_context.items():
        assert context, order
        for entry in context:
            assert entry["basis"] == "verbatim"
            assert entry["locator"]
            assert entry["text"].strip()
    assert with_context[3][0]["text"] == "レベル2領域"
    assert with_context[4][0]["text"] == "レベル3領域"
    assert "利用期間中は" in with_context[6][0]["text"]


def test_teaching_an_item_with_alternatives_adds_every_action(holdout):
    """P25: 教師ありでは、並べた正解の Action をすべて台帳に足す。"""
    ledger = ActionLedger.from_json(holdout["ledger_seed"])
    before = set(ledger.names())
    teach(ledger, holdout["items"][2])  # 項目3(機器操作・持出し)
    assert set(ledger.names()) - before == {"機器操作", "持出し"}


def test_the_nearest_expectation_is_the_one_in_the_same_line(holdout):
    """P25: 誤りの分類は、出力に最も近い正解(親子関係にあるもの)と比べて決める。"""
    ledger = ActionLedger.from_json(holdout["ledger_seed"])
    options = expectations(holdout["items"][1])  # アカウント共用／アカウント発行
    # どちらとも親子関係にない Action を選んだときは、並びの先の方と比べる
    assert nearest_expectation(options, "放置", ledger)["normalized"] == "アカウント共用"
