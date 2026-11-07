"""Action 正規化の評価(前段設計書5章・P13)。

`testdata/action_gold/action_gold_v1.json` の18件を、台帳を育てながら順に正規化し、
正解と比べる。台帳の出発点は初期登録の登録簿にある Action 1件(あおいの外部送信)で、
1件を正規化したら**前段の出力ではなく正解の Action を台帳に足してから**次へ進む
(教師あり。P13)。前段が1件誤ったとき、その誤りが後の条文の選択肢に持ち越されると、
後の誤りの原因が分からなくなるためである。

## 何を測るか(5.1節)

| 項目 | 測り方 | 重み |
| --- | --- | --- |
| 誤統合 | 正解が新規(または別の既存)なのに、既存の Action を選んだ | **最重要。0件が合格** |
| 取りこぼし | 正解が既存なのに、新規にした | 中(ゲートで人が拾える) |
| 既存か新規かの判定 | `is_new` が正解と一致するか | 高 |
| 既存の Action の選択 | 既存を選んだとき、正解と同じ Action か | 高(誤りは誤統合) |
| surface | 条文の部分文字列・様相の語を含まない・正解の中心の動詞を含む | 中 |
| 安定性 | k 回の選択の一致。`action_mapping_unstable` が立った件数 | 参考 |

## 使い方

    # Ollama で実際に流す(1件あたり LLM 呼び出し 6〜7回)
    .venv/bin/python3 -m src.stage3_frontstage.run_action_eval

    # LLM を正解を返す偽物に差し替えて、評価の筋だけを確かめる(Ollama 不要)
    .venv/bin/python3 -m src.stage3_frontstage.run_action_eval --use-oracle
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.stage3_generate.registry import Registry

from .action_ledger import NO_MATCH, ActionEntry, ActionLedger
from .action_normalizer import (
    TEMPERATURES,
    ActionNormalizationResult,
    ActionNormalizer,
    OllamaJsonLlm,
)
from .modality import check_surface

GOLD_PATH = "testdata/action_gold/action_gold_v1.json"
REGISTRY_PATH = "registry/registry.json"
OUT_DIR = "out/action_eval"


# --------------------------------------------------------------------------
# 正解との比較
# --------------------------------------------------------------------------


@dataclass
class ItemResult:
    order: int
    clause_id: str
    group: str
    expected: dict
    chosen: str
    is_new: bool
    surface: str
    agreement: str
    gates: list[str]
    mis_merge: bool
    missed: bool
    is_new_correct: bool
    choice_correct: bool
    surface_in_clause: bool
    surface_no_modality: bool
    surface_has_core_verb: bool
    elapsed_seconds: float
    llm_calls: int
    result: ActionNormalizationResult = field(repr=False, default=None)  # type: ignore[assignment]

    @property
    def surface_ok(self) -> bool:
        return (
            self.surface_in_clause
            and self.surface_no_modality
            and self.surface_has_core_verb
        )

    def to_json(self) -> dict:
        return {
            "order": self.order,
            "clause_id": self.clause_id,
            "group": self.group,
            "expected": dict(self.expected),
            "actual": {
                "surface": self.surface,
                "normalized": self.chosen,
                "is_new": self.is_new,
            },
            "verdict": {
                "mis_merge": self.mis_merge,
                "missed": self.missed,
                "is_new_correct": self.is_new_correct,
                "choice_correct": self.choice_correct,
                "surface_ok": self.surface_ok,
                "surface_in_clause": self.surface_in_clause,
                "surface_no_modality": self.surface_no_modality,
                "surface_has_core_verb": self.surface_has_core_verb,
            },
            "agreement": self.agreement,
            "gates": list(self.gates),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "llm_calls": self.llm_calls,
            "trace": self.result.trace.to_json() if self.result else None,
        }


def judge(item: dict, result: ActionNormalizationResult) -> ItemResult:
    """1件の結果を正解と比べる(5.1節)。"""
    expected = item["expected"]
    chosen = result.action.normalized
    is_new = result.action.is_new
    # 誤統合: 正解が新規(または別の既存)なのに、既存の Action を選んだ
    mis_merge = (not is_new) and (
        expected["is_new"] or chosen != expected["normalized"]
    )
    # 取りこぼし: 正解が既存なのに、新規にした
    missed = (not expected["is_new"]) and is_new
    surface_check = check_surface(result.action.surface, item["text"])
    return ItemResult(
        order=item["order"],
        clause_id=item["clause_id"],
        group=item["group"],
        expected=expected,
        chosen=chosen,
        is_new=is_new,
        surface=result.action.surface,
        agreement=result.trace.agreement,
        gates=result.gates.gate_names(),
        mis_merge=mis_merge,
        missed=missed,
        is_new_correct=is_new == expected["is_new"],
        choice_correct=(not is_new) and chosen == expected["normalized"],
        surface_in_clause=surface_check.in_clause,
        surface_no_modality=not surface_check.modality_found,
        surface_has_core_verb=expected["core_verb"] in result.action.surface,
        elapsed_seconds=result.trace.elapsed_seconds,
        llm_calls=result.trace.llm_calls,
        result=result,
    )


# --------------------------------------------------------------------------
# 教師ありの台帳の更新(P13)
# --------------------------------------------------------------------------


def teach(ledger: ActionLedger, item: dict) -> str:
    """正解の Action を台帳に足す(すでにあれば語句だけ足す)。戻り値は行った操作。

    前段の出力ではなく**正解**を足す。1件ずつ独立に評価するため(P13)。
    """
    expected = item["expected"]
    name = expected["normalized"]
    surface = expected["surface"]
    clause_ref = item["clause_ref"]
    if ledger.find(name) is None:
        definition = item.get("definition") or {}
        ledger.add(
            ActionEntry(
                name=name,
                definition=definition.get("text", ""),
                parent=None,  # D60: 階層の位置づけは人の判断。正解にも親は書かない
                surfaces=[],
                actors=["あおい"],
                # 名前は Kazuhiro さんがレビュー済み(第29回 HANDOFF 4.2節)。
                # 定義は条文の語句から作った案なので provisional のまま扱う(PU4)
                status="approved",
                approved_by=item.get("review", {}).get("approved_by"),
                provenance={
                    "origin": "action_gold",
                    "clause_ref": clause_ref,
                    "registered_by": "run_action_eval(教師あり。P13)",
                    "registered_at": item.get("review", {}).get("reviewed_at"),
                    "definition_status": definition.get("status", "provisional"),
                    "definition_basis": definition.get("basis", []),
                    "definition_beyond_clause": definition.get("beyond_clause", []),
                },
            )
        )
        ledger.record_surface(name, surface, clause_ref)
        return f"台帳に追加: {name}"
    ledger.record_surface(name, surface, clause_ref)
    return f"語句を追加: {name} ← {surface}"


# --------------------------------------------------------------------------
# 正解を返す偽の LLM(Ollama 不要の筋の確認用)
# --------------------------------------------------------------------------


class OracleJsonLlm:
    """正解ファイルの expected をそのまま返す偽の LLM。

    評価の筋(教師ありの台帳の更新・集計・ゲート)を Ollama なしで確かめるためのもので、
    LLM の性能を測るものではない。`--use-oracle` で使う。
    """

    def __init__(self, items: list[dict]) -> None:
        self.by_text = {item["text"]: item for item in items}

    def complete_json(self, prompt: str, *, temperature: float) -> dict:
        item = next(
            (i for t, i in self.by_text.items() if t in prompt),
            None,
        )
        if item is None:
            raise AssertionError("OracleJsonLlm: プロンプトに条文が見つかりません")
        expected = item["expected"]
        if "行為を表す語句を、条文本文から" in prompt:
            return {"surface": expected["surface"], "rationale": "(正解)"}
        if "どれに当たるかを1つだけ選んで" in prompt:
            choice = NO_MATCH if expected["is_new"] else expected["normalized"]
            return {
                "choice": choice,
                "reason": "(正解)",
                "definition_part": "",
                "considered": [],
            }
        definition = item.get("definition") or {}
        return {
            "name": expected["normalized"],
            "definition": definition.get("text", ""),
            "parent": None,
            "basis_quotes": [b["quote"] for b in definition.get("basis", [])],
        }


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------


def load_ledger(gold: dict, registry_path: str | Path) -> ActionLedger:
    """台帳の出発点を作る(P10)。初期登録の登録簿から読み、正解ファイルと照合する。"""
    registry = Registry.load(registry_path)
    ledger = ActionLedger.from_json(registry.action_ledger)
    seed = gold.get("ledger_seed", [])
    names = [e["name"] for e in seed]
    if ledger.names() != names:
        raise SystemExit(
            f"台帳の出発点が正解ファイルと合いません: 登録簿 {ledger.names()} / "
            f"正解ファイル {names}"
        )
    for entry in seed:
        found = ledger.find(entry["name"])
        assert found is not None
        if found.definition != entry["definition"]:
            raise SystemExit(
                f"Action {entry['name']!r} の定義が登録簿と正解ファイルで違います:\n"
                f"  登録簿: {found.definition!r}\n  正解ファイル: {entry['definition']!r}"
            )
    return ledger


def run(
    gold_path: str | Path = GOLD_PATH,
    registry_path: str | Path = REGISTRY_PATH,
    *,
    use_oracle: bool = False,
    limit: Optional[int] = None,
    k: int = len(TEMPERATURES),
    verbose: bool = True,
    partial_path: Optional[str | Path] = None,
) -> dict:
    """18件を順に正規化して集計する。

    `partial_path` を渡すと、1件ごとに途中結果を書き出す。Ollama での1回の評価は
    数時間かかるので、途中で落ちても結果を失わないようにするためである。
    LLM の呼び出しが失敗した件は `errors` に記録して次へ進む(1件の失敗で評価全体を
    やり直さない)。
    """
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    items = gold["items"][:limit] if limit else gold["items"]
    ledger = load_ledger(gold, registry_path)
    llm = OracleJsonLlm(gold["items"]) if use_oracle else OllamaJsonLlm()
    temperatures = TEMPERATURES[:k] if k <= len(TEMPERATURES) else TEMPERATURES + (0.3,) * (k - len(TEMPERATURES))
    normalizer = ActionNormalizer(llm, temperatures=temperatures)

    started = time.monotonic()
    results: list[ItemResult] = []
    ledger_log: list[dict] = []
    errors: list[dict] = []
    for item in items:
        if verbose:
            print(
                f"[{item['order']:>2}/{len(items)}] {item['clause_id']} "
                f"(台帳 {len(ledger.entries)}件) ...",
                flush=True,
            )
        try:
            result = normalizer.normalize(
                item["text"],
                item["clause_ref"],
                ledger,
                context=item.get("context"),
            )
        except Exception as exc:  # LLM の呼び出し・応答の失敗で評価全体を止めない
            errors.append(
                {
                    "order": item["order"],
                    "clause_id": item["clause_id"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            if verbose:
                print(f"      [ERROR] {type(exc).__name__}: {exc}", flush=True)
            taught = teach(ledger, item)
            ledger_log.append({"order": item["order"], "action": taught})
            if verbose:
                print(f"      {taught}", flush=True)
            continue
        judged = judge(item, result)
        results.append(judged)
        if verbose:
            mark = "OK" if judged.choice_correct or (
                judged.is_new and judged.expected["is_new"]
            ) else "NG"
            print(
                f"      [{mark}] surface={judged.surface!r} "
                f"→ {judged.chosen!r}(is_new={judged.is_new}) "
                f"一致={judged.agreement} ゲート={judged.gates} "
                f"{judged.elapsed_seconds:.0f}秒／{judged.llm_calls}回",
                flush=True,
            )
        taught = teach(ledger, item)
        ledger_log.append({"order": item["order"], "action": taught})
        if verbose:
            print(f"      {taught}", flush=True)
        if partial_path is not None:
            Path(partial_path).parent.mkdir(parents=True, exist_ok=True)
            Path(partial_path).write_text(
                json.dumps(
                    {
                        "done": len(results),
                        "of": len(items),
                        "errors": errors,
                        "items": [r.to_json() for r in results],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    elapsed = time.monotonic() - started
    summary = {
        "items": len(results),
        "mis_merge": sum(1 for r in results if r.mis_merge),
        "missed": sum(1 for r in results if r.missed),
        "is_new_correct": sum(1 for r in results if r.is_new_correct),
        "existing_choice_total": sum(
            1 for r in results if not r.expected["is_new"]
        ),
        "existing_choice_correct": sum(
            1 for r in results if not r.expected["is_new"] and r.choice_correct
        ),
        "surface_ok": sum(1 for r in results if r.surface_ok),
        "surface_in_clause": sum(1 for r in results if r.surface_in_clause),
        "surface_no_modality": sum(1 for r in results if r.surface_no_modality),
        "surface_has_core_verb": sum(1 for r in results if r.surface_has_core_verb),
        "unstable": sum(1 for r in results if "action_mapping_unstable" in r.gates),
        "surface_unverified": sum(
            1 for r in results if "action_surface_unverified" in r.gates
        ),
        "possible_duplicate": sum(
            1 for r in results if "action_possible_duplicate" in r.gates
        ),
        "elapsed_seconds_total": round(elapsed, 1),
        "elapsed_seconds_per_item": round(elapsed / max(len(results), 1), 1),
        "llm_calls_total": sum(r.llm_calls for r in results),
        "errors": len(errors),
    }
    out = {
        "_meta": {
            "gold": str(gold_path),
            "registry": str(registry_path),
            "llm": "oracle(偽物)" if use_oracle else f"ollama {llm.model}",  # type: ignore[union-attr]
            "k": len(temperatures),
            "temperatures": list(temperatures),
            "note": "誤統合0件が合格の条件(P9・5.3節)。"
            "結果を見てプロンプトを直して再実行することはしない(第29回 HANDOFF 5.4節)",
        },
        "summary": summary,
        "errors": errors,
        "items": [r.to_json() for r in results],
        "ledger_log": ledger_log,
        "ledger_after": ledger.to_json(),
    }
    if verbose:
        print_summary(out)
    return out


def print_summary(out: dict) -> None:
    s = out["summary"]
    print("\n== 集計 ==")
    print(f"  件数: {s['items']}")
    print(f"  **誤統合: {s['mis_merge']}件**（合格の条件は0件）")
    print(f"  取りこぼし: {s['missed']}件")
    print(f"  既存か新規かの判定: {s['is_new_correct']}/{s['items']}")
    print(
        f"  既存の Action の選択: {s['existing_choice_correct']}/"
        f"{s['existing_choice_total']}"
    )
    print(
        f"  surface の3条件: {s['surface_ok']}/{s['items']}"
        f"（部分文字列 {s['surface_in_clause']}／様相なし {s['surface_no_modality']}／"
        f"中心の動詞 {s['surface_has_core_verb']}）"
    )
    print(f"  action_mapping_unstable: {s['unstable']}件")
    print(f"  action_surface_unverified: {s['surface_unverified']}件")
    print(f"  action_possible_duplicate: {s['possible_duplicate']}件")
    if s.get("errors"):
        print(f"  LLM の呼び出しが失敗した件: {s['errors']}件")
        for e in out["errors"]:
            print(f"    - {e['clause_id']}: {e['error']}")
    print(
        f"  所要時間: 合計 {s['elapsed_seconds_total']:.0f}秒／"
        f"1件あたり {s['elapsed_seconds_per_item']:.0f}秒"
        f"（LLM 呼び出し {s['llm_calls_total']}回）"
    )
    wrong = [i for i in out["items"] if i["verdict"]["mis_merge"] or i["verdict"]["missed"]]
    if wrong:
        print("\n== 誤り(1件ずつ) ==")
        for i in wrong:
            kind = "誤統合" if i["verdict"]["mis_merge"] else "取りこぼし"
            runs = i["trace"]["runs"] if i["trace"] else []
            reason = runs[0]["reason"] if runs else ""
            print(
                f"  [{kind}] {i['clause_id']}: 正解 {i['expected']['normalized']}"
                f"(is_new={i['expected']['is_new']}) → "
                f"出力 {i['actual']['normalized']}(is_new={i['actual']['is_new']})"
            )
            print(f"      理由(1回目): {reason}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", default=GOLD_PATH)
    parser.add_argument("--registry", default=REGISTRY_PATH)
    parser.add_argument("--out", default=OUT_DIR)
    parser.add_argument(
        "--use-oracle",
        action="store_true",
        help="LLM を正解を返す偽物に差し替える(Ollama 不要。評価の筋の確認用)",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--k", type=int, default=len(TEMPERATURES))
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out = run(
        args.gold,
        args.registry,
        use_oracle=args.use_oracle,
        limit=args.limit,
        k=args.k,
        partial_path=None if args.use_oracle else out_dir / "partial.json",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "result_oracle.json" if args.use_oracle else "result.json"
    path = out_dir / name
    path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n結果: {path}")
    return 0 if out["summary"]["mis_merge"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
