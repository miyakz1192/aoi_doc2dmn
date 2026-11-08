"""Action 正規化の評価(前段設計書5章・P9・P13 v0.4)。

`testdata/action_gold/action_gold_v2.json` の18件を、台帳を育てながら順に正規化し、
正解と比べる。台帳の出発点は初期登録の登録簿にある Action 1件(あおいの外部送信)で、
1件を正規化したら**前段の出力ではなく正解の Action を台帳に足してから**次へ進む
(教師あり。P13)。前段が1件誤ったとき、その誤りが後の条文の選択肢に持ち越されると、
後の誤りの原因が分からなくなるためである。

## 何を測るか(5.1節 v0.4)

誤りを重さで4つに分け、**重大な誤りがゲートに捕まっているか**を合格の条件にする(P9)。

| 分類 | 意味 | 重さ |
| --- | --- | --- |
| 誤統合 | 親子関係にない既存の Action に寄せた | **重大** |
| 適用漏れ | 正解は抽象的な Action なのに、その子孫を選んだ | **重大** |
| 過剰適用 | 正解は具体的な Action なのに、その祖先を選んだ | 中(安全側に倒れる) |
| 取りこぼし | 正解は既存なのに、新規にした | 軽(ゲートで人が拾える) |

- **すり抜け**: 重大な誤りのうち、ゲートが1つも立たなかった件数。**0件が合格の条件**
- **ゲートの誤報**: 正しい結果なのに立ったゲートの件数(人の負担の目安)
- surface の3条件(条文の部分文字列・様相の語を含まない・正解の中心の動詞を含む)

親子関係は、その時点の台帳(教師ありで足した正解)から引く(4.2節)。正解の Action を
足してから分類するので、正解が新規の場合も親子関係を見られる。

## 使い方

    # Ollama で実際に流す(1件あたり LLM 呼び出し 2〜3回。k=1)
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
    TEMPERATURE,
    ActionNormalizationResult,
    ActionNormalizer,
    OllamaJsonLlm,
)
from .modality import check_surface

GOLD_PATH = "testdata/action_gold/action_gold_v2.json"
# 重大な誤り(実行時に別の判定に入る・禁止が効かない)。すり抜けを0件にするのが合格の条件
SEVERE = ("mis_merge", "under_applied")
CLASS_LABELS = {
    "correct": "正しい",
    "mis_merge": "誤統合",
    "under_applied": "適用漏れ",
    "over_applied": "過剰適用",
    "missed": "取りこぼし",
}
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
    chosen_kind: str
    is_new: bool
    surface: str
    removed_modality: list[str]
    core_verb: str
    verdict: str
    gates: list[str]
    surface_in_clause: bool
    surface_no_modality: bool
    surface_has_core_verb: bool
    elapsed_seconds: float
    llm_calls: int
    result: ActionNormalizationResult = field(repr=False, default=None)  # type: ignore[assignment]

    @property
    def correct(self) -> bool:
        return self.verdict == "correct"

    @property
    def severe(self) -> bool:
        return self.verdict in SEVERE

    @property
    def slipped(self) -> bool:
        """重大な誤りなのに、ゲートが1つも立たなかった(P9 の合格の条件)。"""
        return self.severe and not self.gates

    @property
    def false_alarm_gates(self) -> list[str]:
        """正しい結果なのに立ったゲート(5.1節のゲートの誤報)。

        `new_action_unapproved` は「新しい Action は必ず人が承認する」という設計
        どおりの動きなので誤報に数えない(D14・D53)。
        """
        if not self.correct:
            return []
        return [g for g in self.gates if g != "new_action_unapproved"]

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
                "removed_modality": list(self.removed_modality),
                "core_verb": self.core_verb,
                "normalized": self.chosen,
                "kind": self.chosen_kind,
                "is_new": self.is_new,
            },
            "verdict": self.verdict,
            "verdict_label": CLASS_LABELS[self.verdict],
            "severe": self.severe,
            "slipped": self.slipped,
            "false_alarm_gates": self.false_alarm_gates,
            "surface": {
                "ok": self.surface_ok,
                "in_clause": self.surface_in_clause,
                "no_modality": self.surface_no_modality,
                "has_core_verb": self.surface_has_core_verb,
            },
            "gates": list(self.gates),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "llm_calls": self.llm_calls,
            "trace": self.result.trace.to_json() if self.result else None,
        }


def classify(item: dict, result: ActionNormalizationResult, ledger: ActionLedger) -> str:
    """1件の結果を4つの誤りに分ける(5.1節 v0.4)。

    台帳は**正解を足した後**のものを渡す。正解が新規の Action のときも、その段と親が
    台帳に入っていなければ、適用漏れ(子孫を選んだ)と過剰適用(祖先を選んだ)を
    区別できないためである。
    """
    expected = item["expected"]
    chosen = result.action.normalized
    if result.action.is_new:
        # 新規にした: 正解も新規なら正しい(名前は文字列で比べない。5.1節)
        return "correct" if expected["is_new"] else "missed"
    if chosen == expected["normalized"]:
        return "correct"
    relation = ledger.relation(expected["normalized"], chosen)
    if relation == "descendant":
        # 正解(抽象)の子孫を選んだ = 抽象的な禁止が子にしか付かない(適用漏れ)
        return "under_applied"
    if relation == "ancestor":
        # 正解(具体)の祖先を選んだ = 禁止が広くかかりすぎる(過剰適用)
        return "over_applied"
    return "mis_merge"


def judge(item: dict, result: ActionNormalizationResult, ledger: ActionLedger) -> ItemResult:
    """1件の結果を正解と比べる(5.1節)。"""
    expected = item["expected"]
    surface_check = check_surface(result.action.surface, item["text"])
    run = result.trace.run
    return ItemResult(
        order=item["order"],
        clause_id=item["clause_id"],
        group=item["group"],
        expected=expected,
        chosen=result.action.normalized,
        chosen_kind=result.chosen_kind,
        is_new=result.action.is_new,
        surface=result.action.surface,
        removed_modality=list(run.candidates[0].check.removed_modality)
        if run.candidates
        else [],
        core_verb=run.candidates[0].core_verb if run.candidates else "",
        verdict=classify(item, result, ledger),
        gates=result.gates.gate_names(),
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

    前段の出力ではなく**正解**を足す。1件ずつ独立に評価するため(P13)。段(kind)と
    親(parent)も正解どおりに足す(4.2節)。`ledger_updates` があれば、すでに台帳に
    ある Action の親を付け替える(項目3で外部送信に親が付く。4.1節)。
    """
    expected = item["expected"]
    name = expected["normalized"]
    surface = expected["surface"]
    core_verb = expected.get("core_verb", "")
    clause_ref = item["clause_ref"]
    actions: list[str] = []
    if ledger.find(name) is None:
        definition = item.get("definition") or {}
        ledger.add(
            ActionEntry(
                name=name,
                definition=definition.get("text", ""),
                kind=expected.get("kind", "concrete"),
                parent=expected.get("parent"),
                # excludes は空にする(第31回 HANDOFF 4.1節。正解の先取りになる)
                excludes=[],
                surfaces=[],
                actors=["あおい"],
                # 名前・段・親は Kazuhiro さんが承認済み(2026-11-08 の案X)。
                # 定義は書き直した案なので provisional のまま(P18)
                name_status="approved",
                definition_status=definition.get("status", "provisional"),
                approved_by=item.get("review", {}).get("approved_by"),
                provenance={
                    "origin": "action_gold",
                    "clause_ref": clause_ref,
                    "registered_by": "run_action_eval(教師あり。P13)",
                    "registered_at": item.get("review", {}).get("reviewed_at"),
                    "definition_basis": definition.get("basis", []),
                    "definition_general_terms": definition.get("general_terms", []),
                },
            )
        )
        actions.append(f"台帳に追加: {name}({expected.get('kind', 'concrete')})")
    else:
        actions.append(f"語句を追加: {name} ← {surface}")
    ledger.record_surface(name, surface, clause_ref, core_verb)
    for update in item.get("ledger_updates", []):
        entry = ledger.find(update["name"])
        if entry is None:
            continue
        if "parent" in update:
            entry.parent = update["parent"]
            actions.append(f"親を付けた: {update['name']} → {update['parent']}")
    return "／".join(actions)


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
            return {
                "candidates": [
                    {
                        "surface": expected["surface"],
                        "core_verb": expected.get("core_verb", ""),
                    }
                ],
                "rationale": "(正解)",
            }
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
            "kind": expected.get("kind", "concrete"),
            # P6: 親の案は Trace にだけ置く。正解の親をそのまま返す
            "parent": expected.get("parent"),
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
    normalizer = ActionNormalizer(llm)

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
        # 正解を台帳に足してから分類する(親子関係を台帳から引くため。4.2節)
        taught = teach(ledger, item)
        ledger_log.append({"order": item["order"], "action": taught})
        judged = judge(item, result, ledger)
        results.append(judged)
        if verbose:
            mark = "OK" if judged.correct else ("すり抜け" if judged.slipped else "NG")
            print(
                f"      [{mark}] {CLASS_LABELS[judged.verdict]} "
                f"surface={judged.surface!r}"
                f"(削った様相={judged.removed_modality}／動詞={judged.core_verb!r}) "
                f"→ {judged.chosen!r}({judged.chosen_kind}, is_new={judged.is_new}) "
                f"ゲート={judged.gates} "
                f"{judged.elapsed_seconds:.0f}秒／{judged.llm_calls}回",
                flush=True,
            )
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
    gate_counts: dict[str, int] = {}
    false_alarm_counts: dict[str, int] = {}
    for item_result in results:
        for gate in item_result.gates:
            gate_counts[gate] = gate_counts.get(gate, 0) + 1
        for gate in item_result.false_alarm_gates:
            false_alarm_counts[gate] = false_alarm_counts.get(gate, 0) + 1
    summary = {
        "items": len(results),
        # P9: すり抜け(ゲートが1つも立たなかった重大な誤り)が0件なら合格
        "slipped": sum(1 for r in results if r.slipped),
        "severe": sum(1 for r in results if r.severe),
        "correct": sum(1 for r in results if r.correct),
        "by_verdict": {
            key: sum(1 for r in results if r.verdict == key) for key in CLASS_LABELS
        },
        "is_new_correct": sum(
            1 for r in results if r.is_new == r.expected["is_new"]
        ),
        "existing_choice_total": sum(1 for r in results if not r.expected["is_new"]),
        "existing_choice_correct": sum(
            1
            for r in results
            if not r.expected["is_new"] and not r.is_new and r.chosen == r.expected["normalized"]
        ),
        "surface_ok": sum(1 for r in results if r.surface_ok),
        "surface_in_clause": sum(1 for r in results if r.surface_in_clause),
        "surface_no_modality": sum(1 for r in results if r.surface_no_modality),
        "surface_has_core_verb": sum(1 for r in results if r.surface_has_core_verb),
        "gates": gate_counts,
        "false_alarm_items": sum(1 for r in results if r.false_alarm_gates),
        "false_alarm_gates": false_alarm_counts,
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
            "k": 1,
            "temperature": TEMPERATURE,
            "note": "ゲートをすり抜けた重大な誤り(誤統合・適用漏れ)が0件なら合格"
            "(P9・5.3節 v0.4)。結果を見てプロンプトを直して再実行することはしない"
            "(第31回 HANDOFF 5章)",
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
    print(
        f"  **すり抜け(ゲートが立たなかった重大な誤り): {s['slipped']}件**"
        "（合格の条件は0件。P9）"
    )
    print(f"  重大な誤り(誤統合・適用漏れ): {s['severe']}件")
    for key, label in CLASS_LABELS.items():
        print(f"    {label}: {s['by_verdict'][key]}件")
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
    print("  立ったゲート:")
    for gate, count in sorted(s["gates"].items(), key=lambda kv: -kv[1]):
        print(f"    {gate}: {count}件")
    print(f"  ゲートの誤報: {s['false_alarm_items']}件 {s['false_alarm_gates']}")
    if s.get("errors"):
        print(f"  LLM の呼び出しが失敗した件: {s['errors']}件")
        for e in out["errors"]:
            print(f"    - {e['clause_id']}: {e['error']}")
    print(
        f"  所要時間: 合計 {s['elapsed_seconds_total']:.0f}秒／"
        f"1件あたり {s['elapsed_seconds_per_item']:.0f}秒"
        f"（LLM 呼び出し {s['llm_calls_total']}回）"
    )
    wrong = [i for i in out["items"] if i["verdict"] != "correct"]
    if wrong:
        print("\n== 誤り(1件ずつ) ==")
        for i in wrong:
            mark = "すり抜け" if i["slipped"] else ("重大" if i["severe"] else "軽〜中")
            trace = i["trace"] or {}
            run = trace.get("run") or {}
            verb = trace.get("verb_check") or {}
            print(
                f"  [{i['verdict_label']}／{mark}] {i['clause_id']}: "
                f"正解 {i['expected']['normalized']}"
                f"({i['expected']['kind']}, is_new={i['expected']['is_new']}) → "
                f"出力 {i['actual']['normalized']}"
                f"({i['actual']['kind']}, is_new={i['actual']['is_new']})"
            )
            print(f"      ゲート: {i['gates']}")
            print(f"      理由: {run.get('reason', '')}")
            if verb:
                print(
                    f"      動詞の一致: {verb.get('core_verb')!r} vs "
                    f"{verb.get('compared')} → {verb.get('matched')}"
                    f"（{verb.get('reason')}）"
                )


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
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out = run(
        args.gold,
        args.registry,
        use_oracle=args.use_oracle,
        limit=args.limit,
        partial_path=None if args.use_oracle else out_dir / "partial.json",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "result_oracle.json" if args.use_oracle else "result.json"
    path = out_dir / name
    path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n結果: {path}")
    # P9: 合格の条件は「ゲートをすり抜けた重大な誤りが0件」
    return 0 if out["summary"]["slipped"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
