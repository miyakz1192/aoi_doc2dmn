"""正解IR(testdata/ir_gold/)のテスト。

第21回 HANDOFF パートB。`src/stage3_generate/run_ir_gold.py` が行う確認を pytest から
かけ、正解IR の下書きが壊れたら気付けるようにする。

| 確かめること | 根拠 |
| --- | --- |
| manifest の順に全件が C1〜C11 を通り、シナリオがすべて一致する | 設計書6.2・D43 |
| 評価が `registry/` を書き換えない(メモリ上と一時ディレクトリだけで行う) | D43 |
| ファイルのキーが揃い、承認者がすべて None である | 設計書6.2・HANDOFF 3.4節 |
| 表しきれなかった条文が記録されている | 設計書6章冒頭 |

`review.status` は下書き(`draft`)である。Kazuhiro さんが承認して `approved` にしたものだけが
正解になる(設計書6.2 手順5)ので、このテストは status が draft であることまでしか言わない。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_generate.ir_schema import clause_ir_from_json  # noqa: E402
from src.stage3_generate.run_ir_gold import (  # noqa: E402
    REQUIRED_KEYS,
    load_manifest,
    run_all,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = REPO_ROOT / "testdata" / "ir_gold"
REGISTRY_PATH = REPO_ROOT / "registry" / "registry.json"
DMN_DIR = REPO_ROOT / "registry" / "dmn"


@pytest.fixture(scope="module")
def gold_results():
    """manifest の順に全件を通した結果(モジュール内で1回だけ流す)。"""
    return run_all(GOLD_DIR, REGISTRY_PATH, DMN_DIR, verbose=False)


def _gold_files() -> list[Path]:
    return [GOLD_DIR / entry["file"] for entry in load_manifest(GOLD_DIR)]


def test_manifest_lists_every_gold_file():
    """manifest に載っていない正解IR のファイルが取り残されていないこと。"""
    listed = {p.name for p in _gold_files()}
    on_disk = {p.name for p in GOLD_DIR.glob("*.json") if p.name != "manifest.json"}
    assert listed == on_disk


def test_golden_path_is_first():
    """ゴールデンパスを先頭に置く(設計書6.1)。"""
    assert load_manifest(GOLD_DIR)[0]["gold_id"] == "ipa_secguide_v4_6_7_1"


def test_every_gold_ir_passes(gold_results):
    """全件が C1〜C11 を通り、シナリオもすべて一致する。"""
    failed = {r.gold_id: r.problems for r in gold_results if not r.ok}
    assert not failed, failed
    assert len(gold_results) == 5


def test_every_scenario_matches(gold_results):
    """シナリオの合計が全件一致する(既定の行に落ちる場合を含む)。"""
    total = sum(r.scenarios_total for r in gold_results)
    ok = sum(r.scenarios_ok for r in gold_results)
    assert total == ok
    assert total >= len(gold_results), "各正解IR に1件以上のシナリオがある"


def test_gates_are_raised_because_nothing_is_approved(gold_results):
    """承認前なので、全件でゲートが立つ(これが正しい状態)。"""
    for result in gold_results:
        assert "new_action_unapproved" in result.gates, result.gold_id
        assert "new_fact_unapproved" in result.gates, result.gold_id


def test_running_the_gold_irs_does_not_touch_the_registry(gold_results):
    """D43: 評価は registry/ に反映しない。"""
    before = REGISTRY_PATH.read_bytes()
    run_all(GOLD_DIR, REGISTRY_PATH, DMN_DIR, verbose=False)
    assert REGISTRY_PATH.read_bytes() == before
    assert (DMN_DIR / "aoi_transmission.dmn").read_text(encoding="utf-8").count(
        "id_r_art14"
    ) == 0, "第14条の行が永続台帳の DMN に書き込まれていないこと"


@pytest.mark.parametrize("path", _gold_files(), ids=lambda p: p.stem)
def test_gold_file_shape(path):
    """設計書6.2「正解 IR のファイル」のキーが揃い、下書きの状態になっている。"""
    gold = json.loads(path.read_text(encoding="utf-8"))
    for key in REQUIRED_KEYS:
        assert key in gold, key
    assert gold["gold_id"] == path.stem
    assert gold["review"]["status"] == "draft"
    assert gold["review"]["reviewed_by"] is None
    assert gold["review"]["notes"], "レビューで見てほしい点を書く"
    # 承認者はすべて None(承認は人が行う)
    ir = clause_ir_from_json(gold["ir"])
    assert ir.action.approved_by is None
    assert all(f.approved_by is None for f in ir.facts.new_facts)
    assert ir.human_review_status == "pending"


@pytest.mark.parametrize("path", _gold_files(), ids=lambda p: p.stem)
def test_evidence_covers_every_possible_value(path):
    """evidence に、new_facts の各値と各行の根拠が書かれている(設計書6.2)。"""
    gold = json.loads(path.read_text(encoding="utf-8"))
    ir = clause_ir_from_json(gold["ir"])
    evidence = gold["evidence"]

    documented = {(e["fact"], e["value"]) for e in evidence.get("possible_values", [])}
    for fact in ir.facts.new_facts:
        for value in fact.possible_values:
            assert (fact.variable, value) in documented, (fact.variable, value)

    rows = {e["row_id"] for e in evidence.get("rule_rows", [])}
    for row in ir.rule_row_additions:
        assert row.row_id in rows, row.row_id
    for table in ir.new_decision_tables:
        for row in table.rule_rows:
            assert row.row_id in rows, row.row_id

    for change in ir.level_determination.intermediate:
        for name, additions in change.new_output_values.items():
            documented_outputs = {
                (e["output"], e["value"]) for e in evidence.get("new_output_values", [])
            }
            for addition in additions:
                assert (name, addition.value) in documented_outputs

    assert evidence["action_surface"]["value"] == ir.action.surface


def test_not_expressible_clauses_are_recorded():
    """型で表しきれなかった条文が、理由とともに記録されている(設計書6章冒頭)。"""
    manifest = json.loads((GOLD_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["not_expressible"], "表しきれなかった条文の一覧"
    note = (GOLD_DIR / "NOT_EXPRESSIBLE.md").read_text(encoding="utf-8")
    for entry in manifest["not_expressible"]:
        assert entry["clause_id"] in note
        assert entry["reason"]


def test_axis_labels_come_from_the_testdata_oracle():
    """設計書6.2: 軸1・軸2は testdata の正解ラベル(oracle)を使う。

    testdata にある条文(4件)について、正解IR のラベルが oracle と一致することを
    確かめる。あおいの第14条は testdata に無いので、出典の資料のラベルを使っている
    (`source.testdata` が null)。
    """
    clauses = json.loads(
        (REPO_ROOT / "testdata" / "clauses" / "clauses_v1.json").read_text(
            encoding="utf-8"
        )
    )["clauses"]
    oracle = {c["clause_id"]: c for c in clauses}

    checked = 0
    for path in _gold_files():
        gold = json.loads(path.read_text(encoding="utf-8"))
        clause = oracle.get(gold["gold_id"])
        if clause is None:
            assert gold["source"]["testdata"] is None, gold["gold_id"]
            continue
        checked += 1
        assert gold["ir"]["axis1_result"]["label"] == clause["oracle"]["axis1"]
        assert gold["ir"]["axis2_result"]["label"] == clause["oracle"]["axis2"]
        assert clause["oracle"]["cell_id"] == "hard_declarative"
        # 条文本文も testdata の text_raw と一致する(②の分類器が受け取るのと同じテキスト)
        assert gold["ir"]["clause_text"] == clause["text_raw"]
        assert gold["source"]["text_variant"] == "text_raw"
    assert checked == 4, "testdata 由来の正解IR は4件"
