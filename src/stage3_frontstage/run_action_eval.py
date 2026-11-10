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
- **ゲートの誤報**: 正しい結果なのに立った**疑いの**ゲートの件数(人の負担の目安)
- surface の3条件(条文の部分文字列・様相の語を含まない・正解の中心の動詞を含む)

## v0.5(第33回)で足した指標(5.1節・P21・P23)

ゲートを2種類に分ける(P21。一覧は `ir_schema.SUSPICION_GATES` と
`DESIGN_CHECK_GATES`)。

| 種類 | ゲート | 誤報に数えるか |
| --- | --- | --- |
| 疑い | surface_unverified・surface_ambiguous・verb_mismatch・possible_duplicate・name_rule | 数える |
| 設計上の確認 | new_action_unapproved・action_abstract_selected | 数えない |

- **人に回る割合**: ゲートが1本以上立った件数。「設計上の確認だけ」と「疑いのゲート
  あり」に分ける。合格の条件「すり抜け0件」は全件にゲートを立てても満たせるので、
  人の負担を合格の条件と並べて見る
- **疑いのゲートの適合率**: 疑いのゲートが立った件のうち、本当に誤りだった割合。
  ゲートごとにも出す
- **新規候補の段の一致率**: 正解が新規で、新規候補を出した項目のうち、`kind` が
  正解と一致した割合(P23)
- **新規候補の一覧**: 名前・定義・段・親の案・根拠の語句。名前と定義に条件が
  入っているかは**人が判定する**(P23)。プログラムは一覧を出すだけである

## 複数の行為を含む条文(P25)

1文に複数の行為がある条文は、`expected` に代表の正解を書き、残りを
`expected_alternatives` に並べる。**どれか1つと一致すれば正しい**とし、誤りの分類は
出力に最も近い正解(親子関係にあるもの)と比べて決める。教師ありでは並べた正解の
Action をすべて台帳に足す。`action_surface_ambiguous` が立つのは設計どおりの動きで、
誤報に数えない(正解が1つの項目で立った場合は誤報)。

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

from src.stage3_generate.ir_schema import (
    DESIGN_CHECK_GATES,
    SUSPICION_GATES,
)
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


def expectations(item: dict) -> list[dict]:
    """その項目の正解の並びを返す(P25)。

    1文に複数の行為がある条文では、`expected`(代表)に `expected_alternatives` を
    足したものが正解の並びになる。並びでない項目は1件のリストになる。
    """
    return [item["expected"], *item.get("expected_alternatives", [])]


def _matches(expected: dict, chosen: str, is_new: bool) -> bool:
    """出力が1つの正解と一致するか。

    新規にした場合、名前は文字列で比べない(名前の付け方には幅があり、人が承認して
    決める。5.1節)。既存を選んだ場合は Action 名で比べる。
    """
    if is_new:
        return bool(expected["is_new"])
    return (not expected["is_new"]) and chosen == expected["normalized"]


def nearest_expectation(
    options: list[dict], chosen: str, ledger: ActionLedger
) -> dict:
    """誤りの分類に使う「出力に最も近い正解」を選ぶ(P25)。

    親子関係にある正解(同じ系統)を優先し、同じ順位なら並びの先の方を採る。並びで
    ない項目では `expected` そのものになる。
    """
    if len(options) == 1:
        return options[0]
    rank = {"same": 0, "descendant": 1, "ancestor": 1}
    ranked = sorted(
        ((rank.get(ledger.relation(exp["normalized"], chosen), 2), i, exp)
         for i, exp in enumerate(options)),
        key=lambda t: (t[0], t[1]),
    )
    return ranked[0][2]


@dataclass
class ItemResult:
    order: int
    clause_id: str
    group: str
    expected: dict
    # P25: 正解の並び(並びでなければ expected 1件だけ)と、判定に使った正解
    expectations: list[dict]
    matched: dict
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
    def suspicion_gates(self) -> list[str]:
        """立った「疑い」のゲート(P21)。"""
        return [g for g in self.gates if g in SUSPICION_GATES]

    @property
    def design_check_gates(self) -> list[str]:
        """立った「設計上の確認」のゲート(P21)。誤報には数えない。"""
        return [g for g in self.gates if g in DESIGN_CHECK_GATES]

    @property
    def false_alarm_gates(self) -> list[str]:
        """正しい結果なのに立った**疑いの**ゲート(5.1節のゲートの誤報。P21)。

        設計上の確認(new_action_unapproved・action_abstract_selected)は、結果が
        正しくても人が見ることにしているゲートなので誤報に数えない(P21・P22)。

        `action_surface_ambiguous` は、正解が並びの項目(1文に複数の行為。P25)では
        設計どおりの動きなので、そのときだけ誤報から外す。
        """
        if not self.correct:
            return []
        gates = self.suspicion_gates
        if self.is_alternatives:
            gates = [g for g in gates if g != "action_surface_ambiguous"]
        return gates

    @property
    def is_alternatives(self) -> bool:
        """正解が並びの項目か(1文に複数の行為。P25)。"""
        return len(self.expectations) > 1

    @property
    def routed_to_human(self) -> bool:
        """ゲートが1本以上立ち、人のレビューに回った(P21)。"""
        return bool(self.gates)

    @property
    def design_check_only(self) -> bool:
        """人に回ったが、立ったのは設計上の確認だけ(P21)。"""
        return bool(self.gates) and not self.suspicion_gates

    @property
    def surface_ambiguous(self) -> bool:
        """行為の語句の候補が2つ出た(P17・P25)。"""
        return "action_surface_ambiguous" in self.gates

    @property
    def new_action_proposal(self) -> Optional[dict]:
        """新規候補の提案(出していなければ None)。"""
        if self.result is None or self.result.trace.new_action_proposal is None:
            return None
        return self.result.trace.new_action_proposal.to_json()

    @property
    def new_action_kind_matches(self) -> Optional[bool]:
        """新規候補の段が正解と一致したか(P23)。

        正解が新規で、かつ新規候補を出した項目でだけ測る。それ以外は None にして
        分母から外す(既存を選んだ項目に段の一致を問えない)。
        """
        if not self.is_new or not any(e["is_new"] for e in self.expectations):
            return None
        proposal = self.new_action_proposal
        if proposal is None:
            return None
        expected_kinds = {
            e.get("kind", "concrete") for e in self.expectations if e["is_new"]
        }
        return proposal.get("kind") in expected_kinds

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
            "expected_alternatives": [dict(e) for e in self.expectations[1:]],
            "matched_expectation": dict(self.matched),
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
            "suspicion_gates": self.suspicion_gates,
            "design_check_gates": self.design_check_gates,
            "routed_to_human": self.routed_to_human,
            "design_check_only": self.design_check_only,
            "surface_ambiguous": self.surface_ambiguous,
            "new_action_kind_matches": self.new_action_kind_matches,
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
    """1件の結果を4つの誤りに分ける(5.1節 v0.4、P25 で並びに対応)。

    台帳は**正解を足した後**のものを渡す。正解が新規の Action のときも、その段と親が
    台帳に入っていなければ、適用漏れ(子孫を選んだ)と過剰適用(祖先を選んだ)を
    区別できないためである。
    """
    options = expectations(item)
    chosen = result.action.normalized
    is_new = result.action.is_new
    # P25: 並びのどれか1つと一致すれば正しい
    if any(_matches(exp, chosen, is_new) for exp in options):
        return "correct"
    if is_new:
        # 新規にしたが、どの正解も既存だった
        return "missed"
    # 誤りの分類は、出力に最も近い正解(同じ系統のもの)と比べて決める(P25)
    expected = nearest_expectation(options, chosen, ledger)
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
    options = expectations(item)
    surface_check = check_surface(result.action.surface, item["text"])
    run = result.trace.run
    verdict = classify(item, result, ledger)
    matched = (
        next(
            (
                exp
                for exp in options
                if _matches(exp, result.action.normalized, result.action.is_new)
            ),
            options[0],
        )
        if verdict == "correct"
        else nearest_expectation(options, result.action.normalized, ledger)
    )
    return ItemResult(
        order=item["order"],
        clause_id=item["clause_id"],
        group=item["group"],
        expected=item["expected"],
        expectations=options,
        matched=matched,
        chosen=result.action.normalized,
        chosen_kind=result.chosen_kind,
        is_new=result.action.is_new,
        surface=result.action.surface,
        removed_modality=list(run.candidates[0].check.removed_modality)
        if run.candidates
        else [],
        core_verb=run.candidates[0].core_verb if run.candidates else "",
        verdict=verdict,
        gates=result.gates.gate_names(),
        surface_in_clause=surface_check.in_clause,
        surface_no_modality=not surface_check.modality_found,
        # P25: 並びのどれかの中心の動詞を含んでいればよい
        surface_has_core_verb=any(
            exp["core_verb"] in result.action.surface for exp in options
        ),
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

    正解が並びの項目(1文に複数の行為。P25)では、**並べた正解をすべて**足す。
    """
    actions: list[str] = []
    for expected in expectations(item):
        actions.extend(_teach_one(ledger, item, expected))
    for update in item.get("ledger_updates", []):
        entry = ledger.find(update["name"])
        if entry is None:
            continue
        if "parent" in update:
            entry.parent = update["parent"]
            actions.append(f"親を付けた: {update['name']} → {update['parent']}")
    return "／".join(actions)


def _teach_one(ledger: ActionLedger, item: dict, expected: dict) -> list[str]:
    """正解1件分を台帳に足す。

    定義は、並びの要素が自分の `definition` を持っていればそれを使い、無ければ項目の
    `definition` を使う(並びでない項目は後者だけ)。
    """
    name = expected["normalized"]
    surface = expected["surface"]
    core_verb = expected.get("core_verb", "")
    clause_ref = item["clause_ref"]
    actions: list[str] = []
    if ledger.find(name) is None:
        definition = expected.get("definition") or item.get("definition") or {}
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
    return actions


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
            # P25: 正解が並びの項目では候補を並びの数だけ返す
            # (action_surface_ambiguous が設計どおりに立つことを確かめられる)
            return {
                "candidates": [
                    {
                        "surface": exp["surface"],
                        "core_verb": exp.get("core_verb", ""),
                    }
                    for exp in expectations(item)[:2]
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
        definition = expected.get("definition") or item.get("definition") or {}
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
    """台帳の出発点を作る(P10)。

    - **開発用(split=dev)**: 初期登録の登録簿から読み、正解ファイルの `ledger_seed`
      (1件。あおいの外部送信)と照合する。登録簿が規範で、正解ファイルは写しである
    - **保留用(split=holdout)**: 出発点は「開発用18件の正解をすべて足し終えた状態」
      (12件)で、登録簿には無い。正解ファイルの `ledger_seed` から作り、登録簿の
      エントリがその中にあること(定義まで一致すること)だけを確かめる(第33回4.1節)
    """
    registry = Registry.load(registry_path)
    seed = gold.get("ledger_seed", [])
    if gold.get("_meta", {}).get("split") == "holdout":
        ledger = ActionLedger.from_json(seed)
        by_name = {e["name"]: e for e in seed}
        for entry in registry.action_ledger:
            found = by_name.get(entry["name"])
            if found is None:
                raise SystemExit(
                    f"登録簿の Action {entry['name']!r} が保留用の ledger_seed に"
                    "ありません(出発点は開発用18件を足し終えた台帳のはず)"
                )
            if found["definition"] != entry["definition"]:
                raise SystemExit(
                    f"Action {entry['name']!r} の定義が登録簿と保留用の ledger_seed で"
                    f"違います:\n  登録簿: {entry['definition']!r}\n"
                    f"  正解ファイル: {found['definition']!r}"
                )
        return ledger
    ledger = ActionLedger.from_json(registry.action_ledger)
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


class HoldoutNotApproved(SystemExit):
    """保留用の正解が承認されていないのに評価を流そうとした(4.3節)。"""


def check_holdout_is_approved(gold: dict) -> None:
    """保留用の正解ファイルは、全件が承認済みでなければ評価を流さない(第33回4.3節)。

    誤って未確定の正解で流すと、「結果を見てから直さない」という保留評価の前提が
    崩れ、その条文は二度と保留用に使えない(P20)。開発用(split=dev)には当てない。
    """
    if gold.get("_meta", {}).get("split") != "holdout":
        return
    unapproved = [
        item["clause_id"]
        for item in gold["items"]
        if not (item.get("review") or {}).get("approved_by")
    ]
    if unapproved:
        raise HoldoutNotApproved(
            "保留用の正解が承認されていないので評価を止めました"
            f"（{len(unapproved)}件: {'、'.join(unapproved)}）。"
            "review.approved_by が入るまで流さないこと(第33回 HANDOFF 4.3節・P20)"
        )


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
    # 保留用は全件が承認済みでなければ流さない(4.3節)
    check_holdout_is_approved(gold)
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
    # P21: 疑いのゲートごとの適合率(立った件数と、そのうち本当に誤りだった件数)
    precision: dict[str, dict[str, int]] = {}
    for item_result in results:
        for gate in item_result.gates:
            gate_counts[gate] = gate_counts.get(gate, 0) + 1
        for gate in item_result.false_alarm_gates:
            false_alarm_counts[gate] = false_alarm_counts.get(gate, 0) + 1
        for gate in item_result.suspicion_gates:
            cell = precision.setdefault(gate, {"raised": 0, "really_wrong": 0})
            cell["raised"] += 1
            if not item_result.correct:
                cell["really_wrong"] += 1
    with_suspicion = [r for r in results if r.suspicion_gates]
    kind_judged = [
        r for r in results if r.new_action_kind_matches is not None
    ]
    proposals = [
        {
            "order": r.order,
            "clause_id": r.clause_id,
            "proposal": r.new_action_proposal,
            "expected_kind": [
                e.get("kind", "concrete") for e in r.expectations if e["is_new"]
            ],
            "kind_matches": r.new_action_kind_matches,
        }
        for r in results
        if r.new_action_proposal is not None
    ]
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
        # --- v0.5 で足した指標(P21・P23・5.1節) ---
        # 人に回る割合: ゲートが1本以上立った件数。設計上の確認だけの件と分ける
        "routed_to_human": sum(1 for r in results if r.routed_to_human),
        "routed_design_check_only": sum(1 for r in results if r.design_check_only),
        "routed_with_suspicion": len(with_suspicion),
        "automatic": sum(1 for r in results if not r.routed_to_human),
        # 疑いのゲートの適合率: 立った件のうち本当に誤りだった割合
        "suspicion_precision": {
            "raised": len(with_suspicion),
            "really_wrong": sum(1 for r in with_suspicion if not r.correct),
        },
        "suspicion_precision_by_gate": precision,
        # 新規候補の段の一致率(P23)
        "new_action_kind_total": len(kind_judged),
        "new_action_kind_matches": sum(
            1 for r in kind_judged if r.new_action_kind_matches
        ),
        # 正解が並びの項目で action_surface_ambiguous が立ったか(P25)
        "alternatives_items": sum(1 for r in results if r.is_alternatives),
        "alternatives_ambiguous_raised": sum(
            1 for r in results if r.is_alternatives and r.surface_ambiguous
        ),
        "elapsed_seconds_total": round(elapsed, 1),
        "elapsed_seconds_per_item": round(elapsed / max(len(results), 1), 1),
        "llm_calls_total": sum(r.llm_calls for r in results),
        "errors": len(errors),
    }
    out = {
        "_meta": {
            "gold": str(gold_path),
            # P20: 開発用(dev)か保留用(holdout)か
            "split": gold.get("_meta", {}).get("split", "dev"),
            "registry": str(registry_path),
            "llm": "oracle(偽物)" if use_oracle else f"ollama {llm.model}",  # type: ignore[union-attr]
            "k": 1,
            "temperature": TEMPERATURE,
            "note": "ゲートをすり抜けた重大な誤り(誤統合・適用漏れ)が0件なら合格"
            "(P9・5.3節 v0.4)。結果を見てプロンプトを直して再実行することはしない"
            "(第31回 HANDOFF 5章)",
        },
        "summary": summary,
        # P23: 名前と定義に条件が入っているかは人が判定する。一覧を出すだけである
        "new_action_proposals": proposals,
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
        kind = "疑い" if gate in SUSPICION_GATES else "設計上の確認"
        print(f"    {gate}: {count}件（{kind}）")
    print(
        f"  ゲートの誤報（疑いのゲートだけ。P21）: {s['false_alarm_items']}件 "
        f"{s['false_alarm_gates']}"
    )
    # --- v0.5 の指標(P21・P23) ---
    print(
        f"  人に回る割合: {s['routed_to_human']}/{s['items']}"
        f"（疑いのゲートあり {s['routed_with_suspicion']}／"
        f"設計上の確認だけ {s['routed_design_check_only']}／"
        f"自動で通った {s['automatic']}）"
    )
    prec = s["suspicion_precision"]
    if prec["raised"]:
        print(
            f"  疑いのゲートの適合率: {prec['really_wrong']}/{prec['raised']}"
            f"（{prec['really_wrong'] / prec['raised']:.0%}）"
        )
    else:
        print("  疑いのゲートの適合率: 立った件が無い")
    for gate, cell in sorted(s["suspicion_precision_by_gate"].items()):
        print(f"    {gate}: {cell['really_wrong']}/{cell['raised']}")
    if s["new_action_kind_total"]:
        print(
            f"  新規候補の段の一致: {s['new_action_kind_matches']}/"
            f"{s['new_action_kind_total']}（P23）"
        )
    if s["alternatives_items"]:
        print(
            f"  正解が並びの項目: {s['alternatives_items']}件"
            f"（action_surface_ambiguous が立った "
            f"{s['alternatives_ambiguous_raised']}件。P25）"
        )
    if s.get("errors"):
        print(f"  LLM の呼び出しが失敗した件: {s['errors']}件")
        for e in out["errors"]:
            print(f"    - {e['clause_id']}: {e['error']}")
    print(
        f"  所要時間: 合計 {s['elapsed_seconds_total']:.0f}秒／"
        f"1件あたり {s['elapsed_seconds_per_item']:.0f}秒"
        f"（LLM 呼び出し {s['llm_calls_total']}回）"
    )
    proposals = out.get("new_action_proposals") or []
    if proposals:
        print("\n== 新規候補の一覧（名前・定義に条件が入っているかは人が判定する。P23）==")
        for entry in proposals:
            p = entry["proposal"] or {}
            mark = {True: "一致", False: "**不一致**", None: "―"}[entry["kind_matches"]]
            print(
                f"  [{entry['order']:>2}] {entry['clause_id']}: {p.get('name')!r}"
                f"（段={p.get('kind')}／正解の段={entry['expected_kind']}→{mark}／"
                f"親の案={p.get('parent_suggestion')}）"
            )
            print(f"       定義: {p.get('definition')}")
            print(f"       根拠の語句: {p.get('basis_quotes')}")
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
    # 保留用の結果は開発用と別のファイルに出す(上書きして取り違えないため。P20)
    split = json.loads(Path(args.gold).read_text(encoding="utf-8")).get("_meta", {}).get(
        "split", "dev"
    )
    suffix = "" if split == "dev" else f"_{split}"
    out = run(
        args.gold,
        args.registry,
        use_oracle=args.use_oracle,
        limit=args.limit,
        partial_path=None if args.use_oracle else out_dir / f"partial{suffix}.json",
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"result{suffix}_oracle.json" if args.use_oracle else f"result{suffix}.json"
    path = out_dir / name
    path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n結果: {path}")
    # P9: 合格の条件は「ゲートをすり抜けた重大な誤りが0件」
    return 0 if out["summary"]["slipped"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
