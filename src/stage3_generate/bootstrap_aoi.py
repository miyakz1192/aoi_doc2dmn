"""あおいの送信可否判定 DMN を登録簿へ初期登録する(bootstrap)。

第17回 HANDOFF タスク2。`reference/` 以下にある既存の DMN(本人確認判定・情報分類判定・
送信可否判定)を `registry/dmn/aoi_transmission.dmn` に置き、そこから層1・層2・層3を
機械的に起こす。`provenance.origin` は `bootstrap` とする。

この登録簿は、レベル2(既存の最終判定への行追加)・中間判定への行追加・列の追加といった
「受け入れ経路」のテストの土台になる。

## 何をどこから取るか

| 登録簿の項目 | 出典 |
| --- | --- |
| 層1 decision_id・decision_name・decision_table_id・hit_policy | DMN の decision / decisionTable |
| 層1 input_columns・output_columns | decisionTable の input / output |
| 層1 depends_on_decisions・depends_on_inputs | informationRequirement(宣言順) |
| 層1 knowledge_sources | authorityRequirement |
| 層2 の行 | decisionTable の rule(セルは DMN の表記のまま) |
| 層3 possible_values | 決定表のセルに出てくる値だけ(D4)。DMN にない値は補わない |
| 層3 primitive | 設計書3.5節の一致プリミティブ表(`FACT_PRIMITIVES`) |
| 層1 entry_points | **DMN には無い**(`ENTRY_POINTS`。出典は設計書 D32) |
| 層3 sources | **DMN には無く、補わない**(`SOURCE_GAP_NOTE`・D33) |

DMN に無いものを勝手に埋めないのが原則(設計書1.4・HANDOFF 4節)。埋めずに空にしたもの、
出典を付けて入れたものは、どちらも下の定数のところに理由を書いてある。

## 元の DMN の修正(D34)

元の DMN に不足・矛盾があっても `reference/` の元ファイルは書き換えず、`registry/` の
側で直す。直す内容は `DMN_FIXES` に、内容・理由・承認者とともに1か所にまとめてある
(`ENTRY_POINTS` と同じ考え方で、差し替えられるようにするため)。適用した修正は層1・層2の
`history` に残る。

修正後の登録簿には C5・C11 を当て(`check_registry_cell_values`)、違反があれば止める。
これがないと、矛盾を抱えた値が台帳に入り、あとで正解 IR がその値を参照するたびに
C11 に当たる。

## 実行

    .venv/bin/python3 -m src.stage3_generate.bootstrap_aoi

元の DMN は `tests/fixtures/aoi_transmission_baseline.dmn` にコミットしてある(D42)。
あおいの既存実装側(`reference/`)はリポジトリに入っていないため、そのままでは初期登録も
そのテストも動かせなかった。コピーが古くなっていないかは
`tests/test_bootstrap_fixes.py` が `reference/` のある環境でだけ照合する。

同じ入力なら何度実行しても同じ結果になる(`REGISTERED_AT` を固定しているため)。
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .registry import Registry, dmn_path
from .wiring_generator import (
    DMNDI_NS,
    SchemaWiringError,
    _cell_values,
    _register_namespaces,
    _serialize,
    check_registry_cell_values,
)

DMN_NS = "https://www.omg.org/spec/DMN/20240513/MODEL/"
_NS = {"d": DMN_NS, "dmndi": DMNDI_NS}

# 初期登録の元にする DMN。元は `reference/` にあるが、`reference/` はリポジトリに
# 入っていない(.gitignore 対象)ため、同じ内容を `tests/fixtures/` にコミットして
# こちらを既定の入力にする(D42)。これで、初期登録とそのテストがリポジトリだけで動く。
SOURCE_DMN_DEFAULT = "tests/fixtures/aoi_transmission_baseline.dmn"
# 元ファイル(あおいの既存実装側)。コピーが古くなっていないかの照合にだけ使う。
UPSTREAM_DMN = (
    "reference/gijinka-extraction-pipeline/dmn/aoi_transmission_baseline.dmn"
)
DMN_MODEL = "aoi_transmission"
REGISTRY_PATH_DEFAULT = "registry/registry.json"
DMN_DIR_DEFAULT = "registry/dmn"

# 再実行しても同じ登録簿になるよう、登録時刻は固定値にする。
REGISTERED_AT = "2026-10-23T00:00:00+00:00"
REGISTERED_BY = "bootstrap"
CLAUSE_REF = "あおいの既存DMN(条文由来ではない)"

# fact -> 一致プリミティブ。出典は設計書3.5節の表(「例」の列が、そのまま
# あおいの fact を指している)。DMN XML には primitive は書かれていないため、
# ここだけは設計書を出典として付与する。identifiable の
# identifiability_classifier は、この初期登録分でだけ使える(D23)。
FACT_PRIMITIVES: dict[str, str] = {
    "name_match": "fuzzy_match_name",       # 設計書3.5「氏名の照合」
    "birth_match": "date_match",            # 設計書3.5「生年月日の照合」
    "phone_match": "exact_match",           # 設計書3.5「電話番号の照合」
    "has_medical": "ontology_class_match",  # 設計書3.5「検査結果が病歴に含まれるか」
    "has_pii": "ontology_class_match",      # 同上
    "identifiable": "identifiability_classifier",  # 設計書3.5・D23
    "contact_consent": "passthrough",       # 区分の値をそのまま渡す
}

# 最終判定を呼ぶ actor・action の組(D16)。**DMN XML には actor も action も
# 書かれていない**。D16 の作り直しはこれが無いと成立しないため、次の出典を付けて
# 明示的に付与する(設計書1.4「出典付きで明示的に付与した context」)。
#
#   reference/gijinka-extraction-pipeline/docs/diagrams/sequence_diagram_explained.md:278
#   > `actor_action=("あおい", "口頭伝達")` / `existing_routes={("あおい","外部送信")}`
#
# 「既存のルート」= 初期登録時点で登録簿にある最終判定の呼び出し口なので、
# 送信可否判定の entry_points は ("あおい", "外部送信") になる。
# この付与は第18回の報告事項であり、出典ごと差し替えられるようここに分離してある。
ENTRY_POINTS: dict[str, list[dict[str, str]]] = {
    "dt_transmission": [{"actor": "あおい", "action": "外部送信"}],
}
ENTRY_POINTS_SOURCE = (
    "reference/gijinka-extraction-pipeline/docs/diagrams/"
    "sequence_diagram_explained.md:278 (existing_routes)"
)

# 層3の sources(一致プリミティブの引数の取得元)は DMN XML に書かれていない。
# 補うと「どのマスタのどの項目と比べたか」を実装者が創作することになるため、
# 空のままにして報告する(HANDOFF 4節・タスク2)。
SOURCE_GAP_NOTE = (
    "sources は元の DMN XML に情報が無いため未設定。"
    "一致プリミティブの実装時に、出典付きで付与する必要がある"
)

# 元の DMN の不足・矛盾に対する修正(D34)。`reference/` の元ファイルは書き換えず、
# `registry/dmn/` に置く側だけを直す。承認されたものだけを並べる。
FIX_APPROVER = "Kazuhiro"

DMN_FIXES: list[dict] = [
    {
        "fix_id": "drop_identity_result_value_from_identity_level_reference",
        "kind": "remove_cell_value",
        "decision_table_id": "dt_transmission",
        "column": "本人確認判定.identity_level",
        "row_ids": ["tr_r3", "tr_r5"],
        "value": "未",
        "change": (
            "送信可否判定の tr_r3・tr_r5 の「確認レベル」のセルから \"未\" を削除する"
        ),
        "reason": (
            "「未」は本人確認判定の identity_result(済／未)の値で、"
            "identity_level(高／中／不一致検出／情報不足)には決して来ない。"
            "残すと、この値を参照する行が実行時に当たらないまま C11 に当たり続ける"
        ),
        "approved_by": FIX_APPROVER,
    },
    {
        "fix_id": "drop_unreferenced_input_data",
        "kind": "remove_input_data",
        "name": "added by me",
        "change": 'inputData "added by me"(と対応する DMNDI の図形)を削除する',
        "reason": (
            "どの決定表も参照していない inputData。実行時に集める事実が1つ増えたように"
            "見えるだけで、判定には使われない"
        ),
        "approved_by": FIX_APPROVER,
    },
]


# --------------------------------------------------------------------------
# DMN の読み取り
# --------------------------------------------------------------------------


def _text(element: Optional[ET.Element]) -> str:
    return "" if element is None or element.text is None else element.text.strip()


def _href_id(href: Optional[str]) -> str:
    return (href or "").lstrip("#")


def _read_decisions(root: ET.Element) -> list[dict]:
    """DMN の decision を、登録簿に起こすのに必要な形で読み取る。"""
    out = []
    for decision in root.findall("d:decision", _NS):
        table = decision.find("d:decisionTable", _NS)
        if table is None:
            continue
        required_decisions, required_inputs = [], []
        for requirement in decision.findall("d:informationRequirement", _NS):
            dec = requirement.find("d:requiredDecision", _NS)
            inp = requirement.find("d:requiredInput", _NS)
            if dec is not None:
                required_decisions.append(_href_id(dec.get("href")))
            if inp is not None:
                required_inputs.append(_href_id(inp.get("href")))
        rules = []
        for position, rule in enumerate(table.findall("d:rule", _NS), start=1):
            rules.append(
                {
                    "row_id": rule.get("id"),
                    "position": position,
                    "inputs": [
                        _text(e.find("d:text", _NS))
                        for e in rule.findall("d:inputEntry", _NS)
                    ],
                    "outputs": [
                        _text(e.find("d:text", _NS))
                        for e in rule.findall("d:outputEntry", _NS)
                    ],
                }
            )
        out.append(
            {
                "decision_id": decision.get("id"),
                "decision_name": decision.get("name"),
                "decision_table_id": table.get("id"),
                "hit_policy": table.get("hitPolicy") or "UNIQUE",
                "input_columns": [
                    _text(i.find("d:inputExpression/d:text", _NS))
                    for i in table.findall("d:input", _NS)
                ],
                "output_columns": [
                    o.get("name") for o in table.findall("d:output", _NS)
                ],
                "required_decisions": required_decisions,
                "required_inputs": required_inputs,
                "knowledge_sources": [
                    _href_id(a.find("d:requiredAuthority", _NS).get("href"))
                    for a in decision.findall("d:authorityRequirement", _NS)
                    if a.find("d:requiredAuthority", _NS) is not None
                ],
                "rules": rules,
            }
        )
    return out


def _input_data_names(root: ET.Element) -> dict[str, str]:
    return {
        i.get("id"): i.get("name") for i in root.findall("d:inputData", _NS)
    }


# --------------------------------------------------------------------------
# 元の DMN の修正(D34)
# --------------------------------------------------------------------------


def _find_decision_table(root: ET.Element, decision_table_id: str) -> Optional[ET.Element]:
    for decision in root.findall("d:decision", _NS):
        table = decision.find("d:decisionTable", _NS)
        if table is not None and table.get("id") == decision_table_id:
            return table
    return None


def _remove_cell_value(root: ET.Element, fix: dict) -> dict:
    """決定表のセル(FEEL のリテラル列)から値を1つ取り除く。

    セルの読み方は D28 の `_cell_values()` に任せ、残った値を同じ FEEL の表記で
    書き戻す。取り除く値がもう無い場合は停止する(修正の一覧が実態と合っていない
    ことを黙って見逃さないため)。
    """
    table = _find_decision_table(root, fix["decision_table_id"])
    if table is None:
        raise SchemaWiringError(
            f"修正 {fix['fix_id']!r} を適用できません: 決定表 "
            f"{fix['decision_table_id']!r} が DMN にありません。"
        )
    columns = [
        _text(i.find("d:inputExpression/d:text", _NS))
        for i in table.findall("d:input", _NS)
    ]
    if fix["column"] not in columns:
        raise SchemaWiringError(
            f"修正 {fix['fix_id']!r} を適用できません: 決定表 "
            f"{fix['decision_table_id']!r} に列 {fix['column']!r} がありません"
            f"(入力列: {columns})。"
        )
    index = columns.index(fix["column"])
    rules = {r.get("id"): r for r in table.findall("d:rule", _NS)}
    for row_id in fix["row_ids"]:
        rule = rules.get(row_id)
        if rule is None:
            raise SchemaWiringError(
                f"修正 {fix['fix_id']!r} を適用できません: 決定表 "
                f"{fix['decision_table_id']!r} に行 {row_id!r} がありません。"
            )
        text_el = rule.findall("d:inputEntry", _NS)[index].find("d:text", _NS)
        where = f"{fix['decision_table_id']}/{row_id}/{fix['column']}"
        values = _cell_values(_text(text_el), where=where)
        if fix["value"] not in values:
            raise SchemaWiringError(
                f"修正 {fix['fix_id']!r} を適用できません: {where} のセルに "
                f"{fix['value']!r} がありません(値: {values})。すでに直っているなら、"
                "DMN_FIXES から外してください。"
            )
        remaining = [v for v in values if v != fix["value"]]
        if not remaining:
            raise SchemaWiringError(
                f"修正 {fix['fix_id']!r} を適用できません: {where} のセルが空になります。"
            )
        text_el.text = ",".join(f'"{v}"' for v in remaining)
    return _fix_record(fix, scope="rule", row_ids=list(fix["row_ids"]))


def _remove_input_data(root: ET.Element, fix: dict) -> dict:
    """どの決定表も参照していない inputData を、DMNDI の図形ごと取り除く。"""
    targets = [i for i in root.findall("d:inputData", _NS) if i.get("name") == fix["name"]]
    if not targets:
        raise SchemaWiringError(
            f"修正 {fix['fix_id']!r} を適用できません: inputData {fix['name']!r} が"
            "DMN にありません。すでに直っているなら、DMN_FIXES から外してください。"
        )
    used = {
        column
        for decision in _read_decisions(root)
        for column in decision["input_columns"]
    }
    if fix["name"] in used:
        raise SchemaWiringError(
            f"修正 {fix['fix_id']!r} を適用できません: inputData {fix['name']!r} は"
            "決定表から参照されています(参照されている入力は削除しません)。"
        )
    removed_ids = {i.get("id") for i in targets}
    for element in targets:
        root.remove(element)
    # DMNDI の図形を残すと、存在しない要素を指す dmnElementRef になる
    for diagram in root.findall("dmndi:DMNDI/dmndi:DMNDiagram", _NS):
        for shape in list(diagram.findall("dmndi:DMNShape", _NS)):
            if shape.get("dmnElementRef") in removed_ids:
                diagram.remove(shape)
    return _fix_record(fix, scope="dmn_model")


def _fix_record(fix: dict, *, scope: str, row_ids: Optional[list[str]] = None) -> dict:
    """`history` に残す修正の記録(内容・理由・承認者)。"""
    record = {
        "change": "dmn_fix",
        "fix_id": fix["fix_id"],
        "description": fix["change"],
        "reason": fix["reason"],
        "approved_by": fix["approved_by"],
        "scope": scope,
    }
    if scope == "rule":
        record["decision_table_id"] = fix["decision_table_id"]
        record["row_ids"] = list(row_ids or [])
    return record


_FIX_KINDS = {
    "remove_cell_value": _remove_cell_value,
    "remove_input_data": _remove_input_data,
}


def apply_dmn_fixes(root: ET.Element, fixes: Optional[list[dict]] = None) -> list[dict]:
    """承認済みの修正を DMN の木に当て、`history` に残す記録を返す(D34)。

    `fixes` を空にすると修正なしで登録できる(修正を外すと C5・C11 で止まることを
    確かめるテスト用)。
    """
    applied: list[dict] = []
    for fix in DMN_FIXES if fixes is None else fixes:
        apply_one = _FIX_KINDS.get(fix["kind"])
        if apply_one is None:
            raise SchemaWiringError(
                f"修正 {fix['fix_id']!r} の kind={fix['kind']!r} は未対応です。"
            )
        applied.append(apply_one(root, fix))
    return applied


def _record_fixes(registry: Registry, applied: list[dict]) -> None:
    """適用した修正を層1・層2の history に残す(D34)。

    行に閉じた修正(`scope="rule"`)は、その決定表の層1エントリと対象の行に残す。
    モデル全体に関わる修正(`scope="dmn_model"`。参照されない inputData の削除など)は
    置き場所が1つに決まらないため、そのモデルの層1エントリすべてに残す
    (登録簿にモデル単位のエントリが無い。**要報告事項**)。
    """
    for record in applied:
        if record["scope"] == "dmn_model":
            for entry in registry.decision_tables:
                entry["history"].append(dict(record))
            continue
        table_id = record["decision_table_id"]
        for entry in registry.decision_tables:
            if entry["decision_table_id"] == table_id:
                entry["history"].append(dict(record))
        for row in registry.rule_rows:
            if (
                row["decision_table_id"] == table_id
                and row["row_id"] in record["row_ids"]
            ):
                row["history"].append(dict(record))


# --------------------------------------------------------------------------
# 登録簿の組み立て
# --------------------------------------------------------------------------


def _provenance(note: Optional[str] = None) -> dict:
    out = {
        "origin": "bootstrap",
        "clause_ref": CLAUSE_REF,
        "registered_by": REGISTERED_BY,
        "registered_at": REGISTERED_AT,
    }
    if note:
        out["note"] = note
    return out


def _is_dot_reference(column: str) -> bool:
    return "." in column


@dataclass
class BootstrapResult:
    """初期登録の結果。"""

    registry: Registry
    # 元の DMN に残った不足・矛盾(補わずに報告するもの)
    problems: list[str] = field(default_factory=list)
    # `registry/dmn/` に置く DMN XML(承認済みの修正を当てたもの。D34)
    dmn_xml: str = ""
    # 当てた修正の記録(内容・理由・承認者)
    fixes: list[dict] = field(default_factory=list)


def build_registry(
    source_dmn: str | Path, *, fixes: Optional[list[dict]] = None
) -> BootstrapResult:
    """DMN XML から登録簿(層1・層2・層3)を起こす。

    承認済みの修正(D34)を当ててから読み取るので、`problems` に残るのは「修正して
    いない不足・矛盾」だけになる。最後に登録簿へ C5・C11 を当て、違反があれば
    `SchemaWiringError` で停止する。

    `fixes=[]` を渡すと修正なしで組み立てる(修正を外すと止まることの確認用)。
    """
    _register_namespaces()
    root = ET.parse(str(source_dmn)).getroot()
    applied = apply_dmn_fixes(root, fixes)
    decisions = _read_decisions(root)
    input_data = _input_data_names(root)

    # ---- 層1 ----
    layer1 = []
    for d in decisions:
        entry_points = ENTRY_POINTS.get(d["decision_table_id"], [])
        provenance = _provenance(
            f"entry_points の出典: {ENTRY_POINTS_SOURCE}" if entry_points else None
        )
        layer1.append(
            {
                "decision_table_id": d["decision_table_id"],
                "decision_id": d["decision_id"],
                "decision_name": d["decision_name"],
                "dmn_model": DMN_MODEL,
                "entry_points": entry_points,
                "hit_policy": d["hit_policy"],
                "input_columns": list(d["input_columns"]),
                "output_columns": list(d["output_columns"]),
                "depends_on_decisions": list(d["required_decisions"]),
                "depends_on_inputs": [
                    input_data.get(i, i) for i in d["required_inputs"]
                ],
                "knowledge_sources": list(d["knowledge_sources"]),
                "provenance": provenance,
                # 修正の記録を残す場所(D34)。層2の行と同じ形にそろえる
                "history": [],
            }
        )

    # ---- 層2 ----
    layer2 = []
    for d in decisions:
        for rule in d["rules"]:
            layer2.append(
                {
                    "row_id": rule["row_id"],
                    "decision_table_id": d["decision_table_id"],
                    "position": rule["position"],
                    # セルは DMN の表記のまま持つ(`"中","未"` のような複数値を
                    # 1つの文字列に落とさないため)。
                    "conditions": dict(zip(d["input_columns"], rule["inputs"])),
                    "outputs": dict(zip(d["output_columns"], rule["outputs"])),
                    "provenance": _provenance(),
                    "history": [],
                }
            )

    # ---- 層3 ----
    layer3 = []
    for variable in _fact_order(decisions):
        values = _values_of_column(decisions, variable)
        primitive = FACT_PRIMITIVES.get(variable)
        layer3.append(
            {
                "variable": variable,
                "primitive": primitive if primitive else "",
                "possible_values": values,
                "sources": {},
                "comparison_kind": None,
                "approved_by": REGISTERED_BY,
                "defined_by": CLAUSE_REF,
                "used_by": sorted(
                    {
                        d["decision_table_id"]
                        for d in decisions
                        if variable in d["input_columns"]
                    }
                ),
                "provenance": _provenance(SOURCE_GAP_NOTE),
                "history": [],
            }
        )

    registry = Registry(
        decision_tables=layer1, rule_rows=layer2, fact_registry=layer3
    )
    _record_fixes(registry, applied)
    # D34: 修正後の登録簿に C5・C11 を当てる。元の DMN の矛盾を抱えたまま
    # 登録すると、あとで正解 IR がその値を参照するたびに C11 に当たる
    check_registry_cell_values(registry)
    return BootstrapResult(
        registry=registry,
        problems=find_problems(decisions, input_data),
        dmn_xml=_serialize(root),
        fixes=applied,
    )


def _fact_order(decisions: list[dict]) -> list[str]:
    """fact(ドットを含まない入力列)を、決定表の宣言順で重複なく並べる。"""
    order: list[str] = []
    for d in decisions:
        for column in d["input_columns"]:
            if not _is_dot_reference(column) and column not in order:
                order.append(column)
    return order


def _values_of_column(decisions: list[dict], column: str) -> list[str]:
    """決定表のセルに出てくる値だけを、出現順で集める(D4)。"""
    values: list[str] = []
    for d in decisions:
        if column not in d["input_columns"]:
            continue
        index = d["input_columns"].index(column)
        for rule in d["rules"]:
            if index >= len(rule["inputs"]):
                continue
            for value in _cell_values(
                rule["inputs"][index],
                where=f"{d['decision_table_id']}/{rule['row_id']}/{column}",
            ):
                if value not in values:
                    values.append(value)
    return values


# --------------------------------------------------------------------------
# 元の DMN の不足・矛盾の洗い出し(補わずに報告する)
# --------------------------------------------------------------------------


def find_problems(decisions: list[dict], input_data: dict[str, str]) -> list[str]:
    """初期登録の元にした DMN の不足・矛盾を列挙する(HANDOFF タスク2)。

    承認済みの修正(D34)を当てたあとの木を渡すので、修正した2件(「未」と
    参照されない inputData)はここに出ない。使われない出力(identity_result・
    info_class)は、あおいの実行時に説明やログで使っている可能性があるため
    削除せず、報告に残す。
    """
    problems: list[str] = []

    # (1) どの決定表からも参照されていない inputData
    used_inputs = {
        name
        for d in decisions
        for name in d["input_columns"]
        if not _is_dot_reference(name)
    }
    for input_id, name in input_data.items():
        if name not in used_inputs:
            problems.append(
                f"inputData {name!r}(id={input_id}) をどの決定表も参照していない"
            )

    # (2) ドット参照の条件値が、参照先の判定の出力値に無い(C11 相当)
    by_name = {d["decision_name"]: d for d in decisions}
    for d in decisions:
        for index, column in enumerate(d["input_columns"]):
            if not _is_dot_reference(column):
                continue
            decision_name, _, output_name = column.partition(".")
            referenced = by_name.get(decision_name)
            if referenced is None:
                problems.append(f"{column!r} の参照先 {decision_name!r} が DMN に無い")
                continue
            if output_name not in referenced["output_columns"]:
                problems.append(
                    f"{column!r} の出力 {output_name!r} が "
                    f"{decision_name!r} に無い"
                )
                continue
            out_index = referenced["output_columns"].index(output_name)
            produced = {
                value
                for rule in referenced["rules"]
                for value in _cell_values(
                    rule["outputs"][out_index], where=column
                )
            }
            for rule in d["rules"]:
                for value in _cell_values(rule["inputs"][index], where=column):
                    if value not in produced:
                        problems.append(
                            f"{d['decision_table_id']}/{rule['row_id']} の "
                            f"{column!r} = {value!r} は "
                            f"{decision_name!r} が出力しない値(この行のこの値は当たらない)"
                        )

    # (3) どの決定表からも参照されていない出力列
    referenced_outputs = {
        column for d in decisions for column in d["input_columns"] if _is_dot_reference(column)
    }
    for d in decisions:
        for output_name in d["output_columns"]:
            dotted = f"{d['decision_name']}.{output_name}"
            is_final = bool(ENTRY_POINTS.get(d["decision_table_id"]))
            if not is_final and dotted not in referenced_outputs:
                problems.append(
                    f"{d['decision_name']}.{output_name} をどの決定表も参照していない"
                    "(中間判定の出力なので、使われないなら不要)"
                )
    return problems


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------


def bootstrap(
    source_dmn: str | Path = SOURCE_DMN_DEFAULT,
    registry_path: str | Path = REGISTRY_PATH_DEFAULT,
    dmn_dir: str | Path = DMN_DIR_DEFAULT,
    *,
    fixes: Optional[list[dict]] = None,
) -> BootstrapResult:
    """DMN を `registry/dmn/` に置き、登録簿を作って保存する(再実行可能)。

    置く DMN は、承認済みの修正(D34)を当てたもの。`reference/` の元ファイルは
    書き換えない。同じ入力なら何度実行してもバイト単位で同じ結果になる。
    """
    source = Path(source_dmn)
    if not source.exists():
        raise FileNotFoundError(
            f"初期登録の元にする DMN が見つかりません: {source}\n"
            f"既定の入力は {SOURCE_DMN_DEFAULT}(D42 でコミットした元の DMN)です。"
        )
    result = build_registry(source, fixes=fixes)
    target = dmn_path(DMN_MODEL, dmn_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.dmn_xml, encoding="utf-8")
    result.registry.save(registry_path)
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dmn", default=SOURCE_DMN_DEFAULT)
    parser.add_argument("--registry", default=REGISTRY_PATH_DEFAULT)
    parser.add_argument("--dmn-dir", default=DMN_DIR_DEFAULT)
    args = parser.parse_args(argv)

    result = bootstrap(args.source_dmn, args.registry, args.dmn_dir)
    registry, problems = result.registry, result.problems
    print(f"元にした DMN: {args.source_dmn}")
    print(f"置いた DMN  : {dmn_path(DMN_MODEL, args.dmn_dir)}")
    print(f"登録簿      : {args.registry}")
    print()
    print(f"層1(決定表) {len(registry.decision_tables)}件")
    for entry in registry.decision_tables:
        print(
            f"  - {entry['decision_table_id']} / {entry['decision_name']}"
            f" in={entry['input_columns']} out={entry['output_columns']}"
            f" entry_points={entry['entry_points']}"
        )
    print(f"層2(ルール行) {len(registry.rule_rows)}件")
    for table in registry.decision_tables:
        rows = registry.rows_of(table["decision_table_id"])
        print(f"  - {table['decision_table_id']}: {len(rows)}行")
    print(f"層3(fact) {len(registry.fact_registry)}件")
    for fact in registry.fact_registry:
        print(
            f"  - {fact['variable']} [{fact['primitive']}]"
            f" {fact['possible_values']} used_by={fact['used_by']}"
        )
    print()
    print(f"元の DMN に当てた修正 {len(result.fixes)}件(承認者: {FIX_APPROVER}。D34):")
    for record in result.fixes:
        print(f"  * {record['description']}")
        print(f"    理由: {record['reason']}")
        print(f"    記録先: 層1・層2の history(scope={record['scope']})")
    print()
    if problems:
        print(f"元の DMN に見つかった不足・矛盾 {len(problems)}件(補っていない):")
        for problem in problems:
            print(f"  * {problem}")
    else:
        print("元の DMN に不足・矛盾は見つからなかった")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
