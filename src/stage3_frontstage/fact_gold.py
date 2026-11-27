"""事実の抽出の開発用の正解を読む(前段設計書10.4節 v0.13。第52回パートB)。

開発用25件は2種類が混ざっている。

| 種類 | 中身 | 読み方 |
| --- | --- | --- |
| `ir_gold` | 承認済みの正解 IR 5件(決定表も行も持つ) | これまでどおり IR の JSON を読む |
| `fact_gold` | 第52回 HANDOFF 付録の20件(fact の正解だけ) | この module が IR の形に合わせる |

付録の20件は**決定表を持たない**(決定表の中身は作らない)。それでも教師ありの評価では
「その行為の表がその fact を入力に使う」ことを配線に足さないと、次の同じ行為の条文で
手順3a の寄せ先が分からない(P37 改訂・K61)。そこで、**入力列だけを持つ表**(行の無い
決定表)を1枚作って `new_decision_tables` に置く。表の中身(行・出力・レベル)は作らない。

正解の値は、付録の `facts` 欄をそのまま使う(`run_fact_eval.gold_values` が
`fact_gold` を見て切り替える)。正解 IR 5件のように決定表の行から値を拾う必要はない。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DEV_GOLD_FILE = "testdata/fact_gold/fact_gold_dev_v1.json"


def _wiring_table(entry: dict) -> dict:
    """その行為の表が、その件の fact を入力に使うことだけを示す表(K61)。

    `rule_rows` は空にする(決定表の中身は作らない)。`entry_points` は配線
    ジェネレータと同じ形で、起点の判定に `{actor, action}` を入れる(D16)。
    """
    clause_id = entry["gold_id"]
    return {
        "decision_table_id": f"dt_fact_gold_{clause_id}",
        "decision_id": f"decision_fact_gold_{clause_id}",
        "decision_name": f"{entry['action']}の判定（配線のみ・第52回パートB）",
        "input_columns": [f["variable"] for f in entry["facts"]],
        "knowledge_sources": ["ks_action_request"],
        "rule_rows": [],
    }


def _facts_payload(entry: dict) -> dict:
    """付録の正解から IR の `facts` 欄を組む(教師ありの層3の更新に使う。P42)。"""
    new_facts = []
    referenced: list[str] = []
    value_additions: dict[str, list[dict]] = {}
    for fact in entry["facts"]:
        variable = fact["variable"]
        if fact["status"] == "new":
            new_facts.append(
                {
                    "variable": variable,
                    "primitive": "passthrough",
                    "possible_values": [v["value"] for v in fact["values"]],
                    "other_value_note": fact.get("other_value_note"),
                    "sources": {
                        "value": {"from": "action_request", "path": variable}
                    },
                    # D53: 承認は人。正解の承認は item の `review` に記録してある
                    "approved_by": None,
                }
            )
            continue
        if variable not in referenced:
            referenced.append(variable)
        for value in fact["values"]:
            if not value.get("is_addition"):
                continue
            value_additions.setdefault(variable, []).append(
                {
                    "value": value["value"],
                    "quote": value.get("quote", ""),
                    "approved_by": None,
                }
            )
    return {
        "new_facts": new_facts,
        "referenced_facts": referenced,
        "value_additions": value_additions,
    }


def gold_from_fact_entry(entry: dict) -> dict:
    """付録の1件を、正解 IR と同じ形(評価が読める形)にする。"""
    table = _wiring_table(entry)
    return {
        "gold_id": entry["gold_id"],
        "context": entry.get("context"),
        "review": entry.get("review"),
        "source": entry.get("source"),
        # 正解の値はここから読む(run_fact_eval.gold_values)
        "fact_gold": entry,
        "ir": {
            "clause_ref": entry["clause_ref"],
            "clause_text": entry["clause_text"],
            "actor": entry.get("actor"),
            "action": {
                "surface": entry.get("action_surface", ""),
                "normalized": entry["action"],
                "is_new": False,
                "approved_by": "Kazuhiro",
            },
            "target": {
                "dmn_model": f"fact_gold_{entry['gold_id']}",
                "entry_decision_id": table["decision_id"],
            },
            "facts": _facts_payload(entry),
            "new_decision_tables": [table],
            "rule_row_additions": [],
        },
    }


def load_items(
    gold_file: str | Path = DEV_GOLD_FILE, *, root: Optional[str | Path] = None
) -> list[dict]:
    """正解ファイルの順に読む。`run_fact_eval.load_items` と同じ形を返す。"""
    path = Path(gold_file)
    base = Path(root) if root is not None else path.resolve().parents[2]
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for order, entry in enumerate(payload["items"], start=1):
        kind = entry.get("kind", "fact_gold")
        if kind == "ir_gold":
            gold = json.loads((base / entry["file"]).read_text(encoding="utf-8"))
        elif kind == "fact_gold":
            gold = gold_from_fact_entry(entry)
        else:
            raise ValueError(f"知らない正解の種類 {kind!r}（{entry.get('gold_id')}）")
        items.append({"order": order, "gold": gold, "manifest": entry})
    return items
