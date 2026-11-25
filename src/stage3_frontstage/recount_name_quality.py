"""v0.9 の結果ファイルから、新規候補の名前の質を数え直す(第46回 パートB)。

**LLM は回さない。**済んだ評価の結果 JSON(`actual.normalized`・`actual.core_verb`・
`is_new`)と正解ファイルだけを読み、5.1節 v0.11 の参考指標2つを当てる。

| 指標 | 判定の規則(`run_action_eval.name_quality`) |
| --- | --- |
| 既存の名前との衝突 | 新規候補の名前が、**その件の正解を足す前**の台帳の Action の名前と完全に一致する |
| 名前に対象が入った疑い | 名前が中心の動詞より長く、動詞を含み、動詞を除いた余り(2文字以上)が条文の中で動詞より前に現れる |

台帳は、正解ファイルの `ledger_seed` から始め、評価と同じ順に正解を足して作り直す
(教師あり。P13)。評価の結果に依らず決まるので、LLM を回さずに同じ状態を再現できる。

正しさの判定(P32)と合格の条件(P9)は変えない。出力は参考指標だけの小さな JSON で、
元の結果ファイルは書き換えない。

    .venv/bin/python3 -m src.stage3_frontstage.recount_name_quality
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .action_ledger import ActionLedger
from .run_action_eval import name_quality, teach

# (名前, 結果ファイル, 正解ファイル, 数え直した結果の置き場所)
SETS = [
    (
        "v4",
        "docs/eval/action_eval_v4_20261122_v09.json",
        "testdata/action_gold/action_gold_v4.json",
        "docs/eval/action_eval_v4_20261122_reclassified_v011.json",
    ),
    (
        "dev2",
        "docs/eval/action_eval_dev2_20261122_v09.json",
        "testdata/action_gold/action_gold_dev2_v1.json",
        "docs/eval/action_eval_dev2_20261122_reclassified_v011.json",
    ),
    (
        "holdout_v2",
        "docs/eval/action_eval_holdout_v2_20261123_v09.json",
        "testdata/action_gold/action_gold_holdout_v2.json",
        "docs/eval/action_eval_holdout_v2_20261123_reclassified_v011.json",
    ),
]


def recount(result: dict, gold: dict) -> dict:
    """1セットを数え直す。戻り値は参考指標だけの JSON。"""
    gold_items = {item["clause_id"]: item for item in gold["items"]}
    ledger = ActionLedger.from_json(gold["ledger_seed"])
    rows: list[dict] = []
    for item in result["items"]:
        gold_item = gold_items[item["clause_id"]]
        # 名前の衝突は、この件の正解を足す**前**の台帳で見る
        names_before = [e.name for e in ledger.entries]
        teach(ledger, gold_item)
        actual = item["actual"]
        if not actual["is_new"]:
            continue
        quality = name_quality(
            actual["normalized"],
            actual["core_verb"],
            gold_item["text"],
            names_before,
        )
        rows.append(
            {
                "order": item["order"],
                "clause_id": item["clause_id"],
                "verdict": item["verdict"],
                "verdict_label": item["verdict_label"],
                "expected": item["expected"]["normalized"],
                "expected_is_new": item["expected"]["is_new"],
                **quality,
                "ledger_size_before": len(names_before),
            }
        )
    return {
        "_meta": {
            "purpose": "新規候補の名前の質(5.1節 v0.11)の参考指標。LLM は回していない",
            "rule_collides": "新規候補の名前が、その件の正解を足す前の台帳の Action の名前と完全に一致する",
            "rule_includes_object": (
                "名前が中心の動詞より長く、動詞を含み、動詞を除いた余り(2文字以上)が"
                "条文の中で動詞より前に現れる"
            ),
            "note": "正しさの判定(P32)と合格の条件(P9)は変えない",
        },
        "summary": {
            "items": len(result["items"]),
            "new_candidates": len(rows),
            "collides_with_existing": sum(1 for r in rows if r["collides_with_existing"]),
            "includes_object": sum(1 for r in rows if r["includes_object"]),
        },
        "new_candidates": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="ファイルに書かない")
    args = parser.parse_args(argv)

    for name, result_path, gold_path, out_path in SETS:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
        gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
        recounted = recount(result, gold)
        summary = recounted["summary"]
        print(
            f"{name}: 新規候補 {summary['new_candidates']}件／"
            f"既存の名前と衝突 {summary['collides_with_existing']}件／"
            f"対象が入った疑い {summary['includes_object']}件"
        )
        for row in recounted["new_candidates"]:
            marks = []
            if row["collides_with_existing"]:
                marks.append("衝突")
            if row["includes_object"]:
                marks.append(f"対象入り（余り {row['extra']!r}）")
            if marks:
                print(
                    f"  [{row['order']:>2}] {row['clause_id']}: {row['name']!r}"
                    f"（動詞 {row['core_verb']!r}・{row['verdict_label']}）"
                    f" — {'／'.join(marks)}"
                )
        if not args.dry_run:
            Path(out_path).write_text(
                json.dumps(recounted, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"  → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
