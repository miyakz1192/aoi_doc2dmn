"""事実の抽出の保留用の候補(testdata/candidates/candidates_round5.json)のテスト。

第48回 HANDOFF パートD(10.5節・P20)。保留用として使うには、**この候補に前段を流さず、
正解の案も②の分類器の結果も付けない**ことが条件である。ここではその条件と、候補の形・
重なりの無さ・話題と条件の種類の混ざり方を確かめる。

| 確かめること | 根拠 |
| --- | --- |
| 20件で、話題ごとに2〜3件ずつ | HANDOFF パートD |
| round3・round4、および正解を作った条文と重ならない | HANDOFF パートD・P20 |
| 正解の案・②の分類器の結果が入っていない | HANDOFF パートD・P20 |
| 形は candidates_round4.json と同じ(逐語・出典・位置・抜き出しの注記) | HANDOFF パートD |
| 対象・主体の属性・状況・状態の4種類が入る | P35 |
| 評価の判断だけの条文・「/」が残る条文を入れない | D51・D72 |
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
ROUND5_PATH = CAND_DIR / "candidates_round5.json"

FORBIDDEN_KEYS = (
    "expected",
    "expected_alternatives",
    "oracle",
    "oracle_labels",
    "classifier_result",
    "alt_labels",
    "review",
    "ledger_updates",
    "facts",
    "ir",
)

# P35: 取り出す条件の4種類
CONDITION_KINDS = ("対象", "主体の属性", "状況", "状態")


@pytest.fixture(scope="module")
def round5() -> dict:
    return json.loads(ROUND5_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def previous() -> list[dict]:
    out = []
    for name in ("candidates_round1", "candidates_round2", "candidates_round3", "candidates_round4"):
        path = CAND_DIR / f"{name}.json"
        if path.exists():
            out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def test_there_are_twenty_candidates(round5):
    """HANDOFF パートD: 20件。"""
    assert len(round5["candidates"]) == 20


def test_the_ids_are_unique(round5):
    ids = [c["candidate_id"] for c in round5["candidates"]]
    assert len(ids) == len(set(ids))


def test_no_answer_and_no_classifier_result_is_attached(round5):
    """P20: 正解の案も②の分類器の結果も載せない(保留用として使うため)。"""
    for c in round5["candidates"]:
        for key in FORBIDDEN_KEYS:
            assert key not in c, (c["candidate_id"], key)
    meta = round5["_meta"]
    assert meta["status"] == "pending_human_review"
    assert len(meta["not_run"]) == 3
    assert "正解" in meta["labeling_note"]


def test_nothing_overlaps_the_previous_candidates_or_the_gold(round5, previous):
    """P20: これまでの候補と、すでに正解を作った条文と重ならない。"""
    old_ids: set[str] = set()
    old_texts: set[str] = set()
    old_locators: set[str] = set()
    for doc in previous:
        for c in doc["candidates"]:
            # round1・round2 は欄の名前が違う(clause_id)
            old_ids.add(c.get("candidate_id") or c.get("clause_id", ""))
            old_texts.add(c.get("text_raw") or c.get("text", ""))
            old_locators.add((c.get("source") or {}).get("locator", ""))
    for name in (
        "clauses_v1.json",
        "clauses_frontstage_v1.json",
        "clauses_holdout_v1.json",
        "clauses_holdout_v2.json",
    ):
        doc = json.loads((CAND_DIR.parent / "clauses" / name).read_text(encoding="utf-8"))
        for clause in doc["clauses"]:
            for key in ("text_raw", "text", "text_filled"):
                if clause.get(key):
                    old_texts.add(clause[key])
    # 正解 IR の条文(5件)とも重ならない
    for path in (CAND_DIR.parent / "ir_gold").glob("*.json"):
        gold = json.loads(path.read_text(encoding="utf-8"))
        if "ir" in gold:
            old_texts.add(gold["ir"]["clause_text"])
    for c in round5["candidates"]:
        assert c["candidate_id"] not in old_ids, c["candidate_id"]
        assert c["text_raw"] not in old_texts, c["candidate_id"]
        assert c["source"]["locator"] not in old_locators, c["candidate_id"]


def test_the_shape_follows_round4(round5):
    """形は candidates_round4.json と同じ。"""
    for c in round5["candidates"]:
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
        assert src["locator"][0] in "PT"
        assert src["section"].strip()
        assert src["url"].startswith("https://")
        assert c["text_raw"].strip() == c["text_raw"]
        assert c["text_raw"].endswith("。")
        assert c["note"].strip()
        assert c["task"] == "round48_partD"


def test_the_clause_text_is_verbatim_from_the_source(round5):
    """逐語で写してあるか(原典の .docx から読み直して照合する)。"""
    docx = pytest.importorskip("docx")
    docs = {
        "IPA": docx.Document(
            ROOT / "testdata" / "sources" / "ipa_security_regulations_sample.docx"
        ),
        "厚生労働省": docx.Document(
            ROOT / "testdata" / "sources" / "mhlw_model_rules_sample.docx"
        ),
    }
    for c in round5["candidates"]:
        loc = c["source"]["locator"]
        key = "IPA" if c["source"]["name"].startswith("IPA") else "厚生労働省"
        doc = docs[key]
        if loc.startswith("P"):
            para_no, sent_no = loc[1:].split(".")
            text = doc.paragraphs[int(para_no) - 1].text.strip()
            parts = [s.strip() for s in re.split(r"(?<=。)", text) if s.strip()]
            source_line = parts[int(sent_no) - 1]
        else:
            m = re.match(r"T(\d+)\.R(\d+)\.C(\d+)\.L(\d+)", loc)
            assert m, loc
            ti, ri, ci, li = (int(g) for g in m.groups())
            cell = doc.tables[ti - 1].rows[ri - 1].cells[ci - 1]
            source_line = cell.text.split("\n")[li - 1].strip()
        # 落としてよいのは条番号・項番号・箇条書きの番号だけ
        assert c["text_raw"] in source_line, (c["candidate_id"], source_line)
        dropped = source_line.replace(c["text_raw"], "")
        assert re.fullmatch(r"[第０-９条　 ①-⑭]*", dropped), (
            c["candidate_id"],
            dropped,
        )


def test_the_clause_text_has_no_numbering_or_slash_choice(round5):
    """条番号・箇条書きの番号は落ち、「/」の選択肢は残っていない(D72)。"""
    for c in round5["candidates"]:
        assert c["text_raw"][0] not in "・※①②③④⑤⑥⑦⑧⑨⑩", c["candidate_id"]
        assert not re.match(r"^第[０-９一二三四五六七八九十]+条", c["text_raw"])
        # 「/」が残る条文は選んでいない(「SSL/TLS」のような語は対象外だが、今回は無い)
        assert "/" not in c["text_raw"], c["candidate_id"]
        # 空欄(値が決まらない)を含む条文も選んでいない
        assert "　　" not in c["text_raw"], c["candidate_id"]


def test_the_topics_come_in_groups_of_two_or_three(round5):
    """パートD: 同じ話題の条文が2〜3件ずつ続く(fact の再利用を測るため)。"""
    topics = round5["_meta"]["topics"]
    assert len(topics) >= 5
    for t in topics:
        assert t["reason"].strip()
        assert t["count"] == sum(
            1 for c in round5["candidates"] if c["topic"] == t["topic"]
        )
        assert 1 <= t["count"] <= 3, t
    assert sum(t["count"] for t in topics) == len(round5["candidates"])
    # 2〜3件の固まりが大半である(1件だけの話題は1つまで)
    assert sum(1 for t in topics if t["count"] == 1) <= 1


def test_the_four_condition_kinds_are_covered(round5):
    """P35: 対象・主体の属性・状況・状態の条件がそれぞれ含まれる。"""
    kinds = round5["_meta"]["condition_kinds"]
    assert set(kinds) == set(CONDITION_KINDS)
    ids = {c["candidate_id"] for c in round5["candidates"]}
    for kind, members in kinds.items():
        assert len(members) >= 3, kind
        for member in members:
            assert member in ids, (kind, member)


def test_both_source_documents_are_used(round5):
    """元の2文書から抜き出す。どちらも8件以上(偏りの理由は _meta に書く)。"""
    names = {c["source"]["name"] for c in round5["candidates"]}
    assert len(names) == 2
    by_doc = {
        name: sum(1 for c in round5["candidates"] if c["source"]["name"] == name)
        for name in names
    }
    assert min(by_doc.values()) >= 8, by_doc
    assert round5["_meta"]["document_split_note"].strip()


def test_what_was_excluded_is_recorded(round5):
    """D51・D72: 外した条文の理由を記録する。"""
    note = "\n".join(round5["_meta"]["excluded_note"])
    assert "D51" in note
    assert "D72" in note
