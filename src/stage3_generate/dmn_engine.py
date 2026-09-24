"""
軽量DMN検証エンジン(自作)。

OMG DMN XMLを解析し、FEELの簡易サブセット(ワイルドカード"-"、文字列リテラル一致、
カンマ区切りのin-listリテラル、"決定名.出力名"のドット参照)を評価する。
Kogito実機のdmnContextネスト仕様(多出力の決定表結果は決定の"名前"の下にネストされる)
に合わせて、依存関係解決時のコンテキストを構築する。

対応するDMN名前空間は固定しない(root要素のタグから自動検出する)。旧DMN
(https://www.omg.org/spec/DMN/20191111/MODEL/)・新DMN
(https://www.omg.org/spec/DMN/20240513/MODEL/)のどちらでも動作する。

## 出所

`reference/gijinka-extraction-pipeline/src/dmn_engine.py` をそのまま移植したもの
(ロジックの変更なし)。reference側ではこのエンジンでの検証結果とApache KIE(Kogito)
実機での評価結果が18/18一致することを確認済みであり、本リポジトリでも生成した
DMN XMLの一次検証に用いる。Kogito実機での最終確認は別タスクとする。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


class DmnEvalError(Exception):
    pass


@dataclass
class DecisionTableInput:
    id: str
    label: str
    expression_text: str


@dataclass
class DecisionTableOutput:
    id: str
    name: str
    label: str


@dataclass
class DecisionTableRule:
    id: str
    input_entries: list[str]
    output_entries: list[str]


@dataclass
class DecisionTable:
    id: str
    hit_policy: str
    inputs: list[DecisionTableInput] = field(default_factory=list)
    outputs: list[DecisionTableOutput] = field(default_factory=list)
    rules: list[DecisionTableRule] = field(default_factory=list)


@dataclass
class Decision:
    id: str
    name: str
    required_input_ids: list[str] = field(default_factory=list)
    required_decision_ids: list[str] = field(default_factory=list)
    decision_table: DecisionTable | None = None


def _local(tag: str) -> str:
    """'{ns}local' -> 'local'"""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _strip_literal(text: str) -> str:
    text = text.strip()
    m = re.fullmatch(r'"([^"]*)"', text)
    if m:
        return m.group(1)
    return text


def _extract_literals(text: str) -> list[str]:
    return re.findall(r'"([^"]*)"', text)


class DmnModel:
    def __init__(self, path: str):
        self.path = path
        tree = ET.parse(path)
        root = tree.getroot()
        ns_uri = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
        self.ns = {"dmn": ns_uri}

        self.input_data_by_id: dict[str, str] = {}
        self.decisions: dict[str, Decision] = {}
        self.decisions_by_name: dict[str, str] = {}

        for el in root:
            local = _local(el.tag)
            if local == "inputData":
                did = el.get("id")
                dname = el.get("name")
                if did and dname:
                    self.input_data_by_id[did] = dname
            elif local == "decision":
                dec = self._parse_decision(el)
                self.decisions[dec.id] = dec
                self.decisions_by_name[dec.name] = dec.id

    def _parse_decision(self, el: ET.Element) -> Decision:
        dec = Decision(id=el.get("id"), name=el.get("name"))
        for child in el:
            local = _local(child.tag)
            if local == "informationRequirement":
                for req in child:
                    req_local = _local(req.tag)
                    href = req.get("href", "")
                    ref_id = href.lstrip("#")
                    if req_local == "requiredInput":
                        dec.required_input_ids.append(ref_id)
                    elif req_local == "requiredDecision":
                        dec.required_decision_ids.append(ref_id)
            elif local == "decisionTable":
                dec.decision_table = self._parse_decision_table(child)
        return dec

    def _parse_decision_table(self, el: ET.Element) -> DecisionTable:
        dt = DecisionTable(id=el.get("id"), hit_policy=el.get("hitPolicy", "UNIQUE"))
        for child in el:
            local = _local(child.tag)
            if local == "input":
                expr_text = ""
                expr_el = self._find(child, "inputExpression")
                if expr_el is not None:
                    text_el = self._find(expr_el, "text")
                    expr_text = (text_el.text or "").strip() if text_el is not None else ""
                dt.inputs.append(
                    DecisionTableInput(id=child.get("id"), label=child.get("label", ""), expression_text=expr_text)
                )
            elif local == "output":
                dt.outputs.append(
                    DecisionTableOutput(id=child.get("id"), name=child.get("name"), label=child.get("label", ""))
                )
            elif local == "rule":
                input_entries = []
                output_entries = []
                for entry in child:
                    entry_local = _local(entry.tag)
                    text_el = self._find(entry, "text")
                    text = (text_el.text or "").strip() if text_el is not None else ""
                    if entry_local == "inputEntry":
                        input_entries.append(text)
                    elif entry_local == "outputEntry":
                        output_entries.append(text)
                dt.rules.append(DecisionTableRule(id=child.get("id"), input_entries=input_entries, output_entries=output_entries))
        return dt

    def _find(self, el: ET.Element, local_name: str) -> ET.Element | None:
        return el.find(f"dmn:{local_name}", self.ns)

    # ---- 評価 ----

    def evaluate(
        self,
        target_decision_id: str,
        input_data: dict,
        trace: bool = False,
        resolve_dependencies: bool = True,
    ) -> dict:
        if target_decision_id not in self.decisions:
            raise DmnEvalError(f"unknown decision id: {target_decision_id}")

        if not resolve_dependencies:
            dec = self.decisions[target_decision_id]
            result, rule_id = self._eval_decision_table(dec.decision_table, input_data)
            result = dict(result)
            result["_matched_rule"] = rule_id
            if trace:
                print(f"  [trace] {dec.id}({dec.name}) matched_rule={rule_id} -> {result}")
            return result

        cache: dict[str, dict] = {}

        def resolve(dec_id: str) -> dict:
            if dec_id in cache:
                return cache[dec_id]
            dec = self.decisions.get(dec_id)
            if dec is None:
                raise DmnEvalError(f"unknown decision id: {dec_id}")

            ctx = dict(input_data)
            for req_id in dec.required_decision_ids:
                sub_result = resolve(req_id)
                sub_dec = self.decisions[req_id]
                ctx[sub_dec.name] = {k: v for k, v in sub_result.items() if not k.startswith("_")}

            result, rule_id = self._eval_decision_table(dec.decision_table, ctx)
            result = dict(result)
            result["_matched_rule"] = rule_id
            cache[dec_id] = result
            if trace:
                print(f"  [trace] {dec.id}({dec.name}) matched_rule={rule_id} -> {result}")
            return result

        return resolve(target_decision_id)

    def _eval_decision_table(self, dt: DecisionTable, ctx: dict) -> tuple[dict, str]:
        if dt.hit_policy not in ("FIRST", "UNIQUE"):
            raise NotImplementedError(f"hitPolicy '{dt.hit_policy}' is not supported by this subset engine")

        for rule in dt.rules:
            if len(rule.input_entries) != len(dt.inputs):
                raise DmnEvalError(f"rule {rule.id}: input entry count mismatch in {dt.id}")
            matched = True
            for inp, entry_text in zip(dt.inputs, rule.input_entries):
                value = self._resolve_expression(inp.expression_text, ctx)
                if not self._matches(entry_text, value):
                    matched = False
                    break
            if matched:
                out = {outp.name: _strip_literal(entry) for outp, entry in zip(dt.outputs, rule.output_entries)}
                return out, rule.id

        raise DmnEvalError(f"no rule matched in decision table {dt.id}; ctx={ctx}")

    @staticmethod
    def _resolve_expression(text: str, ctx: dict):
        text = text.strip()
        if "." in text:
            head, tail = text.split(".", 1)
            sub = ctx.get(head)
            if isinstance(sub, dict):
                return sub.get(tail)
        return ctx.get(text)

    @staticmethod
    def _matches(entry_text: str, value) -> bool:
        entry_text = entry_text.strip()
        if entry_text == "-":
            return True
        literals = _extract_literals(entry_text)
        if literals:
            return value in literals
        return entry_text == value
