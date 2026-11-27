"""事実の抽出の開発用25件の正解の単体テスト(第52回パートB)。

| 確かめること | 根拠 |
| --- | --- |
| 25件の順(正解 IR 5件→付録20件)と承認の記録 | パートB1 |
| 条文・参照・行為が Action の正解(v4・dev2・dev3)と同じ | パートB2 |
| 根拠の語句が条文本文か context に逐語である | パートB4 |
| 値が条文の語のままでないものは例外の一覧に載っている | パートB4・D44・FU1 |
| 既存の fact は、その時点の層3にある(値の追加も矛盾しない) | P42 |
| 各件の後で層1・層2の配線も足される | K61・P37 改訂 |
| 手順2 の逐語の照合は条文本文だけを見る(context は見ない) | 手順2・要判断 |
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_frontstage.fact_extractor import (  # noqa: E402
    normalize_value,
    quote_in_clause,
)
from src.stage3_frontstage.fact_gold import load_items  # noqa: E402
from src.stage3_frontstage.fact_ledger import FactLedger  # noqa: E402
from src.stage3_frontstage.run_fact_eval import gold_values, teach  # noqa: E402
from src.stage3_generate.registry import Registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLD_FILE = ROOT / "testdata" / "fact_gold" / "fact_gold_dev_v1.json"

# パートB4 の例外の一覧: 値が条文の語のままでないもの(D44 が許す直し)。
# (順番, 列, 値, 根拠の語句, 理由)
VALUE_NOT_VERBATIM = [
    (6, "requested_privilege", "特権", "特権操作", "行為の語(操作)を外した"),
    (7, "operation_target", "情報機器", "情報機器・設備", "並列の展開(D44)"),
    (7, "operation_target", "設備", "情報機器・設備", "並列の展開(D44)"),
    (7, "operation_authorization", "無断", "無断操作", "行為の語(操作)を外した"),
    (10, "information_classification", "極秘", "極秘又は社外秘", "並列の展開(D44)"),
    (10, "information_classification", "社外秘", "極秘又は社外秘", "並列の展開(D44)"),
    (12, "information_classification", "社外秘", "社外秘又は極秘", "並列の展開(D44)"),
    (12, "information_classification", "極秘", "社外秘又は極秘", "並列の展開(D44)"),
    (13, "leave_target", "機器", "機器や書類", "並列の展開(D44)"),
    (13, "leave_target", "書類", "機器や書類", "並列の展開(D44)"),
    (14, "information_classification", "社外秘", "社外秘又は極秘", "並列の展開(D44)"),
    (14, "information_classification", "極秘", "社外秘又は極秘", "並列の展開(D44)"),
    (
        15,
        "area_type",
        "立入禁止区域",
        "立入禁止又は通行禁止区域",
        "並列で共有された語(区域)の展開(D44)",
    ),
    (
        15,
        "area_type",
        "通行禁止区域",
        "立入禁止又は通行禁止区域",
        "並列で共有された語(区域)の展開(D44)",
    ),
    (
        21,
        "reuse_scope",
        "機器間",
        "機器やサービス間",
        "並列で共有された語(間)の展開(D44)",
    ),
    (
        21,
        "reuse_scope",
        "サービス間",
        "機器やサービス間",
        "並列で共有された語(間)の展開(D44)",
    ),
    (25, "information_classification", "社外秘", "社外秘又は極秘", "並列の展開(D44)"),
    (25, "information_classification", "極秘", "社外秘又は極秘", "並列の展開(D44)"),
]

# FU1(全角→半角)でそろうもの。プログラムが当てる正規化なので例外ではない。
VALUE_NORMALIZED_BY_FU1 = [
    (16, "entrant_age", "20歳未満の者", "２０歳未満の者"),
    (18, "worker_category", "産後8週間を経過していない女性労働者", "産後８週間を経過していない女性労働者"),
]

# 手順2 の逐語の照合に通らない語句(根拠が context にある値)。
QUOTE_ONLY_IN_CONTEXT = [
    (7, "area_level", "レベル2領域"),
    (12, "area_level", "レベル1領域"),
    (14, "storage_medium", "IT機器"),
    (14, "storage_medium", "電子媒体"),
    (14, "usage_period", "利用期間中"),
]


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(GOLD_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def items() -> list[dict]:
    return load_items(GOLD_FILE)


@pytest.fixture(scope="module")
def fact_entries(payload) -> list[dict]:
    return [i for i in payload["items"] if i["kind"] == "fact_gold"]


def context_text(entry: dict) -> str:
    return " ".join(c.get("text", "") for c in entry.get("context") or [])


# --------------------------------------------------------------------------
# 並び・承認(パートB1)
# --------------------------------------------------------------------------


def test_the_dev_gold_has_the_five_ir_golds_first_then_the_twenty(payload):
    kinds = [i["kind"] for i in payload["items"]]
    assert kinds == ["ir_gold"] * 5 + ["fact_gold"] * 20
    assert [i["order"] for i in payload["items"]] == list(range(1, 26))
    manifest = json.loads(
        (ROOT / "testdata" / "ir_gold" / "manifest.json").read_text(encoding="utf-8")
    )
    assert [i["gold_id"] for i in payload["items"][:5]] == [
        e["gold_id"] for e in manifest["order"]
    ]
    # 名前が重ならない(突き合わせは gold_id で引く)
    assert len({i["gold_id"] for i in payload["items"]}) == 25


def test_every_one_of_the_twenty_is_approved_by_kazuhiro(fact_entries):
    assert len(fact_entries) == 20
    for entry in fact_entries:
        assert entry["split"] == "dev"
        assert entry["review"] == {
            "approved_by": "Kazuhiro",
            "reviewed_at": "2026-11-28",
        }


# --------------------------------------------------------------------------
# 条文は Action の正解と同じもの(パートB2)
# --------------------------------------------------------------------------


def test_the_clause_and_the_action_come_from_the_action_gold(payload, fact_entries):
    """条文・参照・行為・context が Action の開発用の正解と一言一句同じであること。"""
    by_id: dict[str, dict] = {}
    for path in payload["_meta"]["action_gold"]:
        for item in json.loads((ROOT / path).read_text(encoding="utf-8"))["items"]:
            by_id.setdefault(item["clause_id"], item)
    for entry in fact_entries:
        src = by_id[entry["clause_id"]]
        assert entry["clause_text"] == src["text"]
        assert entry["clause_ref"] == src["clause_ref"]
        assert entry["action"] == src["expected"]["normalized"]
        assert entry["action_surface"] == src["expected"]["surface"]
        assert (entry.get("context") or []) == (src.get("context") or [])


# --------------------------------------------------------------------------
# 逐語(パートB4)
# --------------------------------------------------------------------------


def test_every_basis_quote_is_verbatim_in_the_clause_or_the_context(fact_entries):
    """根拠の語句は条文本文か context に逐語である(P36・IR v2 1.4節)。"""
    missing = []
    for entry in fact_entries:
        haystack = entry["clause_text"] + " " + context_text(entry)
        for fact in entry["facts"]:
            for value in fact["values"]:
                if value["quote"] not in haystack:
                    missing.append((entry["order"], fact["variable"], value["quote"]))
        for item in entry["not_modeled"]:
            if item["quote"] not in haystack:
                missing.append((entry["order"], "not_modeled", item["quote"]))
    assert missing == []


def test_the_values_that_are_not_verbatim_are_on_the_exception_list(fact_entries):
    """値が条文の語のままでないものは、理由付きの一覧に載っていること(D44)。"""
    found = []
    for entry in fact_entries:
        for fact in entry["facts"]:
            for value in fact["values"]:
                if normalize_value(value["quote"])[0] != normalize_value(
                    value["value"]
                )[0]:
                    found.append(
                        (
                            entry["order"],
                            fact["variable"],
                            value["value"],
                            value["quote"],
                        )
                    )
    assert found == [row[:4] for row in VALUE_NOT_VERBATIM]


def test_the_width_only_differences_are_handled_by_the_program(fact_entries):
    """FU1: 全角→半角はプログラムが当てるので、例外に数えない。"""
    found = [
        (entry["order"], fact["variable"], value["value"], value["quote"])
        for entry in fact_entries
        for fact in entry["facts"]
        for value in fact["values"]
        if value["quote"] != value["value"]
        and normalize_value(value["quote"])[0] == normalize_value(value["value"])[0]
    ]
    assert found == VALUE_NORMALIZED_BY_FU1


def test_the_quotes_that_only_live_in_the_context_fail_the_step_2_check(fact_entries):
    """手順2 の逐語の照合は**条文本文だけ**を見るので、context の語句は通らない。

    手順1 のプロンプトも「条件の語句は条文本文から抜き出すこと」と指示している。
    正解の根拠は context にあるので正解そのものは正しいが、この5件は正解どおりに
    出しても `fact_value_unverified` が立つ。扱いは第53回の要判断として報告した。
    """
    failing = [
        (entry["order"], fact["variable"], value["quote"])
        for entry in fact_entries
        for fact in entry["facts"]
        for value in fact["values"]
        if not quote_in_clause(value["quote"], entry["clause_text"])
    ]
    assert failing == QUOTE_ONLY_IN_CONTEXT
    # どれも context には逐語である
    for order, _variable, quote in failing:
        entry = next(e for e in fact_entries if e["order"] == order)
        assert quote in context_text(entry)


# --------------------------------------------------------------------------
# 読み込みと教師あり(P42・K61)
# --------------------------------------------------------------------------


def test_the_loader_turns_the_twenty_into_the_shape_the_eval_reads(items):
    assert len(items) == 25
    entry = next(i for i in items if i["gold"]["gold_id"] == "ipa_secguide_v4_6_1_5")
    ir = entry["gold"]["ir"]
    assert ir["action"]["normalized"] == "操作"
    # 正解の値は付録の facts 欄から読む(決定表の行は要らない)
    values = gold_values(entry["gold"])
    assert [(v.variable, v.value, v.is_new) for v in values] == [
        ("requested_privilege", "特権", False),
        ("authentication_method", "多要素認証", True),
        ("device_ownership", "社有機器", True),
        ("device_ownership", "私有機器", True),
        ("access_route", "指定経路", True),
    ]
    # 配線だけの表(行は作らない)
    table = ir["new_decision_tables"][0]
    assert table["rule_rows"] == []
    assert table["input_columns"] == [
        "requested_privilege",
        "authentication_method",
        "device_ownership",
        "access_route",
    ]
    assert ir["target"]["entry_decision_id"] == table["decision_id"]


def test_teaching_the_twenty_keeps_the_existing_facts_consistent(items):
    """`existing` の fact はその時点の層3にあり、`is_addition` と矛盾しない(P42)。"""
    registry = Registry.load(ROOT / "registry" / "registry.json")
    ledger = FactLedger.from_registry(registry)
    problems = []
    for item in items:
        gold = item["gold"]
        entry = gold.get("fact_gold")
        if entry is not None:
            for fact in entry["facts"]:
                found = ledger.find(fact["variable"])
                if fact["status"] == "existing" and found is None:
                    problems.append((item["order"], fact["variable"], "層3に無い"))
                    continue
                if fact["status"] == "new":
                    if found is not None:
                        problems.append((item["order"], fact["variable"], "もうある"))
                    continue
                for value in fact["values"]:
                    normalized, _ = normalize_value(value["value"])
                    have = normalized in found.clause_values()
                    if value.get("is_addition") and have:
                        problems.append(
                            (item["order"], fact["variable"], f"{normalized} はもうある")
                        )
                    if not value.get("is_addition") and not have:
                        problems.append(
                            (item["order"], fact["variable"], f"{normalized} が無い")
                        )
        teach(ledger, gold)
    assert problems == []
    # 初期登録の7件に、25件で足した fact が積み上がる
    assert len(ledger.entries) == 44


def test_teaching_also_wires_the_action_to_its_facts(items):
    """K61: 層1・層2も教えるので、同じ行為の次の条文で寄せ先が分かる。"""
    registry = Registry.load(ROOT / "registry" / "registry.json")
    ledger = FactLedger.from_registry(registry)
    for item in items:
        if item["gold"]["gold_id"] == "ipa_secguide_v4_2_3_3_send":
            break
        teach(ledger, item["gold"])
    # 1つ前の「送信」の条文(転送)で足した fact が、次の「送信」の寄せ先に入っている
    scope = ledger.action_scope("送信") or []
    assert "send_target" in scope and "destination" in scope
    # 別の行為(操作)の fact は入らない(P37 改訂)
    assert "authentication_method" not in scope
    assert ledger.match_value("従業員個人のアドレス", action="送信").chosen == (
        "destination"
    )
    assert ledger.match_value("従業員個人のアドレス", action="保存").chosen is None
