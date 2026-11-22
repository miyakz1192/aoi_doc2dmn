"""第32回の生データから、v0.5 の指標を計算し直す(第33回タスクB5)。

**LLM は回さない。**`docs/eval/action_eval_20261108.json`(第32回の生の結果)の Trace を
読み、前段設計書 v0.5 で足した指標と、P24(括弧の扱い)を当てた場合の surface の
3条件を計算し直す。

| 計算し直すもの | 根拠 |
| --- | --- |
| ゲートの誤報(疑いのゲートだけ) | P21 |
| 人に回る割合(設計上の確認だけ／疑いのゲートあり／自動で通った) | P21 |
| 疑いのゲートの適合率(全体・ゲートごと) | P21 |
| 新規候補の段の一致率 | P23 |
| P24 を当てた場合の surface の3条件と action_surface_unverified | P24 |

P24 は、`action_surface_unverified` が立った項目について、**条文と語句の両方から
括弧とその中身を取り除いてから**部分文字列かを確かめ直す(`modality.check_surface`
そのものを当てる)。第32回の1件(項目4)が消える見込みである。

    .venv/bin/python3 -m src.stage3_frontstage.recompute_metrics
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from src.stage3_generate.ir_schema import gate_kind

from .modality import check_surface

RESULT_PATH = "docs/eval/action_eval_20261108.json"
GOLD_PATH = "testdata/action_gold/action_gold_v2.json"


def recompute(result: dict, gold: dict) -> dict:
    """生データから v0.5 の指標を作る。"""
    items = result["items"]
    clause_text = {i["clause_id"]: i["text"] for i in gold["items"]}

    rows: list[dict] = []
    for item in items:
        gates = item["gates"]
        correct = item["verdict"] == "correct"
        # P30: 廃止したゲートも、当時の種類(gate_kind)で数える
        suspicion = [g for g in gates if gate_kind(g) == "suspicion"]
        design = [g for g in gates if gate_kind(g) == "design_check"]
        # P24: 条文と語句の両方から括弧を除いて、部分文字列かを確かめ直す
        trace_run = item["trace"]["run"] if item.get("trace") else {}
        raw = (trace_run.get("candidates") or [{}])[0].get("raw", item["actual"]["surface"])
        recheck = check_surface(raw, clause_text[item["clause_id"]])
        proposal = (item["trace"] or {}).get("new_action_proposal")
        expected_kind = item["expected"].get("kind")
        rows.append(
            {
                "order": item["order"],
                "clause_id": item["clause_id"],
                "verdict": item["verdict"],
                "verdict_label": item["verdict_label"],
                "correct": correct,
                "gates": gates,
                "suspicion_gates": suspicion,
                "design_check_gates": design,
                # P21: 誤報は疑いのゲートだけ
                "false_alarm_gates": suspicion if correct else [],
                "routed_to_human": bool(gates),
                "design_check_only": bool(gates) and not suspicion,
                # P24 を当てた場合
                "surface_in_clause_before": item["surface"]["in_clause"],
                "surface_in_clause_after": recheck.in_clause,
                "surface_no_modality": item["surface"]["no_modality"],
                "surface_has_core_verb": item["surface"]["has_core_verb"],
                # P23: 新規候補の段
                "proposal_name": (proposal or {}).get("name"),
                "proposal_kind": (proposal or {}).get("kind"),
                "proposal_definition": (proposal or {}).get("definition"),
                "proposal_parent": (proposal or {}).get("parent_suggestion"),
                "proposal_basis": (proposal or {}).get("basis_quotes"),
                "expected_kind": expected_kind,
                "kind_matches": (
                    (proposal or {}).get("kind") == expected_kind
                    if proposal is not None and item["expected"]["is_new"]
                    else None
                ),
            }
        )

    with_suspicion = [r for r in rows if r["suspicion_gates"]]
    precision: dict[str, dict[str, int]] = {}
    false_alarm_counts: dict[str, int] = {}
    for row in rows:
        for gate in row["suspicion_gates"]:
            cell = precision.setdefault(gate, {"raised": 0, "really_wrong": 0})
            cell["raised"] += 1
            if not row["correct"]:
                cell["really_wrong"] += 1
        for gate in row["false_alarm_gates"]:
            false_alarm_counts[gate] = false_alarm_counts.get(gate, 0) + 1
    kind_rows = [r for r in rows if r["kind_matches"] is not None]
    after_in_clause = sum(1 for r in rows if r["surface_in_clause_after"])
    return {
        "items": len(rows),
        "false_alarm_items_v04": result["summary"]["false_alarm_items"],
        "false_alarm_gates_v04": result["summary"]["false_alarm_gates"],
        "false_alarm_items": sum(1 for r in rows if r["false_alarm_gates"]),
        "false_alarm_gates": false_alarm_counts,
        "routed_to_human": sum(1 for r in rows if r["routed_to_human"]),
        "routed_design_check_only": sum(1 for r in rows if r["design_check_only"]),
        "routed_with_suspicion": len(with_suspicion),
        "automatic": sum(1 for r in rows if not r["routed_to_human"]),
        "suspicion_precision": {
            "raised": len(with_suspicion),
            "really_wrong": sum(1 for r in with_suspicion if not r["correct"]),
        },
        "suspicion_precision_by_gate": precision,
        "new_action_kind_total": len(kind_rows),
        "new_action_kind_matches": sum(1 for r in kind_rows if r["kind_matches"]),
        "surface_in_clause_before": sum(
            1 for r in rows if r["surface_in_clause_before"]
        ),
        "surface_in_clause_after": after_in_clause,
        "surface_unverified_before": result["summary"]["gates"].get(
            "action_surface_unverified", 0
        ),
        "surface_unverified_after": len(rows) - after_in_clause,
        "surface_ok_before": result["summary"]["surface_ok"],
        "surface_ok_after": sum(
            1
            for r in rows
            if r["surface_in_clause_after"]
            and r["surface_no_modality"]
            and r["surface_has_core_verb"]
        ),
        "rows": rows,
    }


# IR v2 設計書 D82 の名前の対応(旧 → 新)。第32回の出力は旧い名前で書かれている
D82_RENAME = {
    "特権操作": "操作", "施設物品使用": "使用", "情報提供": "提供", "口頭伝達": "伝達",
    "アカウント共用": "共有", "データ取り出し": "取り出し",
    "外部送信": "送信", "データ送信": "送信",
}


def reclassify(result: dict, gold_v3: dict, registry_path: str | Path) -> dict:
    """第32回の出力を、v3 の正解(v0.6 の名前)で分類し直す(第33回追補 タスクE3)。

    **LLM は回さない。**第32回の出力の Action 名を D82 の対応で読み替え、v3 の正解と
    v3 の台帳(教師ありで1件ずつ足す)で4分類をやり直す。ゲートは第32回に立ったもの
    そのままである(名前を読み替えてもゲートの立ち方は変わらない、という仮定を置く)。
    """
    from types import SimpleNamespace

    from .run_action_eval import CLASS_LABELS, SEVERE, classify, load_ledger, teach

    ledger = load_ledger(gold_v3, registry_path)
    by_id = {i["clause_id"]: i for i in result["items"]}
    rows: list[dict] = []
    for item in gold_v3["items"]:
        old = by_id[item["clause_id"]]
        actual_name = D82_RENAME.get(old["actual"]["normalized"], old["actual"]["normalized"])
        fake = SimpleNamespace(
            action=SimpleNamespace(normalized=actual_name, is_new=old["actual"]["is_new"]),
            # P32: 新規どうしは中心の動詞で比べるので、生データの動詞を渡す
            core_verb=old["actual"].get("core_verb", ""),
        )
        teach(ledger, item)  # 正解を足してから分類する(run_action_eval と同じ順)
        verdict = classify(item, fake, ledger)
        gates = old["gates"]
        severe = verdict in SEVERE
        suspicion = [g for g in gates if gate_kind(g) == "suspicion"]
        rows.append(
            {
                "order": item["order"],
                "clause_id": item["clause_id"],
                "expected": item["expected"]["normalized"],
                "expected_is_new": item["expected"]["is_new"],
                "actual": actual_name,
                "actual_is_new": old["actual"]["is_new"],
                "verdict_v2": old["verdict"],
                "verdict_v3": verdict,
                "label_v2": old["verdict_label"],
                "label_v3": CLASS_LABELS[verdict],
                "gates": gates,
                "severe": severe,
                "slipped": severe and not gates,
                "false_alarm_gates": suspicion if verdict == "correct" else [],
            }
        )
    by_verdict = {k: sum(1 for r in rows if r["verdict_v3"] == k) for k in CLASS_LABELS}
    return {
        "items": len(rows),
        "by_verdict": by_verdict,
        "severe": sum(1 for r in rows if r["severe"]),
        "slipped": sum(1 for r in rows if r["slipped"]),
        "false_alarm_items": sum(1 for r in rows if r["false_alarm_gates"]),
        "changed": [r for r in rows if r["verdict_v2"] != r["verdict_v3"]],
        "rows": rows,
    }


def print_reclassify(out: dict) -> None:
    print("== 第32回の出力を v3 の正解で分類し直した結果(追補 タスクE3。LLM は回していない) ==")
    print(f"  件数: {out['items']}")
    print(f"  すり抜け: {out['slipped']}件／重大な誤り: {out['severe']}件")
    print("  " + "・".join(f"{k} {v}" for k, v in out["by_verdict"].items()))
    print(f"  誤報(疑いのゲート): {out['false_alarm_items']}件")
    for r in out["rows"]:
        mark = "" if r["verdict_v2"] == r["verdict_v3"] else "  ← 変わった"
        print(
            f"  [{r['order']:>2}] 正解 {r['expected']}(new={r['expected_is_new']}) "
            f"出力 {r['actual']}(new={r['actual_is_new']}) "
            f"{r['label_v2']} → {r['label_v3']}{mark}"
        )


def print_report(out: dict) -> None:
    print("== 第32回の生データで計算し直した指標(タスクB5。LLM は回していない) ==")
    print(f"  件数: {out['items']}")
    print("\n-- P21: ゲートの誤報(疑いのゲートだけ) --")
    print(
        f"  v0.4 の数え方: {out['false_alarm_items_v04']}件 "
        f"{out['false_alarm_gates_v04']}"
    )
    print(
        f"  v0.5 の数え方: {out['false_alarm_items']}件 {out['false_alarm_gates']}"
    )
    print("\n-- P21: 人に回る割合 --")
    print(
        f"  ゲートが1本以上: {out['routed_to_human']}/{out['items']}"
        f"（疑いのゲートあり {out['routed_with_suspicion']}／"
        f"設計上の確認だけ {out['routed_design_check_only']}）"
    )
    print(f"  自動で通った: {out['automatic']}/{out['items']}")
    print("\n-- P21: 疑いのゲートの適合率 --")
    prec = out["suspicion_precision"]
    if prec["raised"]:
        print(
            f"  全体: {prec['really_wrong']}/{prec['raised']}"
            f"（{prec['really_wrong'] / prec['raised']:.0%}）"
        )
    for gate, cell in sorted(out["suspicion_precision_by_gate"].items()):
        rate = cell["really_wrong"] / cell["raised"]
        print(f"    {gate}: {cell['really_wrong']}/{cell['raised']}（{rate:.0%}）")
    print("\n-- P23: 新規候補の段の一致率 --")
    print(
        f"  {out['new_action_kind_matches']}/{out['new_action_kind_total']}"
    )
    for row in out["rows"]:
        if row["kind_matches"] is False:
            print(
                f"    不一致: [{row['order']}] {row['clause_id']} "
                f"{row['proposal_name']!r} 段={row['proposal_kind']} / "
                f"正解={row['expected_kind']}"
            )
    print("\n-- P24: 括弧を除いてから照合した場合 --")
    print(
        f"  条文の部分文字列: {out['surface_in_clause_before']}/{out['items']} → "
        f"{out['surface_in_clause_after']}/{out['items']}"
    )
    print(
        f"  action_surface_unverified: {out['surface_unverified_before']}件 → "
        f"{out['surface_unverified_after']}件"
    )
    print(
        f"  surface の3条件すべて: {out['surface_ok_before']}/{out['items']} → "
        f"{out['surface_ok_after']}/{out['items']}"
    )
    for row in out["rows"]:
        if row["surface_in_clause_before"] != row["surface_in_clause_after"]:
            print(f"    変わった項目: [{row['order']}] {row['clause_id']}")
    print("\n-- P23: 新規候補の一覧(名前・定義に条件が入っているかは人が判定する) --")
    for row in out["rows"]:
        if row["proposal_name"] is None:
            continue
        print(
            f"  [{row['order']:>2}] {row['clause_id']}: {row['proposal_name']!r}"
            f"（段={row['proposal_kind']}／正解の段={row['expected_kind']}／"
            f"親の案={row['proposal_parent']}）"
        )
        print(f"       定義: {row['proposal_definition']}")
        print(f"       根拠の語句: {row['proposal_basis']}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", default=RESULT_PATH)
    parser.add_argument("--gold", default=GOLD_PATH)
    parser.add_argument("--out", default=None, help="計算し直した指標を書き出す JSON")
    parser.add_argument(
        "--reclassify-v3",
        default=None,
        metavar="GOLD_V3",
        help="第32回の出力を v3 の正解で分類し直す(追補 タスクE3)",
    )
    parser.add_argument("--registry", default="registry/registry.json")
    args = parser.parse_args(argv)

    if args.reclassify_v3:
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        gold_v3 = json.loads(Path(args.reclassify_v3).read_text(encoding="utf-8"))
        out = reclassify(result, gold_v3, args.registry)
        print_reclassify(out)
        if args.out:
            Path(args.out).write_text(
                json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"\n書き出し: {args.out}")
        return 0

    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    out = recompute(result, gold)
    print_report(out)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n書き出し: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
