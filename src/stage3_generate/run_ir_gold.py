"""正解IR(testdata/ir_gold/)を順に配線ジェネレータへ通して確かめる(設計書6.2・D43)。

    .venv/bin/python3 -m src.stage3_generate.run_ir_gold

初期登録した登録簿(`registry/registry.json`)から始め、`manifest.json` の順に各正解IR を
配線ジェネレータへ通す。出力された登録簿を次の IR の入力にし、**registry/ には反映しない**
(promotion は人間レビューを経た別操作。U8)。

## 各正解IR について確かめること

| 確かめること | 根拠 |
| --- | --- |
| ファイルのキーが揃っている(gold_id・source・ir・evidence・scenarios・review) | 設計書6.2「正解 IR のファイル」 |
| `evidence_quote` が条文本文に逐語で実在する(`quote_verified` の裏取り) | 設計書3.4 |
| `vocab_flagged` が②の安全網フラグの実際の結果と一致する | 同上 |
| 立つゲートが `review.expected_gates` と一致する | ゲートは導出なので、手で書いた期待値と突き合わせる |
| 承認者がすべて None である(ゲートが立つのが正しい状態) | 第21回 HANDOFF 3.4節 |
| クロス検証 C1〜C12 を通過する | 設計書6.2 手順3 |
| シナリオがすべて期待どおりになる | 設計書6.2 手順4 |
| 既定の行(ワイルドカード)に落ちるシナリオが1件以上ある | 同上 |
| 「その他」が既定の行に落ちるシナリオが1件以上ある(passthrough の fact を持つ場合) | 同上・D45 |
| 知らない値がエスカレーションになるシナリオが1件以上ある | 同上・D46 |
| evidence の根拠が「逐語」か「正規化」で、**根拠なしが無い** | D44・D47 |

## 値の正規化と根拠の種類(D44・D47)

evidence の各項目は `basis` を持つ。

| basis | 意味 |
| --- | --- |
| `verbatim` | 条文の語句をそのまま使った |
| `normalized` | 条文の語を正規化した(体言化・半角化・共有語の展開など)。`normalization` に種類を書く |
| `default_row` | 既定の行(すべて「-」)。禁止規定の裏なので、条文に逐語の根拠は要らない(D47) |

これ以外(`basis` が無い・知らない値)は「根拠なし」として数え、0件でなければ失敗とする。

## シナリオの評価(D46)

バインディング設定の入口(`scenario_eval.evaluate_scenario`)を通して評価する。fact の値が
`possible_values`(「その他」を含む)に無ければ、DMN を呼ばずに `decision = escalate` に
なる。正解IR のシナリオは、この挙動も期待値として持つ。

## DMN ファイルの持ち回り

既存モデルに行を足す正解IR があるため、DMN ファイルも登録簿と同じように持ち回る。
`registry/dmn/` を一時ディレクトリに写してから始め、各 IR が出力した DMN XML を
そのディレクトリに書き戻す。これで、同じモデルに2件以上の条文が手を入れる場合も、
前の条文の行が残った状態で次の条文を通せる(設計書 D43 は登録簿の持ち回りだけを定めており、
DMN ファイルについては書いていない。**要報告事項**)。
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..stage2_classify.vocab_flags import check_vocab_flag
from .dmn_engine import DmnModel
from .ir_schema import OTHER_VALUE, ClauseIR, clause_ir_from_json, has_other_value
from .registry import Registry, dmn_path
from .scenario_eval import evaluate_scenario
from .wiring_generator import (
    SchemaWiringError,
    existing_facts_used_by_ir,
    generate_wiring_bundle,
)

GOLD_DIR_DEFAULT = "testdata/ir_gold"
REGISTRY_PATH_DEFAULT = "registry/registry.json"
DMN_DIR_DEFAULT = "registry/dmn"

REQUIRED_KEYS = ("gold_id", "source", "ir", "evidence", "scenarios", "review")
# シナリオの種類(`kind` に含まれる語で判定する)
WILDCARD_KINDS = ("既定の行", "ワイルドカード")
OTHER_KINDS = (OTHER_VALUE,)
ESCALATE_KINDS = ("エスカレーション", "escalate")
BASIS_KINDS = ("verbatim", "normalized", "default_row")


@dataclass
class GoldResult:
    """正解IR 1件の確認結果。"""

    gold_id: str
    ok: bool
    outcome: str = ""
    gates: list[str] = field(default_factory=list)
    option_taken: dict = field(default_factory=dict)
    new_facts: list[str] = field(default_factory=list)
    referenced_facts: list[str] = field(default_factory=list)
    routes: list[dict] = field(default_factory=list)
    scenarios_total: int = 0
    scenarios_ok: int = 0
    # evidence の根拠の内訳(D44・D47)
    evidence_basis: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def load_manifest(gold_dir: str | Path) -> list[dict]:
    with (Path(gold_dir) / "manifest.json").open(encoding="utf-8") as f:
        return json.load(f)["order"]


def _check_envelope(gold: dict, ir: ClauseIR, *, passthrough: bool) -> list[str]:
    """正解IR のファイルそのものを確かめる(クロス検証に入る前)。

    `passthrough` は、この IR が passthrough の fact を使うか。使う場合だけ
    「その他」のシナリオを要求する(比較型の fact しか使わない条文には「その他」が
    存在しない。D45)。
    """
    problems: list[str] = []
    for key in REQUIRED_KEYS:
        if key not in gold:
            problems.append(f"キー {key!r} がありません(設計書6.2)")
    text = ir.clause_text
    for name, axis in (("axis1", ir.axis1_result), ("axis2", ir.axis2_result)):
        if axis.evidence_quote and axis.evidence_quote not in text:
            problems.append(
                f"{name}.evidence_quote が条文本文に逐語で実在しません: "
                f"{axis.evidence_quote!r}"
            )
        elif axis.quote_verified is not bool(axis.evidence_quote):
            problems.append(f"{name}.quote_verified が引用の実在と合いません")
        flagged, _ = check_vocab_flag(text)
        if axis.vocab_flagged != flagged:
            problems.append(
                f"{name}.vocab_flagged={axis.vocab_flagged} ですが、②の安全網フラグの"
                f"実際の結果は {flagged} です"
            )

    review = gold.get("review", {})
    expected = review.get("expected_gates")
    if expected is None:
        problems.append("review.expected_gates がありません")
    elif ir.review_gates.gate_names() != list(expected):
        problems.append(
            f"立つゲート {ir.review_gates.gate_names()} が review.expected_gates "
            f"{list(expected)} と一致しません"
        )
    if review.get("status") not in ("draft", "approved", "rejected"):
        problems.append(f"review.status が不正です: {review.get('status')!r}")

    # 承認者はすべて None(ゲートが立つのが正しい状態。第21回 HANDOFF 3.4節)
    if ir.action.is_new and ir.action.approved_by is not None:
        problems.append("action.approved_by が入っています(承認は人が行う)")
    for fact in ir.facts.new_facts:
        if fact.approved_by is not None:
            problems.append(f"fact {fact.variable} の approved_by が入っています")
    for additions in ir.facts.value_additions.values():
        for addition in additions:
            if addition.approved_by is not None:
                problems.append(f"値 {addition.value} の approved_by が入っています")
    for change in ir.level_determination.intermediate:
        for name, additions in change.new_output_values.items():
            for addition in additions:
                if addition.approved_by is not None:
                    problems.append(
                        f"出力値 {name}={addition.value} の approved_by が入っています"
                    )

    # D25: referenced_facts は書いた行と表から決まる(C6 も確かめるが、報告のために見る)
    used = existing_facts_used_by_ir(ir)
    if used != set(ir.facts.referenced_facts):
        problems.append(
            f"referenced_facts {sorted(ir.facts.referenced_facts)} が、IR が使う既存 fact "
            f"{sorted(used)} と一致しません(D25)"
        )

    scenarios = gold.get("scenarios", [])
    kinds = [s.get("kind", "") for s in scenarios]
    if not scenarios:
        problems.append("scenarios がありません")
    else:
        if not any(any(k in kind for k in WILDCARD_KINDS) for kind in kinds):
            problems.append(
                "既定の行(ワイルドカード)に落ちるシナリオがありません(設計書6.2 手順4)"
            )
        if not any(any(k in kind for k in ESCALATE_KINDS) for kind in kinds):
            problems.append(
                "知らない値がエスカレーションになるシナリオがありません(D46)"
            )
        if passthrough and not any(
            any(k in kind for k in OTHER_KINDS) for kind in kinds
        ):
            problems.append(
                f"予約語「{OTHER_VALUE}」が既定の行に落ちるシナリオがありません(D45)"
            )
    return problems


def _count_evidence_basis(evidence: dict) -> dict[str, int]:
    """evidence の根拠を「逐語／正規化／既定の行／根拠なし」に分けて数える(D44・D47)。"""
    counts = {"verbatim": 0, "normalized": 0, "default_row": 0, "none": 0}
    items: list[dict] = []
    for key in ("possible_values", "rule_rows", "new_output_values", "fact_mapping"):
        items.extend(evidence.get(key, []) or [])
    action = evidence.get("action_surface")
    if isinstance(action, dict):
        items.append(action)
    for item in items:
        basis = item.get("basis")
        counts[basis if basis in BASIS_KINDS else "none"] += 1
    return counts


def _has_passthrough_fact(ir: ClauseIR, registry: Registry) -> bool:
    """この IR が passthrough の fact を使うか(「その他」のシナリオを要るかの判定)。"""
    if any(has_other_value(f.primitive) for f in ir.facts.new_facts):
        return True
    for variable in ir.facts.referenced_facts:
        entry = registry.find_fact(variable)
        if entry is not None and has_other_value(entry.get("primitive", "")):
            return True
    return False


def run_one(
    gold: dict, registry: Registry, dmn_dir: Path, *, verbose: bool = True
) -> tuple[GoldResult, Registry]:
    """正解IR 1件を配線ジェネレータへ通し、シナリオを評価する。"""
    ir = clause_ir_from_json(gold["ir"])
    result = GoldResult(
        gold_id=gold.get("gold_id", "(gold_id なし)"),
        ok=True,
        outcome=ir.level_determination.outcome,
        gates=ir.review_gates.gate_names(),
        option_taken=dict(ir.option_taken),
        new_facts=[f.variable for f in ir.facts.new_facts],
        referenced_facts=list(ir.facts.referenced_facts),
    )
    result.evidence_basis = _count_evidence_basis(gold.get("evidence", {}))
    if result.evidence_basis["none"]:
        result.problems.append(
            f"evidence に根拠の種類(basis)が無い項目が {result.evidence_basis['none']}件"
            "あります(D44。逐語なら verbatim、正規化なら normalized、既定の行なら"
            "default_row を書く)"
        )
    result.problems.extend(
        _check_envelope(gold, ir, passthrough=_has_passthrough_fact(ir, registry))
    )

    try:
        bundle = generate_wiring_bundle(ir, registry=registry, dmn_dir=dmn_dir)
    except SchemaWiringError as exc:
        result.problems.append(f"クロス検証で停止: {exc}")
        result.ok = False
        return result, registry

    result.routes = [
        {
            "actor": r["actor"],
            "action": r["action"],
            "decision_id": r["decision_id"],
            "required_facts": list(r["required_facts"]),
        }
        for r in bundle.routing_table["routes"]
    ]

    # 生成した DMN を持ち回り用のディレクトリに書き戻してから評価する
    target = dmn_path(ir.target.dmn_model, dmn_dir)
    target.write_text(bundle.dmn_xml, encoding="utf-8")
    model = DmnModel(str(target))
    scenarios = gold.get("scenarios", [])
    result.scenarios_total = len(scenarios)
    for scenario in scenarios:
        decision_id = scenario.get("decision_id") or ir.target.entry_decision_id
        try:
            actual = evaluate_scenario(
                model, decision_id, scenario["facts"], bindings=bundle.bindings
            )
        except Exception as exc:  # noqa: BLE001 - 評価の失敗も報告に含める
            result.problems.append(f"シナリオ {scenario['name']!r} の評価が失敗: {exc}")
            continue
        mismatches = {
            key: (expected, actual.get(key))
            for key, expected in scenario["expected"].items()
            if actual.get(key) != expected
        }
        if mismatches:
            result.problems.append(
                f"シナリオ {scenario['name']!r} が期待と違います: {mismatches}"
            )
        else:
            result.scenarios_ok += 1

    result.ok = not result.problems
    if verbose:
        _print_one(result)
    return result, Registry.from_json(bundle.registry)


def _print_one(result: GoldResult) -> None:
    mark = "OK" if result.ok else "FAIL"
    print(f"[{mark}] {result.gold_id}")
    print(f"       outcome={result.outcome} gates={result.gates}")
    print(f"       option_taken={result.option_taken}")
    print(
        f"       new_facts={result.new_facts} referenced_facts={result.referenced_facts}"
    )
    for route in result.routes:
        print(
            f"       route: {route['actor']} × {route['action']} -> "
            f"{route['decision_id']} required_facts={route['required_facts']}"
        )
    basis = result.evidence_basis
    print(
        f"       シナリオ {result.scenarios_ok}/{result.scenarios_total} 一致"
        f"／evidence 逐語 {basis.get('verbatim', 0)}・正規化 "
        f"{basis.get('normalized', 0)}・既定の行 {basis.get('default_row', 0)}・"
        f"根拠なし {basis.get('none', 0)}"
    )
    for problem in result.problems:
        print(f"       * {problem}")


def run_all(
    gold_dir: str | Path = GOLD_DIR_DEFAULT,
    registry_path: str | Path = REGISTRY_PATH_DEFAULT,
    dmn_dir: str | Path = DMN_DIR_DEFAULT,
    *,
    verbose: bool = True,
) -> list[GoldResult]:
    """manifest の順に全件を通す。登録簿と DMN は一時ディレクトリで持ち回る。"""
    gold_dir = Path(gold_dir)
    registry = Registry.load(registry_path)
    work = Path(tempfile.mkdtemp(prefix="ir_gold_"))
    work_dmn = work / "dmn"
    source_dmn = Path(dmn_dir)
    if source_dmn.exists():
        shutil.copytree(source_dmn, work_dmn)
    else:
        work_dmn.mkdir(parents=True)

    results: list[GoldResult] = []
    for entry in load_manifest(gold_dir):
        with (gold_dir / entry["file"]).open(encoding="utf-8") as f:
            gold = json.load(f)
        result, registry = run_one(gold, registry, work_dmn, verbose=verbose)
        results.append(result)
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-dir", default=GOLD_DIR_DEFAULT)
    parser.add_argument("--registry", default=REGISTRY_PATH_DEFAULT)
    parser.add_argument("--dmn-dir", default=DMN_DIR_DEFAULT)
    args = parser.parse_args(argv)

    print(f"[正解IR] {args.gold_dir}(登録簿: {args.registry})")
    print("manifest の順に通し、出力された登録簿を次の IR の入力にする(registry/ には反映しない)")
    print()
    results = run_all(args.gold_dir, args.registry, args.dmn_dir)
    failed = [r for r in results if not r.ok]
    print()
    print(
        f"正解IR {len(results)}件: 通過 {len(results) - len(failed)}件／失敗 {len(failed)}件"
    )
    total = sum(r.scenarios_total for r in results)
    ok = sum(r.scenarios_ok for r in results)
    print(f"シナリオ {ok}/{total} 一致")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
