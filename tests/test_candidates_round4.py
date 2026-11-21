"""新しい保留用の候補(testdata/candidates/candidates_round4.json)のテスト。

第38回 HANDOFF パートE(Q4・D65)。保留用として使うには、**この候補に前段を流さず、
正解の案も②の分類器の結果も付けない**ことが条件である(HANDOFF 6章)。ここでは
その条件と、候補の形・重なりの無さを確かめる。

| 確かめること | 根拠 |
| --- | --- |
| 25〜30件で、話題ごとに分かれている | HANDOFF 6章 |
| 候補37件(round3)と、すでに正解を作った条文と重ならない | HANDOFF 6章・P20 |
| 正解の案・②の分類器の結果が入っていない | HANDOFF 6章 |
| 形は candidates_round3.json と同じ(逐語・出典・位置・抜き出しの注記) | HANDOFF 6章 |
| 1文に複数の行為・使役・既存と同義の動詞・新しい動詞が偏らずに入る | HANDOFF 6章 |
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
CAND_DIR = ROOT / "testdata" / "candidates"
ROUND4_PATH = CAND_DIR / "candidates_round4.json"
ROUND3_PATH = CAND_DIR / "candidates_round3.json"

# 付けてはいけない欄(正解の案・分類器の結果)
FORBIDDEN_KEYS = (
    "expected",
    "expected_alternatives",
    "oracle",
    "oracle_labels",
    "classifier_result",
    "alt_labels",
    "review",
    "ledger_updates",
)


@pytest.fixture(scope="module")
def round4() -> dict:
    return json.loads(ROUND4_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def round3() -> dict:
    return json.loads(ROUND3_PATH.read_text(encoding="utf-8"))


def test_the_count_is_in_the_target_range(round4):
    """HANDOFF 6章: 件数は25〜30件を目安とする。"""
    assert 25 <= len(round4["candidates"]) <= 30


def test_the_ids_are_unique(round4):
    ids = [c["candidate_id"] for c in round4["candidates"]]
    assert len(ids) == len(set(ids))


def test_no_answer_and_no_classifier_result_is_attached(round4):
    """HANDOFF 6章: 正解の案も②の分類器の結果も載せない(保留用として使うため)。"""
    for c in round4["candidates"]:
        for key in FORBIDDEN_KEYS:
            assert key not in c, (c["candidate_id"], key)
    meta = round4["_meta"]
    assert meta["status"] == "pending_human_review"
    assert len(meta["not_run"]) == 3
    assert "正解の案" in meta["labeling_note"] or "正解ラベルは付けていない" in meta["labeling_note"]


def test_nothing_overlaps_the_previous_candidates(round4, round3):
    """HANDOFF 6章・P20: 候補37件と、すでに正解を作った条文と重ならない。"""
    old_ids = {c["candidate_id"] for c in round3["candidates"]}
    old_texts = {c["text_raw"] for c in round3["candidates"]}
    old_locators = {c["source"]["locator"] for c in round3["candidates"]}
    # 正解を作った条文(オラクル・前段の正解・保留用)も重ならない
    for name in ("clauses_v1.json", "clauses_frontstage_v1.json", "clauses_holdout_v1.json"):
        doc = json.loads((CAND_DIR.parent / "clauses" / name).read_text(encoding="utf-8"))
        for clause in doc["clauses"]:
            text = clause.get("text_raw") or clause.get("text")
            if text:
                old_texts.add(text)
    for c in round4["candidates"]:
        assert c["candidate_id"] not in old_ids, c["candidate_id"]
        assert c["text_raw"] not in old_texts, c["candidate_id"]
        assert c["source"]["locator"] not in old_locators, c["candidate_id"]


def test_the_shape_follows_round3(round4, round3):
    """形は candidates_round3.json と同じ(逐語・出典・位置・抜き出しの注記)。"""
    for c in round4["candidates"]:
        assert set(c) == {
            "candidate_id",
            "topic",
            "source",
            "task",
            "text_raw",
            "note",
        }, c["candidate_id"]
        src = c["source"]
        assert set(src) == {"name", "section", "url", "locator", "extraction_note"}
        assert src["name"] in {
            round3["candidates"][0]["source"]["name"],
            "厚生労働省「モデル就業規則」解説版",
        }, src["name"]
        assert src["locator"][0] in "PT"
        assert src["section"].strip()
        assert c["text_raw"].strip() == c["text_raw"]
        assert c["note"].strip()
        assert c["task"] == "round38_partE"


def test_the_clause_text_has_no_bullet_markers(round4):
    """抜き出しの注記どおり、箇条書きの番号・中黒・項番号は落ちている。"""
    for c in round4["candidates"]:
        head = c["text_raw"][0]
        assert head not in "・※①②③④⑤⑥⑦⑧⑨⑩", c["candidate_id"]
        # 条番号(「第１６条　」)は落とすが、本文が「第１項又は…」で始まるのは原典のまま
        assert not re.match(r"^第[０-９一二三四五六七八九十]+条", c["text_raw"]), (
            c["candidate_id"]
        )


def test_the_topics_are_declared_with_counts(round4):
    """話題ごとに抜き出す(D65)。_meta.topics の件数が候補と一致する。"""
    topics = round4["_meta"]["topics"]
    assert len(topics) >= 5
    for t in topics:
        assert t["reason"].strip()
        assert t["count"] == sum(
            1 for c in round4["candidates"] if c["topic"] == t["topic"]
        )
    assert sum(t["count"] for t in topics) == len(round4["candidates"])


def test_the_mix_covers_the_four_kinds(round4):
    """HANDOFF 6章: 1文に複数の行為・使役・既存と同義の動詞・新しい動詞が入る。"""
    mix = round4["_meta"]["mix"]
    assert set(mix) == {
        "使役の条文",
        "1文に複数の行為",
        "既存の Action と同義の動詞",
        "台帳に無い新しい動詞",
    }
    ids = {c["candidate_id"] for c in round4["candidates"]}
    for kind, members in mix.items():
        assert len(members) >= 2, kind
        for member in members:
            assert member in ids, (kind, member)


def test_both_source_documents_are_used(round4):
    """元の2文書(IPA 付録5・厚労 モデル就業規則)の両方から抜き出す。"""
    names = {c["source"]["name"] for c in round4["candidates"]}
    assert len(names) == 2
    by_doc = {
        name: sum(1 for c in round4["candidates"] if c["source"]["name"] == name)
        for name in names
    }
    assert min(by_doc.values()) >= 10, by_doc


def test_no_clause_keeps_a_slash_choice(round4):
    """D72: 原典に「/」で並んだ選択肢が残る条文は入れていない(入れた場合は注記)。"""
    for c in round4["candidates"]:
        if "/" in c["text_raw"]:
            assert "選択肢" in c["note"] or "/" in round4["_meta"]["slash_note"]
