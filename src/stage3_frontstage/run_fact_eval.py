"""事実の抽出の評価(前段設計書10.4節・P40・P42)。

承認済みの正解 IR(`testdata/ir_gold/manifest.json` の順)の `facts` 欄と、前段の第2要素の
出力を**値の単位**で突き合わせる。層3の出発点は初期登録の登録簿で、1件を流したら
**前段の出力ではなく正解の fact を層3に足してから**次へ進む(教師あり。P42)。

## 何を測るか(10.4.2節)

誤りを重さで6つに分け、**重大な誤りがゲートに捕まっているか**を合格の条件にする(P40)。

| 分類 | 意味 | 重さ |
| --- | --- | --- |
| 条件の付け足し | 条文に無い条件を足した。禁止が狭くなり、効かない場面ができる | **重大** |
| 誤った再利用 | 別の列の fact に値を入れた | **重大** |
| 値の取り違え | 同じ条件を、条文と違う値にした | **重大** |
| 条件の落とし | 条文の条件を出さなかった。禁止が広がる(安全側) | 中 |
| 取りこぼし | 既存の fact を使うべきところを使わなかった | 中 |
| fact の分け方の違い | 2つの値を1つの fact にまとめた、またはその逆 | 中 |

- **すり抜け**: 重大な誤りのうち、ゲートが1つも立たなかった件数。**0件が合格の条件**
- 条件の語句の逐語率／既存の fact の再利用(正しい再利用・取りこぼし・誤った再利用)／
  提案の当たり・誤報／人に回る割合／新規候補の primitive の一致

## 突き合わせ方(10.4.1節)

1. 正解と出力の値を、**正規化した文字列**で対応づける(同じ値なら同じ条件と見る。
   同じ値が2つの列に入る条文(第14条)があるので、対応づけは正解の列を優先する)
2. 対応した値ごとに、入った fact が正解と同じ列かを見る(既存の fact なら名前、新規なら
   同じ値の組を持つか)
3. 正解に無い値は「条件の付け足し」、出力に無い値は「条件の落とし」、同じ条件で値が
   違えば「値の取り違え」に数える(P40)

**正解の値の求め方**: `facts.new_facts` の `possible_values` と、`referenced_facts` が
決定表の行で取っている値(「-」以外)、`value_additions` の値を集める(D25 の向き)。
比較型の新しい fact は、値がプリミティブの定義で決まる(D59)ので値としては数えず、
primitive の一致だけを見る。

## 評価から外す値(FU2)

評価の判断を含む条件(「公序良俗に反する」「盗み見可能な環境」など)は、D51 により
正解 IR にしない。出力に出たら**評価から外し、件数だけ数える**(FU2)。判定は語の一覧
(`JUDGMENT_WORDS`)で行う。正解 IR 5件には1件も無い。

## 使い方

    # Ollama で実際に流す(1件あたり LLM 呼び出し1〜3回。k=1)
    .venv/bin/python3 -m src.stage3_frontstage.run_fact_eval

    # LLM を正解を返す偽物に差し替えて、評価の筋だけを確かめる(Ollama 不要)
    .venv/bin/python3 -m src.stage3_frontstage.run_fact_eval --use-oracle
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.stage3_generate.ir_schema import (
    DESIGN_CHECK_GATES,
    SUSPICION_GATES,
    OTHER_VALUE,
    fact_def_from_json,
)
from src.stage3_generate.registry import Registry

from .fact_extractor import (
    TEMPERATURE,
    FactExtractionResult,
    FactExtractor,
    OllamaJsonLlm,
    normalize_value,
)
from .fact_ledger import NO_MATCH, FactEntry, FactLedger

GOLD_DIR = "testdata/ir_gold"
REGISTRY_PATH = "registry/registry.json"
OUT_DIR = "out/fact_eval"

# P40: 重大な誤り(禁止が効かなくなる・別の列で判定する)。すり抜け0件が合格の条件
SEVERE_KINDS = ("condition_added", "wrong_reuse", "value_mistaken")
# P40 改訂【v0.13】: 新しい fact の値で、根拠の語句が正解と同じで同じ列に入っているが
# 書き方だけが違うものは「表記の違い(中)」にする(重大な「値の取り違え」にしない)
MIDDLE_KINDS = (
    "condition_dropped",
    "reuse_missed",
    "grouping_differs",
    "notation_differs",
)
ERROR_LABELS = {
    "condition_added": "条件の付け足し",
    "wrong_reuse": "誤った再利用",
    "value_mistaken": "値の取り違え",
    "condition_dropped": "条件の落とし",
    "reuse_missed": "取りこぼし",
    "grouping_differs": "fact の分け方の違い",
    "notation_differs": "表記の違い",
}
VERDICT_LABELS = {
    "correct": "正しい",
    "severe": "重大な誤りあり",
    "middle": "中の誤りのみ",
}

# FU2: 評価の判断を含む条件の語(D51)。この語を含む値は評価から外し、件数だけ数える。
JUDGMENT_WORDS: tuple[str, ...] = (
    "公序良俗",
    "みだりに",
    "必要かつ相当",
    "相当な範囲",
    "盗み見",
    "著しく",
    "正当な理由",
    "ふさわしく",
    "適切",
)

# 「同じ条件で値が違う」とみなす近さ(値の取り違えと条件の付け足しを分ける)。
VALUE_CLOSE_RATIO = 0.5


def values_are_close(a: str, b: str) -> bool:
    """2つの値が同じ条件を指していそうか(値の取り違えの判定)。"""
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= VALUE_CLOSE_RATIO


def same_condition(
    out_value: str, out_quote: str, gold_value: str, gold_quote: str
) -> bool:
    """出力の値と正解の値が、**同じ条件**を指しているか(10.4.1節)。

    10.4.1節は「条件の語句の重なりで補助する」と定めているので、値の近さだけで
    なく根拠の語句の重なりも見る。同じ条件で値が違えば「値の取り違え」(重大)、
    別の条件なら「条件の付け足し」(重大)である。どちらも重大なので、合格の条件
    (すり抜け0件)は分け方に左右されない。
    """
    if out_quote and gold_quote and (out_quote in gold_quote or gold_quote in out_quote):
        return True
    return values_are_close(out_value, gold_value)


def quotes_overlap(a: str, b: str) -> bool:
    """2つの根拠の語句が、どちらかがもう一方を含むか(10.4.1節)。"""
    return bool(a) and bool(b) and (a in b or b in a)


def has_judgment_word(text: str) -> list[str]:
    """評価の判断を含む条件の語(FU2)。"""
    return [w for w in JUDGMENT_WORDS if w in text]


# --------------------------------------------------------------------------
# 正解の読み込み
# --------------------------------------------------------------------------


@dataclass
class GoldValue:
    """正解の1つの値(列の名前・値・その列が新しいか)。"""

    variable: str
    value: str
    is_new: bool
    quote: str = ""
    primitive: str = "passthrough"

    def to_json(self) -> dict:
        return {
            "variable": self.variable,
            "value": self.value,
            "is_new": self.is_new,
            "quote": self.quote,
            "primitive": self.primitive,
        }


def load_items(gold_dir: str | Path = GOLD_DIR) -> list[dict]:
    """manifest.json の順に正解 IR を読む(D43 と同じ順)。"""
    base = Path(gold_dir)
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    items = []
    for order, entry in enumerate(manifest["order"], start=1):
        gold = json.loads((base / entry["file"]).read_text(encoding="utf-8"))
        items.append({"order": order, "gold": gold, "manifest": entry})
    return items


def gold_values(gold: dict) -> list[GoldValue]:
    """正解 IR の facts 欄と決定表の行から、正解の値を求める(10.4.1節)。"""
    ir = gold["ir"]
    facts = ir["facts"]
    quotes: dict[tuple[str, str], str] = {}
    for entry in gold.get("evidence", {}).get("possible_values", []) or []:
        key = (entry.get("fact", ""), normalize_value(entry.get("value", ""))[0])
        quotes[key] = entry.get("quote", "")
    fact_quotes: dict[str, str] = {}
    for entry in gold.get("evidence", {}).get("fact_mapping", []) or []:
        fact_quotes[entry.get("fact", "")] = entry.get("quote", "")

    out: list[GoldValue] = []
    new_names: set[str] = set()
    for payload in facts.get("new_facts", []):
        variable = payload["variable"]
        new_names.add(variable)
        primitive = payload["primitive"]
        if primitive != "passthrough":
            # 比較型の値はプリミティブの定義で決まる(D59)ので、値としては数えない
            continue
        for value in payload.get("possible_values", []):
            if value == OTHER_VALUE:
                continue
            normalized, _ = normalize_value(value)
            out.append(
                GoldValue(
                    variable=variable,
                    value=normalized,
                    is_new=True,
                    quote=quotes.get((variable, normalized), ""),
                    primitive=primitive,
                )
            )
    referenced = set(facts.get("referenced_facts", []))
    rows = [
        row
        for table in ir.get("new_decision_tables", [])
        for row in table.get("rule_rows", [])
    ] + list(ir.get("rule_row_additions", []))
    for row in rows:
        for variable, value in (row.get("conditions") or {}).items():
            if variable not in referenced or value in ("-", None, ""):
                continue
            normalized, _ = normalize_value(str(value))
            if any(
                g.variable == variable and g.value == normalized for g in out
            ):
                continue
            out.append(
                GoldValue(
                    variable=variable,
                    value=normalized,
                    is_new=False,
                    quote=fact_quotes.get(variable, ""),
                )
            )
    for variable, additions in (facts.get("value_additions") or {}).items():
        for addition in additions:
            normalized, _ = normalize_value(addition["value"])
            out.append(
                GoldValue(
                    variable=variable,
                    value=normalized,
                    is_new=False,
                    quote=fact_quotes.get(variable, ""),
                )
            )
    return out


def gold_new_fact_primitives(gold: dict) -> dict[str, str]:
    return {
        f["variable"]: f["primitive"] for f in gold["ir"]["facts"].get("new_facts", [])
    }


# --------------------------------------------------------------------------
# 出力の読み込み
# --------------------------------------------------------------------------


@dataclass
class OutValue:
    """前段の出力の1つの値。"""

    variable: str
    value: str
    is_new: bool
    quote: str = ""
    primitive: str = "passthrough"

    def to_json(self) -> dict:
        return {
            "variable": self.variable,
            "value": self.value,
            "is_new": self.is_new,
            "quote": self.quote,
            "primitive": self.primitive,
        }


def out_values(result: FactExtractionResult) -> list[OutValue]:
    """前段の出力を値の一覧にする(3a で寄せた値と、新規候補の値)。"""
    quote_of = {c.value: c.quote for c in result.trace.conditions}
    out: list[OutValue] = []
    for match in result.trace.matches:
        if match.chosen is not None:
            out.append(
                OutValue(
                    variable=match.chosen,
                    value=match.value,
                    is_new=False,
                    quote=quote_of.get(match.value, ""),
                )
            )
    for proposal in result.trace.proposals:
        for value in proposal.values:
            out.append(
                OutValue(
                    variable=proposal.variable,
                    value=value,
                    is_new=True,
                    quote=quote_of.get(value, ""),
                    primitive=proposal.primitive,
                )
            )
    return out


# --------------------------------------------------------------------------
# 突き合わせ(10.4.1節)
# --------------------------------------------------------------------------


@dataclass
class ItemResult:
    order: int
    gold_id: str
    clause_ref: str
    clause_text: str
    action: str
    gold: list[GoldValue]
    output: list[OutValue]
    errors: list[dict] = field(default_factory=list)
    matched: list[dict] = field(default_factory=list)
    excluded_values: list[dict] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    conditions: list[dict] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    primitive_checks: list[dict] = field(default_factory=list)
    # 手順3a の記録(P37 改訂の寄せ先と、寄せなかった一致)
    matches: list[dict] = field(default_factory=list)
    reuse_scope_facts: list[str] = field(default_factory=list)
    out_of_scope_hits: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    llm_calls: int = 0

    @property
    def severe_errors(self) -> list[dict]:
        return [e for e in self.errors if e["kind"] in SEVERE_KINDS]

    @property
    def middle_errors(self) -> list[dict]:
        return [e for e in self.errors if e["kind"] in MIDDLE_KINDS]

    @property
    def verdict(self) -> str:
        if self.severe_errors:
            return "severe"
        if self.middle_errors:
            return "middle"
        return "correct"

    @property
    def correct(self) -> bool:
        return self.verdict == "correct"

    @property
    def slipped(self) -> bool:
        """重大な誤りなのにゲートが1つも立たなかった(P40 の合格の条件)。"""
        return bool(self.severe_errors) and not self.gates

    @property
    def slipped_strict(self) -> bool:
        """重大な誤りなのに**疑いのゲート**が1つも立たなかった(参考指標。Q2)。

        `new_fact_unapproved`(設計上の確認)は新しい fact を提案すればほぼ毎件立つ
        ので、それだけで止まっている件は「人が気づける」とは言いにくい。合格の条件は
        P40 のまま(`slipped`)で、この指標は必ず併記する(第50回 Kazuhiro さんの判断)。
        """
        return bool(self.severe_errors) and not self.suspicion_gates

    @property
    def suspicion_gates(self) -> list[str]:
        return [g for g in self.gates if g in SUSPICION_GATES]

    @property
    def design_check_gates(self) -> list[str]:
        return [g for g in self.gates if g in DESIGN_CHECK_GATES]

    @property
    def false_alarm_gates(self) -> list[str]:
        """正しい結果なのに立った疑いのゲート(P21)。"""
        return self.suspicion_gates if self.correct else []

    @property
    def routed_to_human(self) -> bool:
        return bool(self.gates)

    @property
    def verbatim_total(self) -> int:
        return len(self.conditions)

    @property
    def verbatim_ok(self) -> int:
        return sum(1 for c in self.conditions if c["in_clause"])

    def to_json(self) -> dict:
        return {
            "order": self.order,
            "gold_id": self.gold_id,
            "clause_ref": self.clause_ref,
            "action": self.action,
            "verdict": self.verdict,
            "verdict_label": VERDICT_LABELS[self.verdict],
            "slipped": self.slipped,
            "slipped_strict": self.slipped_strict,
            "gold": [g.to_json() for g in self.gold],
            "output": [o.to_json() for o in self.output],
            "matched": [dict(m) for m in self.matched],
            "errors": [dict(e) for e in self.errors],
            "excluded_values": [dict(e) for e in self.excluded_values],
            "gates": list(self.gates),
            "suspicion_gates": self.suspicion_gates,
            "design_check_gates": self.design_check_gates,
            "conditions": [dict(c) for c in self.conditions],
            "suggestions": [dict(s) for s in self.suggestions],
            "proposals": [dict(p) for p in self.proposals],
            "primitive_checks": [dict(p) for p in self.primitive_checks],
            "matches": [dict(m) for m in self.matches],
            "reuse_scope_facts": list(self.reuse_scope_facts),
            "out_of_scope_hits": [dict(h) for h in self.out_of_scope_hits],
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "llm_calls": self.llm_calls,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "ItemResult":
        """保存した結果から読み直す(LLM を回さずに数え直すため。パートB3)。"""
        item = cls(
            order=payload["order"],
            gold_id=payload["gold_id"],
            clause_ref=payload.get("clause_ref", ""),
            clause_text=payload.get("clause_text", ""),
            action=payload.get("action", ""),
            gold=[
                GoldValue(
                    variable=g["variable"],
                    value=g["value"],
                    is_new=g["is_new"],
                    quote=g.get("quote", ""),
                    primitive=g.get("primitive", "passthrough"),
                )
                for g in payload.get("gold", [])
            ],
            output=[
                OutValue(
                    variable=o["variable"],
                    value=o["value"],
                    is_new=o["is_new"],
                    quote=o.get("quote", ""),
                    primitive=o.get("primitive", "passthrough"),
                )
                for o in payload.get("output", [])
            ],
            gates=list(payload.get("gates") or []),
            conditions=[dict(c) for c in payload.get("conditions") or []],
            suggestions=[dict(x) for x in payload.get("suggestions") or []],
            proposals=[dict(x) for x in payload.get("proposals") or []],
            primitive_checks=[dict(x) for x in payload.get("primitive_checks") or []],
            matches=[dict(x) for x in payload.get("matches") or []],
            reuse_scope_facts=list(payload.get("reuse_scope_facts") or []),
            out_of_scope_hits=[dict(x) for x in payload.get("out_of_scope_hits") or []],
            elapsed_seconds=payload.get("elapsed_seconds", 0.0),
            llm_calls=payload.get("llm_calls", 0),
        )
        item.errors = [dict(e) for e in payload.get("errors") or []]
        item.matched = [dict(m) for m in payload.get("matched") or []]
        item.excluded_values = [dict(e) for e in payload.get("excluded_values") or []]
        return item


def classify_values(
    golds: list[GoldValue],
    outs: list[OutValue],
    extracted_values: set[str],
) -> tuple[list[dict], list[dict], list[dict]]:
    """正解の値と出力の値を突き合わせ、誤りを分類する(10.4.1節・P40・P40 改訂)。

    `(errors, matched, excluded_values)` を返す。プログラムの外から呼べる形にして
    あるので、済んだ結果の数え直し(`reclassify`)でも同じ規則を使える。

    **表記の違い(中)の分け方(P40 改訂【v0.13】)**: 出力の値が正解のどの値とも一致
    しないとき、次の3つがすべて成り立てば「表記の違い(中)」とし、1つでも欠ければ
    これまでどおり「値の取り違え(重大)」とする。

    1. 近いと見た正解の値が、**新しい fact の値**である(既存の fact への寄せには
       当てない。P40 改訂の但し書き)
    2. **根拠の語句が重なる**(どちらかがもう一方を含む)。値の近さだけでは足りない
    3. 出力のその列が、**別の正解の列に結びついていない**(列の対応が1対1である)。
       名前は比べない(Q2)。結びつきは値の対応から求める
    """
    errors: list[dict] = []
    matched: list[dict] = []
    excluded: list[dict] = []

    gold_groups: dict[str, set[str]] = {}
    for g in golds:
        gold_groups.setdefault(g.variable, set()).add(g.value)
    out_groups: dict[str, set[str]] = {}
    for o in outs:
        out_groups.setdefault(o.variable, set()).add(o.value)

    # 列の結びつき: 値が完全一致した組から先に求める(表記の違いの条件3)
    bindings: dict[str, set[str]] = {}
    for o in outs:
        for g in golds:
            if g.value == o.value:
                bindings.setdefault(o.variable, set()).add(g.variable)

    used_gold: set[int] = set()

    for o in outs:
        # 値が同じ正解を探す。同じ値が2つの列に入る条文(第14条)があるので、
        # 列の名前が同じものを先に当てる
        candidates = [
            i for i, g in enumerate(golds) if i not in used_gold and g.value == o.value
        ]
        same_name = [i for i in candidates if golds[i].variable == o.variable]
        index = (same_name or candidates or [None])[0]
        if index is None:
            judgment = has_judgment_word(o.value) or has_judgment_word(o.quote)
            if judgment:
                # FU2: 評価の判断を含む条件は評価から外し、件数だけ数える
                excluded.append(
                    {"value": o.value, "fact": o.variable, "words": judgment}
                )
                continue
            near_index = next(
                (
                    i
                    for i, g in enumerate(golds)
                    if i not in used_gold
                    and same_condition(o.value, o.quote, g.value, g.quote)
                ),
                None,
            )
            near = None if near_index is None else golds[near_index]
            if near is not None:
                # 同じ条件を取り違えた1件なので、正解の値はここで使い切る
                # (「条件の落とし」と二重に数えない)
                used_gold.add(near_index)
                others = bindings.get(o.variable, set()) - {near.variable}
                notation = (
                    near.is_new
                    and quotes_overlap(o.quote, near.quote)
                    and not others
                )
                bindings.setdefault(o.variable, set()).add(near.variable)
                if notation:
                    errors.append(
                        {
                            "kind": "notation_differs",
                            "label": ERROR_LABELS["notation_differs"],
                            "fact": o.variable,
                            "value": o.value,
                            "gold_fact": near.variable,
                            "gold_value": near.value,
                            "note": "同じ条件・同じ列で、値の書き方が正解と違う"
                            "(P40 改訂)",
                        }
                    )
                else:
                    errors.append(
                        {
                            "kind": "value_mistaken",
                            "label": ERROR_LABELS["value_mistaken"],
                            "fact": o.variable,
                            "value": o.value,
                            "gold_fact": near.variable,
                            "gold_value": near.value,
                            "note": "同じ条件を、正解と違う値にした",
                        }
                    )
            else:
                errors.append(
                    {
                        "kind": "condition_added",
                        "label": ERROR_LABELS["condition_added"],
                        "fact": o.variable,
                        "value": o.value,
                        "note": "正解に無い条件を足した",
                    }
                )
            continue
        g = golds[index]
        used_gold.add(index)
        matched.append(
            {
                "value": o.value,
                "fact": o.variable,
                "gold_fact": g.variable,
                "gold_is_new": g.is_new,
                "out_is_new": o.is_new,
            }
        )
        if not g.is_new:
            if o.is_new:
                errors.append(
                    {
                        "kind": "reuse_missed",
                        "label": ERROR_LABELS["reuse_missed"],
                        "fact": o.variable,
                        "value": o.value,
                        "gold_fact": g.variable,
                        "note": "正解は既存の fact を使うが、新規候補にした",
                    }
                )
            elif o.variable != g.variable:
                errors.append(
                    {
                        "kind": "wrong_reuse",
                        "label": ERROR_LABELS["wrong_reuse"],
                        "fact": o.variable,
                        "value": o.value,
                        "gold_fact": g.variable,
                        "note": "別の既存の fact に値を入れた",
                    }
                )
        else:
            if not o.is_new:
                errors.append(
                    {
                        "kind": "wrong_reuse",
                        "label": ERROR_LABELS["wrong_reuse"],
                        "fact": o.variable,
                        "value": o.value,
                        "gold_fact": g.variable,
                        "note": "正解は新しい列だが、既存の fact に値を入れた",
                    }
                )
            elif out_groups.get(o.variable) != gold_groups.get(g.variable):
                errors.append(
                    {
                        "kind": "grouping_differs",
                        "label": ERROR_LABELS["grouping_differs"],
                        "fact": o.variable,
                        "value": o.value,
                        "gold_fact": g.variable,
                        "out_group": sorted(out_groups.get(o.variable, set())),
                        "gold_group": sorted(gold_groups.get(g.variable, set())),
                        "note": "値の組が正解の列と違う(まとめた、または分けた)",
                    }
                )

    for i, g in enumerate(golds):
        if i in used_gold:
            continue
        if g.value in extracted_values and not g.is_new:
            errors.append(
                {
                    "kind": "reuse_missed",
                    "label": ERROR_LABELS["reuse_missed"],
                    "fact": None,
                    "value": g.value,
                    "gold_fact": g.variable,
                    "note": "値は抜き出したが、正解の既存の fact を使わなかった",
                }
            )
        else:
            errors.append(
                {
                    "kind": "condition_dropped",
                    "label": ERROR_LABELS["condition_dropped"],
                    "fact": None,
                    "value": g.value,
                    "gold_fact": g.variable,
                    "note": "正解の条件を出さなかった",
                }
            )
    return errors, matched, excluded


def judge(
    order: int, gold: dict, result: FactExtractionResult
) -> ItemResult:
    """正解 IR と出力を突き合わせ、誤りを分類する(10.4.1節・P40)。"""
    ir = gold["ir"]
    golds = gold_values(gold)
    outs = out_values(result)
    item = ItemResult(
        order=order,
        gold_id=gold["gold_id"],
        clause_ref=ir["clause_ref"],
        clause_text=ir["clause_text"],
        action=ir["action"]["normalized"],
        gold=golds,
        output=outs,
        gates=result.gates.gate_names(),
        conditions=[c.to_json() for c in result.trace.conditions],
        suggestions=[s.to_json() for s in result.trace.suggestions],
        proposals=[p.to_json() for p in result.trace.proposals],
        matches=[m.to_json() for m in result.trace.matches],
        reuse_scope_facts=list(result.trace.reuse_scope),
        out_of_scope_hits=[dict(h) for h in result.trace.out_of_scope_hits],
        elapsed_seconds=result.trace.elapsed_seconds,
        llm_calls=result.trace.llm_calls,
    )

    item.errors, item.matched, item.excluded_values = classify_values(
        golds, outs, {c.value for c in result.trace.conditions}
    )

    # 新規候補の primitive の一致(10.4.2節)
    gold_primitives = gold_new_fact_primitives(gold)
    for variable, primitive in gold_primitives.items():
        pair = next(
            (m for m in item.matched if m["gold_fact"] == variable and m["out_is_new"]),
            None,
        )
        if pair is None:
            # 値で対応づかない新しい fact(比較型など)は、同じ名前の候補で見る
            pair_proposal = next(
                (p for p in item.proposals if p["variable"] == variable), None
            )
            if pair_proposal is None:
                continue
            out_primitive = pair_proposal["primitive"]
        else:
            out_primitive = next(
                (
                    p["primitive"]
                    for p in item.proposals
                    if p["variable"] == pair["fact"]
                ),
                "passthrough",
            )
        item.primitive_checks.append(
            {
                "gold_fact": variable,
                "gold_primitive": primitive,
                "out_primitive": out_primitive,
                "matches": out_primitive == primitive,
            }
        )
    return item


# --------------------------------------------------------------------------
# 教師あり(P42)
# --------------------------------------------------------------------------


def teach_wiring(ledger: FactLedger, gold: dict) -> list[str]:
    """正解 IR の層1・層2(決定表と行)を配線に足す(P42・P37 改訂)。

    手順3a の寄せ先は「同じ行為の決定表が使っている fact」なので、教師ありの評価では
    fact だけでなく**どの行為のどの表がその fact を使うか**も足さないと、次の条文で
    寄せ先が分からない。`entry_points` は配線ジェネレータと同じ形で作る
    (起点の判定だけに `{actor, action}` を入れ、中間判定は空。D16)。
    """
    if ledger.wiring is None:
        return []
    ir = gold["ir"]
    log: list[str] = []
    entry_decision = (ir.get("target") or {}).get("entry_decision_id")
    for table in ir.get("new_decision_tables", []) or []:
        is_entry = table.get("decision_id") == entry_decision
        ledger.wiring.add_table(
            {
                "decision_table_id": table.get("decision_table_id"),
                "decision_id": table.get("decision_id"),
                "dmn_model": (ir.get("target") or {}).get("dmn_model"),
                "entry_points": (
                    [{"actor": ir.get("actor"), "action": ir["action"]["normalized"]}]
                    if is_entry
                    else []
                ),
                "input_columns": list(table.get("input_columns") or []),
                "depends_on_decisions": [],
            }
        )
        for row in table.get("rule_rows") or []:
            ledger.wiring.add_row(row)
        log.append(
            f"{table.get('decision_table_id')}（行為「{ir['action']['normalized']}」"
            f"の判定）を層1に足した"
        )
    for row in ir.get("rule_row_additions", []) or []:
        ledger.wiring.add_row(row)
        log.append(f"{row.get('decision_table_id')} に行を足した")
    return log


def teach(ledger: FactLedger, gold: dict) -> list[str]:
    """正解の fact を層3に足してから次へ進む(P42)。足した内容を返す。"""
    log: list[str] = []
    facts = gold["ir"]["facts"]
    clause_ref = gold["ir"]["clause_ref"]
    for payload in facts.get("new_facts", []):
        if ledger.find(payload["variable"]) is not None:
            log.append(f"{payload['variable']}（すでに層3にある）")
            continue
        fact = fact_def_from_json(payload)
        entry = FactEntry.from_fact_def(fact, clause_ref=clause_ref)
        # D45: passthrough の fact は予約語「その他」を必ず持つ(層3の形)
        if entry.is_passthrough and OTHER_VALUE not in entry.possible_values:
            entry.possible_values.append(OTHER_VALUE)
        ledger.add(entry)
        log.append(f"{entry.variable}（{'、'.join(entry.possible_values)}）を層3に足した")
    for variable, additions in (facts.get("value_additions") or {}).items():
        added = ledger.add_values(variable, [a["value"] for a in additions])
        if added:
            log.append(f"{variable} に値 {'、'.join(added)} を足した")
    log.extend(teach_wiring(ledger, gold))
    if not log:
        log.append("新しい fact・値は無い(既存の fact を使う条文)")
    return log


# --------------------------------------------------------------------------
# オラクル(正解を返す偽の LLM)
# --------------------------------------------------------------------------


class OracleFactLlm:
    """正解 IR の facts を返す偽の LLM(`--use-oracle`)。

    評価の筋(教師ありの層3の更新・突き合わせ・ゲート)を Ollama なしで確かめるための
    もので、LLM の性能を測るものではない。
    """

    def __init__(self, items: list[dict]) -> None:
        self.by_text = {i["gold"]["ir"]["clause_text"]: i["gold"] for i in items}
        self.golds = [i["gold"] for i in items]

    def _find(self, prompt: str) -> Optional[dict]:
        for text, gold in self.by_text.items():
            if text in prompt:
                return gold
        return None

    def complete_json(self, prompt: str, *, temperature: float) -> dict:
        gold = self._find(prompt)
        if "条件にしている語句を、条文本文から抜き出して" in prompt:
            assert gold is not None, "OracleFactLlm: プロンプトに条文がありません"
            conditions = []
            for g in gold_values(gold):
                conditions.append(
                    {
                        "quote": g.quote or g.value,
                        "value": g.value,
                        # 種類は評価の対象ではない(10.4.1節)
                        "kind": "状況",
                    }
                )
            return {"conditions": conditions, "rationale": "(正解)"}
        if "すでにある列と同じ列の値" in prompt:
            # 手順3b。正解が既存の fact を使う値は、その名前を提案として返す
            assignments = []
            for g in (v for gold in self.golds for v in gold_values(gold)):
                if f"- {g.value}" in prompt:
                    assignments.append(
                        {
                            "value": g.value,
                            "fact": g.variable if not g.is_new else NO_MATCH,
                            "reason": "(正解)",
                        }
                    )
            return {"assignments": assignments}
        assert gold is not None, "OracleFactLlm: プロンプトに条文がありません"
        facts = []
        for payload in gold["ir"]["facts"].get("new_facts", []):
            values = [
                normalize_value(v)[0]
                for v in payload.get("possible_values", [])
                if v != OTHER_VALUE
            ]
            facts.append(
                {
                    "variable": payload["variable"],
                    "primitive": payload["primitive"],
                    "values": values,
                    "other_value_note": payload.get("other_value_note") or "",
                    "comparison_kind": payload.get("comparison_kind"),
                }
            )
        return {"facts": facts, "rationale": "(正解)"}


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------


def build_summary(
    results: list[ItemResult], elapsed: float, errors: Optional[list[dict]] = None
) -> dict:
    """1件ごとの結果から集計を作る(10.4.2節)。数え直し(`reclassify`)でも使う。"""
    errors = errors or []
    gate_counts: dict[str, int] = {}
    false_alarm_counts: dict[str, int] = {}
    for r in results:
        for gate in r.gates:
            gate_counts[gate] = gate_counts.get(gate, 0) + 1
        for gate in r.false_alarm_gates:
            false_alarm_counts[gate] = false_alarm_counts.get(gate, 0) + 1
    by_kind = {
        kind: sum(1 for r in results for e in r.errors if e["kind"] == kind)
        for kind in ERROR_LABELS
    }
    gold_total = sum(len(r.gold) for r in results)
    reuse_scope = sum(1 for r in results for g in r.gold if not g.is_new)
    reuse_correct = sum(
        1
        for r in results
        for m in r.matched
        if not m["gold_is_new"] and not m["out_is_new"] and m["fact"] == m["gold_fact"]
    )
    # 手順3b の提案は**列の単位**で数える(第50回パートC3)。渡した値の書き方が
    # 正解と違っても、提案した列が正解の既存の列と同じなら当たりとする
    suggestion_rows = [
        {
            "order": r.order,
            "gold_id": r.gold_id,
            "value": s["value"],
            "answer": s["answer"],
            "matched_by": s.get("matched_by", "unrecorded"),
            # その条文で正解が再利用している既存の列(当たりの的)
            "gold_reused_facts": sorted({g.variable for g in r.gold if not g.is_new}),
            # 参考: その値が入る正解の列(値の単位の見方)
            "gold_facts_of_value": [g.variable for g in r.gold if g.value == s["value"]],
            "hit": s["answer"] in {g.variable for g in r.gold if not g.is_new},
        }
        for r in results
        for s in r.suggestions
        if s["answer"] is not None
    ]
    primitive_checks = [p for r in results for p in r.primitive_checks]
    summary = {
        "items": len(results),
        # P40: すり抜け(ゲートが1つも立たなかった重大な誤り)が0件なら合格
        "slipped": sum(1 for r in results if r.slipped),
        # 参考指標(第50回 Q2): 重大な誤りで**疑いの**ゲートが1つも立たなかった件。
        # 合格の条件は P40 のまま(`slipped`)で、こちらは必ず併記する
        "slipped_strict": sum(1 for r in results if r.slipped_strict),
        "slipped_strict_items": [
            r.gold_id for r in results if r.slipped_strict
        ],
        "severe_items": sum(1 for r in results if r.severe_errors),
        "middle_items": sum(1 for r in results if r.middle_errors),
        "correct": sum(1 for r in results if r.correct),
        "by_verdict": {
            key: sum(1 for r in results if r.verdict == key) for key in VERDICT_LABELS
        },
        "errors_by_kind": by_kind,
        "severe_errors": sum(len(r.severe_errors) for r in results),
        "middle_errors": sum(len(r.middle_errors) for r in results),
        # 値の単位の突き合わせ
        "gold_values": gold_total,
        "output_values": sum(len(r.output) for r in results),
        "matched_values": sum(len(r.matched) for r in results),
        # 条件の語句の逐語率(10.4.2節)
        "conditions_total": sum(r.verbatim_total for r in results),
        "conditions_verbatim": sum(r.verbatim_ok for r in results),
        # 既存の fact の再利用
        # P37 改訂: 値は一致したが別の行為の fact だったので寄せなかったもの
        "out_of_scope_matches": sum(len(r.out_of_scope_hits) for r in results),
        "out_of_scope_ancestor_only": sum(
            1 for r in results for h in r.out_of_scope_hits if h.get("ancestor_only")
        ),
        "reuse_scope": reuse_scope,
        "reuse_correct": reuse_correct,
        "reuse_missed": by_kind["reuse_missed"],
        "reuse_wrong": by_kind["wrong_reuse"],
        # 提案の当たり・誤報(P33 と同じ数え方)
        "suggestions": len(suggestion_rows),
        "suggestion_hits": sum(1 for s in suggestion_rows if s["hit"]),
        "suggestion_false_alarms": sum(1 for s in suggestion_rows if not s["hit"]),
        "suggestion_offered": sum(len(r.suggestions) for r in results),
        # Q5: 答えの見出しを渡した値に当てた方法。第49回の結果には記録が無いので
        # "unrecorded" に入る(第50回パートC で入れた記録である)
        "suggestion_matched_by": {
            key: sum(
                1
                for r in results
                for s in r.suggestions
                if s.get("matched_by", "unrecorded") == key
            )
            for key in (
                "exact",
                "contains",
                "no_match_answer",
                "ambiguous",
                "none",
                "unrecorded",
            )
        },
        "suggestion_rows": suggestion_rows,
        # 人に回る割合
        "routed_to_human": sum(1 for r in results if r.routed_to_human),
        "routed_with_suspicion": sum(1 for r in results if r.suspicion_gates),
        "routed_design_check_only": sum(
            1 for r in results if r.gates and not r.suspicion_gates
        ),
        "automatic": sum(1 for r in results if not r.gates),
        "gates": gate_counts,
        "false_alarm_items": sum(1 for r in results if r.false_alarm_gates),
        "false_alarm_gates": false_alarm_counts,
        # 新規候補の primitive の一致
        "primitive_total": len(primitive_checks),
        "primitive_matches": sum(1 for p in primitive_checks if p["matches"]),
        # FU2: 評価から外した値(評価の判断を含む条件)
        "excluded_values": sum(len(r.excluded_values) for r in results),
        "elapsed_seconds_total": round(elapsed, 1),
        "elapsed_seconds_per_item": round(elapsed / max(len(results), 1), 1),
        "llm_calls_total": sum(r.llm_calls for r in results),
        "errors": len(errors),
    }
    return summary


def reclassify(payload: dict, *, design: str = "前段 v0.13") -> dict:
    """済んだ結果を、**LLM を回さずに**今の数え方で数え直す(第50回パートB3)。

    保存してある1件ごとの正解の値・出力の値・根拠の語句・ゲートだけを使うので、
    結果を見てからプロンプトや正解を直すことにはならない(P20)。数え直すのは誤りの
    分類と集計だけで、出力そのものは変えない。
    """
    results = [ItemResult.from_json(item) for item in payload.get("items", [])]
    for item in results:
        extracted = {c.get("value", "") for c in item.conditions}
        item.errors, item.matched, item.excluded_values = classify_values(
            item.gold, item.output, extracted
        )
    elapsed = payload.get("summary", {}).get("elapsed_seconds_total", 0.0)
    summary = build_summary(results, elapsed, payload.get("errors") or [])
    before = payload.get("summary", {})
    out = {
        "_meta": dict(
            payload.get("_meta", {}),
            design=design,
            reclassified_from=before.get("_source", "（同じ回の結果）"),
            note="LLM は回していない。保存した出力をいまの分類の規則で数え直した"
            "だけである(第50回パートB3)。所要時間と LLM の回数は元の実行の値",
        ),
        "summary": summary,
        "summary_before": before,
        "changed": [
            {
                "order": item.order,
                "gold_id": item.gold_id,
                "verdict_before": next(
                    (
                        i.get("verdict")
                        for i in payload.get("items", [])
                        if i["order"] == item.order
                    ),
                    None,
                ),
                "verdict_after": item.verdict,
                "errors_before": [
                    dict(e)
                    for i in payload.get("items", [])
                    if i["order"] == item.order
                    for e in i.get("errors") or []
                ],
                "errors_after": [dict(e) for e in item.errors],
            }
            for item in results
        ],
        "errors": payload.get("errors") or [],
        "items": [item.to_json() for item in results],
        "ledger_log": payload.get("ledger_log") or [],
        "ledger_after": payload.get("ledger_after") or [],
    }
    return out


def run(
    gold_dir: str | Path = GOLD_DIR,
    registry_path: str | Path = REGISTRY_PATH,
    *,
    use_oracle: bool = False,
    limit: Optional[int] = None,
    verbose: bool = True,
    partial_path: Optional[str | Path] = None,
) -> dict:
    """正解 IR を manifest の順に流して集計する。"""
    items = load_items(gold_dir)
    if limit:
        items = items[:limit]
    registry = Registry.load(registry_path)
    # P37 改訂: 寄せ先を「同じ行為の決定表が使う fact」に限るので、層3だけでなく
    # 層1・層2の配線も読む
    ledger = FactLedger.from_registry(registry)
    llm = OracleFactLlm(items) if use_oracle else OllamaJsonLlm()
    extractor = FactExtractor(llm)

    started = time.monotonic()
    results: list[ItemResult] = []
    ledger_log: list[dict] = []
    errors: list[dict] = []
    for item in items:
        gold = item["gold"]
        ir = gold["ir"]
        if verbose:
            print(
                f"[{item['order']}/{len(items)}] {gold['gold_id']} "
                f"(層3 {len(ledger.entries)}件) ...",
                flush=True,
            )
        try:
            result = extractor.extract(
                ir["clause_text"],
                ir["clause_ref"],
                ledger,
                action_name=ir["action"]["normalized"],
                action_surface=ir["action"]["surface"],
                context=gold.get("context"),
            )
        except Exception as exc:  # LLM の失敗で評価全体を止めない
            errors.append(
                {
                    "order": item["order"],
                    "gold_id": gold["gold_id"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            if verbose:
                print(f"      [ERROR] {type(exc).__name__}: {exc}", flush=True)
            ledger_log.append({"order": item["order"], "taught": teach(ledger, gold)})
            continue
        judged = judge(item["order"], gold, result)
        results.append(judged)
        taught = teach(ledger, gold)
        ledger_log.append({"order": item["order"], "taught": taught})
        if verbose:
            mark = (
                "OK"
                if judged.correct
                else ("すり抜け" if judged.slipped else "NG")
            )
            print(
                f"      [{mark}] {VERDICT_LABELS[judged.verdict]}"
                f"（重大{len(judged.severe_errors)}／中{len(judged.middle_errors)}）"
                f" 値{len(judged.output)}件 ゲート={judged.gates}"
                f" {judged.elapsed_seconds:.0f}秒／{judged.llm_calls}回",
                flush=True,
            )
            for err in judged.errors:
                print(
                    f"        - {err['label']}: 値 {err['value']!r}"
                    f"（出力の列 {err.get('fact')}／正解の列 {err.get('gold_fact')}）",
                    flush=True,
                )
            for line in taught:
                print(f"      {line}", flush=True)
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
    summary = build_summary(results, elapsed, errors)
    out = {
        "_meta": {
            "gold_dir": str(gold_dir),
            "registry": str(registry_path),
            "llm": "oracle(偽物)" if use_oracle else f"ollama {llm.model}",  # type: ignore[union-attr]
            "k": 1,
            "temperature": TEMPERATURE,
            "design": "前段 v0.13（第2要素「事実の抽出」。P34〜P42。"
            "P37 改訂＝寄せ先は同じ行為の fact／P40 改訂＝表記の違いは中）",
            "note": "合格の条件は「ゲートをすり抜けた重大な誤りが0件」(P40・10.4.2節)。"
            "参考指標 slipped_strict（重大な誤りで疑いのゲートが1つも立たなかった件）"
            "を併記する。sources は評価しない(P39)。fact の名前は文字列で比べない(Q2)",
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
        f"  すり抜け(重大な誤りでゲート無し): {s['slipped']}件"
        f" → {'合格' if s['slipped'] == 0 else '不合格'}（P40）"
    )
    print(
        f"  [参考] 重大な誤りで疑いのゲート無し: {s['slipped_strict']}件"
        f" {s['slipped_strict_items']}"
    )
    print(
        f"  正しい: {s['correct']}/{s['items']}"
        f"（重大な誤りを含む件 {s['severe_items']}／中の誤りを含む件 {s['middle_items']}"
        f"／中の誤りだけの件 {s['by_verdict'].get('middle', 0)}）"
    )
    print("  誤りの内訳（値の単位）:")
    for kind, label in ERROR_LABELS.items():
        weight = "重大" if kind in SEVERE_KINDS else "中"
        print(f"    {label}（{weight}）: {s['errors_by_kind'][kind]}件")
    print(
        f"  値の突き合わせ: 正解 {s['gold_values']}件／出力 {s['output_values']}件／"
        f"対応 {s['matched_values']}件"
    )
    print(
        f"  条件の語句の逐語率: {s['conditions_verbatim']}/{s['conditions_total']}"
    )
    print(
        f"  既存の fact の再利用: 正しい {s['reuse_correct']}/{s['reuse_scope']}"
        f"（取りこぼし {s['reuse_missed']}／誤った再利用 {s['reuse_wrong']}）"
    )
    print(
        f"  手順3b の提案: {s['suggestions']}件／渡した値 {s['suggestion_offered']}件"
        f"（当たり {s['suggestion_hits']}／誤報 {s['suggestion_false_alarms']}。列の単位）"
    )
    print(f"  手順3b の答えの照合: {s['suggestion_matched_by']}")
    print(
        f"  別の行為の fact と値が一致(寄せずに提案へ。P37 改訂): "
        f"{s['out_of_scope_matches']}件"
        f"（うち祖先の行為だけ {s['out_of_scope_ancestor_only']}件）"
    )
    print(
        f"  人に回る割合: {s['routed_to_human']}/{s['items']}"
        f"（疑いあり {s['routed_with_suspicion']}／設計上の確認だけ "
        f"{s['routed_design_check_only']}／自動 {s['automatic']}）"
    )
    print(f"  ゲート: {s['gates']}")
    print(
        f"  疑いのゲートの誤報: {s['false_alarm_items']}件 {s['false_alarm_gates']}"
    )
    print(
        f"  新規候補の primitive の一致: {s['primitive_matches']}/{s['primitive_total']}"
    )
    print(f"  評価から外した値(FU2): {s['excluded_values']}件")
    print(
        f"  所要: {s['elapsed_seconds_total']}秒"
        f"（1件あたり {s['elapsed_seconds_per_item']}秒／LLM {s['llm_calls_total']}回）"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold-dir", default=GOLD_DIR)
    parser.add_argument("--registry", default=REGISTRY_PATH)
    parser.add_argument("--out", default=OUT_DIR)
    parser.add_argument(
        "--use-oracle",
        action="store_true",
        help="LLM を正解を返す偽物に差し替える(Ollama 不要。評価の筋の確認用)",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--reclassify",
        default=None,
        metavar="RESULT_JSON",
        help="済んだ結果を LLM なしでいまの数え方で数え直す(第50回パートB3)",
    )
    parser.add_argument(
        "--reclassify-out",
        default=None,
        metavar="PATH",
        help="--reclassify の書き出し先(既定は入力と同じ場所に _reclassified を付ける)",
    )
    args = parser.parse_args(argv)

    if args.reclassify:
        src = Path(args.reclassify)
        payload = json.loads(src.read_text(encoding="utf-8"))
        out = reclassify(payload)
        dest = (
            Path(args.reclassify_out)
            if args.reclassify_out
            else src.with_name(src.stem + "_reclassified" + src.suffix)
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print_summary(out)
        print(f"\n数え直した結果: {dest}")
        return 0

    out_dir = Path(args.out)
    out = run(
        args.gold_dir,
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
    # P40: 合格の条件は「ゲートをすり抜けた重大な誤りが0件」
    return 0 if out["summary"]["slipped"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
