"""4点セット同時生成(スキーマ配線ジェネレータ)。

DMN XML／バインディング設定JSON／ルーティング表JSON／Scope・Decision登録簿の4点セットは、
同一のIR(`ClauseIR`)から**1回の呼び出しで同時に**生成する。この4点を別々の関数・別々の
タイミングで生成することはしない(reference/gijinka-extraction-pipeline/docs/ir_schema.md
の設計方針を踏襲)。

必須のクロス検証: 登録簿/新規決定表が要求するフラットfactキー(ドット参照=既存決定の出力を
除いたもの)は、バインディング設定側のfactキー集合と完全一致していなければならない。
不一致を検出したら生成を失敗させる。

## reference/ からの踏襲と調整

- `generate_wiring_bundle()`のクロス検証ロジック・登録簿エントリ構造はreference側の
  `stage5_schema_wiring_generator.py`をほぼそのまま踏襲
- **調整点**: reference側はDMN XMLを「既存ファイルへのパッチ(差分)」として出力していたが、
  本リポジトリでは③の初回スコープが「独立した単純な条文1件」であり、追加先となる既存DMN
  ファイルが存在しない。そのため`render_dmn_xml()`で**完結した新規DMNファイルを生成する**
  経路を追加した。既存DMNへの行追加(レベル2)は次スコープで扱う。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .ir_schema import ClauseIR, NewDecisionTableDraft, RuleRowDraft

DMN_NS = "https://www.omg.org/spec/DMN/20240513/MODEL/"
DMNDI_NS = "https://www.omg.org/spec/DMN/20230324/DMNDI/"
DC_NS = "http://www.omg.org/spec/DMN/20180521/DC/"
FEEL_NS = "https://www.omg.org/spec/DMN/20240513/FEEL/"
KIE_NS = "https://kie.org/dmn/extensions/1.0"
DI_NS = "http://www.omg.org/spec/DMN/20180521/DI/"


class SchemaWiringError(Exception):
    pass


@dataclass
class WiringBundle:
    """4点セット。同一IRから同時生成される。"""

    dmn_xml: str
    bindings: list[dict] = field(default_factory=list)
    routing_table: dict = field(default_factory=dict)
    registry: dict = field(default_factory=dict)

    def write_all(self, out_dir: str | Path, basename: str) -> dict[str, Path]:
        """4点セットをファイルとして書き出す。戻り値は種別->パス。"""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "dmn": out / f"{basename}.dmn",
            "bindings": out / f"{basename}_bindings.json",
            "routing_table": out / f"{basename}_routing.json",
            "registry": out / f"{basename}_registry.json",
        }
        paths["dmn"].write_text(self.dmn_xml, encoding="utf-8")
        for key in ("bindings", "routing_table", "registry"):
            payload = getattr(self, key)
            paths[key].write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return paths


def _is_dot_reference(key: str) -> bool:
    return "." in key


def _flat_fact_keys(ir: ClauseIR) -> set[str]:
    return {k for k in ir.all_fact_keys() if not _is_dot_reference(k)}


def _derive_route(ir: ClauseIR) -> dict:
    if ir.new_decision_tables:
        input_columns = ir.new_decision_tables[-1].input_columns
    else:
        input_columns = sorted({k for row in ir.rule_row_additions for k in row.conditions.keys()})
    return {
        "actor_action": [{"actor": ir.actor, "action": ir.action.normalized}],
        "input_columns": input_columns,
    }


def _rule_row_to_registry_entry(row: RuleRowDraft, provenance: dict) -> dict:
    entry = {
        "row_id": row.row_id,
        "decision_table_id": row.decision_table_id,
        "position": row.position,
        "conditions": dict(row.conditions),
        "provenance": provenance,
        "history": [],
    }
    if set(row.outputs.keys()) == {"decision", "reason_code"}:
        entry["decision"] = row.outputs["decision"]
        entry["reason_code"] = row.outputs["reason_code"]
    else:
        entry["outputs"] = dict(row.outputs)
    return entry


def _uid(prefix: str) -> str:
    return f"_{prefix}_{uuid.uuid4().hex[:12].upper()}"


def _feel_literal(value: str) -> str:
    """決定表のセル値をFEEL式テキストに変換する。

    "-"(任意にマッチ)はそのまま、それ以外は文字列リテラルとして引用符で囲む。
    複数値は '"a", "b"' 形式(FEELのリスト形式)で与えられている前提で素通しする。
    """
    v = value.strip()
    if v == "-":
        return "-"
    if v.startswith('"'):
        return v
    return f'"{v}"'


def render_dmn_xml(
    ir: ClauseIR,
    *,
    definitions_id: str,
    definitions_name: str,
    namespace: str,
) -> str:
    """IRの新規決定表から、完結した1つのDMNファイルのXMLを生成する。

    reference側のDMN(aoi_transmission.dmn、Kogito実機検証済み)の構造に倣う:
    decision > variable / informationRequirement / authorityRequirement / decisionTable、
    末尾にinputData群とknowledgeSource群を置く。
    """
    ET.register_namespace("", DMN_NS)
    ET.register_namespace("dmndi", DMNDI_NS)
    ET.register_namespace("dc", DC_NS)
    ET.register_namespace("feel", FEEL_NS)
    ET.register_namespace("kie", KIE_NS)
    ET.register_namespace("di", DI_NS)

    definitions = ET.Element(
        f"{{{DMN_NS}}}definitions",
        {"id": definitions_id, "name": definitions_name, "namespace": namespace},
    )

    all_inputs: list[str] = []
    all_knowledge_sources: list[str] = []

    for table in ir.new_decision_tables:
        decision = ET.SubElement(
            definitions,
            f"{{{DMN_NS}}}decision",
            {"id": table.decision_id, "name": table.decision_name},
        )
        ET.SubElement(
            decision,
            f"{{{DMN_NS}}}variable",
            {"id": f"var_{table.decision_id}", "name": table.decision_name, "typeRef": "Any"},
        )
        # informationRequirement: 計算に使う入力(inputData / 他decisionの出力)
        for col in table.input_columns:
            if _is_dot_reference(col):
                # 他decisionの出力を参照する場合
                dep_decision = col.split(".", 1)[0]
                req = ET.SubElement(
                    decision, f"{{{DMN_NS}}}informationRequirement", {"id": _uid("ir")}
                )
                ET.SubElement(req, f"{{{DMN_NS}}}requiredDecision", {"href": f"#{dep_decision}"})
            else:
                req = ET.SubElement(
                    decision, f"{{{DMN_NS}}}informationRequirement", {"id": _uid("ir")}
                )
                ET.SubElement(req, f"{{{DMN_NS}}}requiredInput", {"href": f"#id_{col}"})
                if col not in all_inputs:
                    all_inputs.append(col)
        # authorityRequirement: 来歴専用(計算には使わない)。
        # DMN標準ではinputDataに付けられないため、decisionに紐づける。
        for ks in table.knowledge_sources:
            auth = ET.SubElement(
                decision, f"{{{DMN_NS}}}authorityRequirement", {"id": _uid("auth")}
            )
            ET.SubElement(auth, f"{{{DMN_NS}}}requiredAuthority", {"href": f"#{ks}"})
            if ks not in all_knowledge_sources:
                all_knowledge_sources.append(ks)

        dt = ET.SubElement(
            decision,
            f"{{{DMN_NS}}}decisionTable",
            {"id": table.decision_table_id, "hitPolicy": "FIRST"},
        )
        for col in table.input_columns:
            inp = ET.SubElement(
                dt, f"{{{DMN_NS}}}input", {"id": f"in_{col.replace('.', '_')}", "label": col}
            )
            expr = ET.SubElement(
                inp,
                f"{{{DMN_NS}}}inputExpression",
                {"id": _uid("ie"), "typeRef": "string"},
            )
            ET.SubElement(expr, f"{{{DMN_NS}}}text").text = col

        output_names: list[str] = []
        for row in table.rule_rows:
            for name in row.outputs.keys():
                if name not in output_names:
                    output_names.append(name)
        for name in output_names:
            ET.SubElement(
                dt,
                f"{{{DMN_NS}}}output",
                {"id": f"out_{name}", "label": name, "name": name, "typeRef": "string"},
            )

        for row in sorted(table.rule_rows, key=lambda r: r.position):
            rule = ET.SubElement(dt, f"{{{DMN_NS}}}rule", {"id": row.row_id})
            for col in table.input_columns:
                entry = ET.SubElement(rule, f"{{{DMN_NS}}}inputEntry", {"id": _uid("in")})
                ET.SubElement(entry, f"{{{DMN_NS}}}text").text = _feel_literal(
                    row.conditions.get(col, "-")
                )
            for name in output_names:
                entry = ET.SubElement(rule, f"{{{DMN_NS}}}outputEntry", {"id": _uid("out")})
                ET.SubElement(entry, f"{{{DMN_NS}}}text").text = _feel_literal(
                    row.outputs.get(name, "-")
                )

    for col in all_inputs:
        inp = ET.SubElement(
            definitions, f"{{{DMN_NS}}}inputData", {"id": f"id_{col}", "name": col}
        )
        ET.SubElement(
            inp, f"{{{DMN_NS}}}variable", {"id": f"var_id_{col}", "name": col, "typeRef": "string"}
        )
    for ks in all_knowledge_sources:
        ET.SubElement(
            definitions, f"{{{DMN_NS}}}knowledgeSource", {"id": ks, "name": ks}
        )

    ET.indent(definitions, space="  ")
    xml_body = ET.tostring(definitions, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8" ?>\n{xml_body}\n'


def generate_wiring_bundle(
    ir: ClauseIR,
    *,
    existing_binding_fact_keys: set[str],
    new_bindings: list[dict] | None = None,
    definitions_id: str,
    definitions_name: str,
    namespace: str,
) -> WiringBundle:
    """IRを唯一の入力として4点セットを1回の呼び出しで生成する。

    `new_bindings`: このIRが要求する既存にない新規factのバインディング設定。
    """
    _enforce_no_compound_in_primitive(ir)

    new_bindings = new_bindings or []
    new_binding_keys = {b["variable"] for b in new_bindings}

    required_flat_keys = _flat_fact_keys(ir)
    available_keys = existing_binding_fact_keys | new_binding_keys
    missing = required_flat_keys - available_keys
    if missing:
        raise SchemaWiringError(
            f"クロス検証失敗: 登録簿/新規決定表が要求するfactキー {sorted(missing)} に対応する"
            f"バインディング設定が存在しません(既存keys={sorted(existing_binding_fact_keys)}, "
            f"新規keys={sorted(new_binding_keys)})"
        )
    unused = new_binding_keys - required_flat_keys
    if unused:
        raise SchemaWiringError(
            f"クロス検証失敗: バインディング設定に、どの決定表からも参照されないfactキー "
            f"{sorted(unused)} が含まれています"
        )

    provenance = {
        "origin": ir.provenance.origin,
        "clause_ref": ir.provenance.clause_ref,
        "registered_by": ir.provenance.registered_by,
        "registered_at": ir.provenance.registered_at,
    }

    registry_rows = [_rule_row_to_registry_entry(row, provenance) for row in ir.rule_row_additions]
    decision_tables_meta = []
    for table in ir.new_decision_tables:
        decision_tables_meta.append(
            {
                "decision_table_id": table.decision_table_id,
                "decision_id": table.decision_id,
                "decision_name": table.decision_name,
                "hit_policy": "FIRST",
                "input_columns": list(table.input_columns),
                "depends_on_decisions": list(table.depends_on_decisions),
                "depends_on_inputs": list(table.depends_on_inputs),
                "knowledge_sources": list(table.knowledge_sources),
                "provenance": provenance,
            }
        )
        for row in table.rule_rows:
            registry_rows.append(_rule_row_to_registry_entry(row, provenance))

    dmn_xml = render_dmn_xml(
        ir,
        definitions_id=definitions_id,
        definitions_name=definitions_name,
        namespace=namespace,
    )

    routing_table = {"routes": [_derive_route(ir)]}

    # 層1: 決定表メタregistry、層2: ルール行台帳
    registry = {
        "decision_tables": decision_tables_meta,
        "rule_rows": registry_rows,
        "review": {
            "human_review_status": ir.human_review_status,
            "should_review": ir.review_gates.should_review,
            "gate_reasons": ir.review_gates.reasons(),
        },
    }

    return WiringBundle(
        dmn_xml=dmn_xml,
        bindings=new_bindings,
        routing_table=routing_table,
        registry=registry,
    )


def _enforce_no_compound_in_primitive(ir: ClauseIR) -> None:
    """複合fact禁止原則: 一致モジュール側でANDを組む案は採らない。"""
    if ir.compound_fact_application.option_taken == "rejected_compound_in_primitive":
        raise SchemaWiringError(
            "複合fact禁止原則違反: 一致プリミティブ側で複合判定(AND)を組む案は採用できません。"
            "複合判定は決定表側の行として表現してください。"
            f"(rationale: {ir.compound_fact_application.rationale})"
        )
