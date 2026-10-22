"""4点セット同時生成(スキーマ配線ジェネレータ)v2。

DMN XML／バインディング設定JSON／ルーティング表JSON／Scope・Decision登録簿の4点セットは、
同一のIR(`ClauseIR`)と登録簿から**1回の呼び出しで同時に**生成する。

仕様: docs/design/aoi_doc2dmn_IRv2_design.md 4章(4点セットの導き方)・5章(クロス検証)。

## v1(コミット 9e66776)との違い

- 引数が `(ir, *, registry)` になった。v1 は `new_bindings`・`definitions_id`・
  `definitions_name`・`namespace` を IR の外から渡していたため、「4点セットを同一IRから
  同時生成する」原則が3点にしか効いていなかった(D1・D7)
- `required_facts` を依存関係をたどって求める(設計書4.1)。中間判定を使う場合、最終判定の
  入力列には他の判定の出力が入るため、入力列をそのまま写すのでは実際に集める事実にならない
- ルーティング表に `dmn_model`・`decision_id`・`required_facts` を載せる(D8)
- 返す登録簿は、入力登録簿にマージした**更新後の全体**(設計書4章「registry(更新後)」)
- クロス検証を3項目から10項目に増やした(D9)

## クロス検証の実行順序(設計書5章の補完。要報告事項)

設計書5章は「4点セットを作った後に確かめる」とあるが、C3・C4・C6・C9・C10 は生成の
**前提**である。特に C9 条件1が破れていると `required_facts` の導出自体が始められず、
設計書が要求する「わざと違反させた IR で SchemaWiringError になる」失敗テストが
`KeyError` になってしまう。そのため次の2段構えにした。

1. 生成前(IR と登録簿だけで判定できるもの): C3 → C9 → C4 → C6 → C10
2. 生成後(成果物を突合するもの): C1 → C5 → C7 → C8
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .ir_schema import (
    PRIMITIVE_ARGS,
    ClauseIR,
    FactDef,
    RuleRowDraft,
)
from .registry import Registry

DMN_NS = "https://www.omg.org/spec/DMN/20240513/MODEL/"
DMNDI_NS = "https://www.omg.org/spec/DMN/20230324/DMNDI/"
DC_NS = "http://www.omg.org/spec/DMN/20180521/DC/"
FEEL_NS = "https://www.omg.org/spec/DMN/20240513/FEEL/"
KIE_NS = "https://kie.org/dmn/extensions/1.0"
DI_NS = "http://www.omg.org/spec/DMN/20180521/DI/"

# DMN モデルの namespace は `<base>/<dmn_model>` で導出する。設計書4.2は definitions の
# id・name の導出元(target.dmn_model)だけを定めており namespace の規則が無いため、
# v1 のゴールデンパスと同じ値になるこの base を本実装で定める(要報告事項)。
DMN_NAMESPACE_BASE = "http://gijinka.example.com/dmn"

# 決定表のセルに書ける FEEL のサブセット: ワイルドカード、文字列リテラル、
# カンマ区切りのリテラル列のみ。範囲・比較・否定は未対応(AsIs 5.4)。
_UNSUPPORTED_FEEL_PATTERN = re.compile(r"(>=|<=|>|<|\.\.|\bnot\s*\()")
_STRING_LITERAL_PATTERN = re.compile(r'"([^"]*)"')


class SchemaWiringError(Exception):
    pass


@dataclass
class WiringBundle:
    """4点セット。同一IRと登録簿から同時生成される。"""

    dmn_xml: str
    # 設計書4.3が dmn_model を出力項目に挙げているため、v1 の裸の配列から
    # {dmn_model, facts} のエンベロープに変更した(要報告事項)。
    bindings: dict = field(default_factory=dict)
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


# --------------------------------------------------------------------------
# 小さな補助関数
# --------------------------------------------------------------------------


def _is_dot_reference(key: str) -> bool:
    return "." in key


def _dot_prefix(key: str) -> str:
    """「判定名.出力名」の判定名部分。`判定名.a.b` は先頭だけを判定名として扱う。"""
    return key.split(".", 1)[0]


def _dot_suffix(key: str) -> str:
    return key.split(".", 1)[1]


def _flat_fact_keys(ir: ClauseIR) -> set[str]:
    """IRのどこかの決定表で参照されている fact キー(ドット参照を除く)。"""
    return {k for k in ir.all_fact_keys() if not _is_dot_reference(k)}


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


def _cell_values(cell: str, *, where: str) -> list[str]:
    """決定表のセルから、条件として参照している値の一覧を取り出す(C5用)。

    - `-`(ワイルドカード) -> 空リスト(照合対象なし)
    - `"高", "中"` のような引用符付きのリテラル列 -> ["高", "中"]
    - 引用符なしの素の値 -> その値1つ(golden_path の conditions はこの形)
    - 範囲・比較・否定などの未対応FEEL -> SchemaWiringError
    """
    v = cell.strip()
    if v == "-":
        return []
    if _UNSUPPORTED_FEEL_PATTERN.search(v):
        raise SchemaWiringError(
            f"C5検証失敗: {where} のセル {cell!r} は未対応のFEEL式です。"
            "決定表のセルはワイルドカード(-)・文字列リテラル・カンマ区切りの"
            "リテラル列のみ対応しています。"
        )
    if '"' in v:
        literals = _STRING_LITERAL_PATTERN.findall(v)
        residue = _STRING_LITERAL_PATTERN.sub("", v).replace(",", "").strip()
        if not literals or residue:
            raise SchemaWiringError(
                f"C5検証失敗: {where} のセル {cell!r} を文字列リテラル列として解釈できません。"
            )
        return literals
    return [v]


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


def _ir_tables_by_decision_id(ir: ClauseIR) -> dict[str, object]:
    return {t.decision_id: t for t in ir.new_decision_tables}


def _ir_tables_by_decision_name(ir: ClauseIR) -> dict[str, object]:
    return {t.decision_name: t for t in ir.new_decision_tables}


def _resolve_decision_columns(
    ir: ClauseIR,
    registry: Registry,
    *,
    decision_id: str | None = None,
    decision_name: str | None = None,
) -> list[str]:
    """判定の入力列を、IRの新規決定表 -> 登録簿の層1 の順で探す。

    見つからない場合は `SchemaWiringError`(`KeyError` を外に出さない)。
    """
    if decision_id is not None:
        table = _ir_tables_by_decision_id(ir).get(decision_id)
        if table is not None:
            return list(table.input_columns)  # type: ignore[attr-defined]
        entry = registry.find_decision_table(decision_id=decision_id)
        if entry is not None:
            return list(entry.get("input_columns", []))
        raise SchemaWiringError(
            f"判定 decision_id={decision_id!r} が IR の new_decision_tables にも"
            "登録簿の層1にも見つかりません。"
        )
    if decision_name is not None:
        table = _ir_tables_by_decision_name(ir).get(decision_name)
        if table is not None:
            return list(table.input_columns)  # type: ignore[attr-defined]
        entry = registry.find_decision_table(decision_name=decision_name)
        if entry is not None:
            return list(entry.get("input_columns", []))
        raise SchemaWiringError(
            f"判定 decision_name={decision_name!r} が IR の new_decision_tables にも"
            "登録簿の層1にも見つかりません。"
        )
    raise SchemaWiringError("内部エラー: decision_id か decision_name のどちらかが必要です。")


_MAX_DECISION_DEPTH = 50


def derive_required_facts(ir: ClauseIR, registry: Registry) -> list[str]:
    """最終判定を評価するために実際に集める事実を求める(設計書4.1)。

    1. `target.entry_decision_id` の決定表の入力列から始める
    2. 「判定名.出力名」の形の列は、その判定の入力列に置き換える
    3. 2 を、ドットを含む列がなくなるまで繰り返す
    4. 残った列の集合が required_facts

    ドット参照の接頭辞は decision の**名前**である(FEEL式が参照する名前。
    `dmn_engine.py` は `ctx[decision.name]` でネストする)。

    宣言順の深さ優先で展開し、到達順を保った決定的なリストを返す。同じ判定に複数の
    経路で到達した場合、事実の側で重複排除する(判定の展開自体は経路ごとに行う)。

    次の異常はすべて `SchemaWiringError` にする(`KeyError` / `RecursionError` を
    外に出さない)。

    - 展開の経路上に同じ判定が再び現れる(循環参照)
    - 参照先の判定が IR にも登録簿の層1にも無い
    - 参照先の判定に入力列が無い(required_facts が静かに空になるのを防ぐ)
    """
    required: list[str] = []
    seen_facts: set[str] = set()

    def expand(columns: list[str], path: tuple[str, ...]) -> None:
        if len(path) > _MAX_DECISION_DEPTH:
            raise SchemaWiringError(
                f"required_facts の導出が深すぎます(判定の連鎖が{_MAX_DECISION_DEPTH}段を"
                f"超えました)。経路: {' -> '.join(path)}"
            )
        for col in columns:
            if not _is_dot_reference(col):
                if col not in seen_facts:
                    seen_facts.add(col)
                    required.append(col)
                continue
            name = _dot_prefix(col)
            if name in path:
                raise SchemaWiringError(
                    f"ドット参照 {col!r} で判定の循環参照を検出しました。"
                    f"経路: {' -> '.join(path + (name,))}"
                )
            sub_columns = _resolve_decision_columns(ir, registry, decision_name=name)
            if not sub_columns:
                raise SchemaWiringError(
                    f"ドット参照 {col!r} の判定 {name!r} に入力列がありません。"
                    "required_facts を求められません。"
                )
            expand(sub_columns, path + (name,))

    entry_columns = _resolve_decision_columns(
        ir, registry, decision_id=ir.target.entry_decision_id
    )
    expand(entry_columns, ())
    return required


# --------------------------------------------------------------------------
# クロス検証(設計書5章)
# --------------------------------------------------------------------------


def _check_c3_compound_fact(ir: ClauseIR) -> None:
    """C3: 一致プリミティブ側で複合判定(AND)を組む案は採らない。"""
    if ir.compound_fact_application.option_taken == "rejected_compound_in_primitive":
        raise SchemaWiringError(
            "C3検証失敗(複合fact禁止原則違反): 一致プリミティブ側で複合判定(AND)を組む案は"
            "採用できません。複合判定は決定表側の行として表現してください。"
            f"(rationale: {ir.compound_fact_application.rationale})"
        )


def _check_c9_level_determination(ir: ClauseIR, registry: Registry) -> None:
    """C9: レベル判定の整合(設計書3.7の5条件)。"""
    lv = ir.level_determination
    entry_id = ir.target.entry_decision_id

    # 条件1: final.action が new_table なら、new_decision_tables に entry の決定表がある
    if lv.final.action == "new_table":
        if entry_id not in _ir_tables_by_decision_id(ir):
            raise SchemaWiringError(
                f"C9検証失敗(条件1): final.action=new_table ですが、"
                f"new_decision_tables に target.entry_decision_id={entry_id!r} の"
                "決定表がありません。"
            )

    # 条件2: final.action が add_rows なら、compared_table_id は entry の決定表であり、
    #        rule_row_additions にその表の行がある
    if lv.final.action == "add_rows":
        compared = lv.final.compared_table_id
        if compared is None:
            raise SchemaWiringError(
                "C9検証失敗(条件2): final.action=add_rows ですが compared_table_id が None です。"
            )
        entry_table = registry.find_decision_table(decision_id=entry_id)
        ir_entry = _ir_tables_by_decision_id(ir).get(entry_id)
        entry_table_id = None
        if ir_entry is not None:
            entry_table_id = ir_entry.decision_table_id  # type: ignore[attr-defined]
        elif entry_table is not None:
            entry_table_id = entry_table.get("decision_table_id")
        if entry_table_id is None:
            raise SchemaWiringError(
                f"C9検証失敗(条件2): target.entry_decision_id={entry_id!r} の決定表が"
                "IR にも登録簿の層1にも見つかりません。"
            )
        if compared != entry_table_id:
            raise SchemaWiringError(
                f"C9検証失敗(条件2): compared_table_id={compared!r} が "
                f"target.entry_decision_id={entry_id!r} の決定表 {entry_table_id!r} と"
                "一致しません。"
            )
        if not any(r.decision_table_id == compared for r in ir.rule_row_additions):
            raise SchemaWiringError(
                f"C9検証失敗(条件2): final.action=add_rows ですが、rule_row_additions に"
                f"決定表 {compared!r} の行がありません。"
            )

    # 条件3: intermediate の action が add_row なら、rule_row_additions に table_id の表の
    #        行があり、new_output_values の値がその行の出力に現れる
    for change in lv.intermediate:
        if change.action != "add_row":
            continue
        rows = [r for r in ir.rule_row_additions if r.decision_table_id == change.table_id]
        if not rows:
            raise SchemaWiringError(
                f"C9検証失敗(条件3): intermediate.action=add_row ですが、rule_row_additions に"
                f"決定表 {change.table_id!r} の行がありません。"
            )
        produced = {v for r in rows for v in r.outputs.values()}
        missing_values = [v for v in change.new_output_values if v not in produced]
        if missing_values:
            raise SchemaWiringError(
                f"C9検証失敗(条件3): new_output_values {missing_values} が、決定表 "
                f"{change.table_id!r} へ追加する行の出力に現れません(出力値: {sorted(produced)})。"
            )

    # 条件4: intermediate の table_id と final の compared_table_id は層1に実在する
    for change in lv.intermediate:
        if registry.find_decision_table(decision_table_id=change.table_id) is None:
            raise SchemaWiringError(
                f"C9検証失敗(条件4): intermediate の table_id={change.table_id!r} が"
                "登録簿の層1に実在しません。"
            )
    if lv.final.compared_table_id is not None:
        if registry.find_decision_table(decision_table_id=lv.final.compared_table_id) is None:
            raise SchemaWiringError(
                f"C9検証失敗(条件4): final の compared_table_id="
                f"{lv.final.compared_table_id!r} が登録簿の層1に実在しません。"
            )

    # 条件5: atomic_fact.covered が False なら new_primitives が空でない
    if not lv.atomic_fact.covered and not lv.atomic_fact.new_primitives:
        raise SchemaWiringError(
            "C9検証失敗(条件5): atomic_fact.covered=False ですが new_primitives が空です。"
            "新しいプリミティブの追加は③aの範囲外なので、何が必要かを明示してください。"
        )


def _check_c4_sources_match_primitive(ir: ClauseIR, registry: Registry) -> None:
    """C4: sources のキーが primitive の引数名と一致する(設計書3.5)。

    未知の primitive は、引数名の定義が無いため検証できない。`covered=False` で
    新しいプリミティブを宣言する経路があり得るが、その場合も③aの範囲外なので
    エラーとして止める(要報告事項)。
    """
    for fact in ir.facts.new_facts:
        _check_one_fact_sources(fact)
    # 層3から引く既存 fact も、台帳が壊れていないか同じ基準で確かめる
    for variable in ir.facts.referenced_facts:
        entry = registry.find_fact(variable)
        if entry is None:
            continue  # 実在確認は C6 の責務
        _check_one_fact_sources(FactDef.from_json(entry))


def _check_one_fact_sources(fact: FactDef) -> None:
    expected = PRIMITIVE_ARGS.get(fact.primitive)
    if expected is None:
        raise SchemaWiringError(
            f"C4検証失敗: fact {fact.variable!r} の primitive={fact.primitive!r} は"
            f"未知です(既知: {sorted(PRIMITIVE_ARGS)})。引数名の定義が無いため"
            "sources を検証できません。"
        )
    actual = set(fact.sources.keys())
    if actual != set(expected):
        raise SchemaWiringError(
            f"C4検証失敗: fact {fact.variable!r}(primitive={fact.primitive!r})の sources の"
            f"キー {sorted(actual)} が、プリミティブの引数名 {sorted(expected)} と"
            "一致しません。"
        )


def _check_c6_referenced_facts_exist(ir: ClauseIR, registry: Registry) -> None:
    """C6: referenced_facts と value_additions のキーが層3に実在する。"""
    missing_refs = [v for v in ir.facts.referenced_facts if registry.find_fact(v) is None]
    if missing_refs:
        raise SchemaWiringError(
            f"C6検証失敗: referenced_facts {sorted(missing_refs)} が登録簿の層3"
            "(fact台帳)に実在しません。"
        )
    missing_additions = [v for v in ir.facts.value_additions if registry.find_fact(v) is None]
    if missing_additions:
        raise SchemaWiringError(
            f"C6検証失敗: value_additions のキー {sorted(missing_additions)} が"
            "登録簿の層3(fact台帳)に実在しません。"
        )


def _check_c10_dot_references(ir: ClauseIR, registry: Registry) -> None:
    """C10: 「判定名.出力名」の判定が実在し、その出力名を持つ。

    接頭辞は decision の名前。出力名は、IR の新規決定表なら行の outputs のキー、
    登録簿のみに存在する判定なら層2の行から集める(層1に出力列の一覧が無いため。
    要報告事項)。
    """
    dot_refs = sorted({k for k in ir.all_fact_keys() if _is_dot_reference(k)})
    by_name = _ir_tables_by_decision_name(ir)
    for ref in dot_refs:
        name, output = _dot_prefix(ref), _dot_suffix(ref)
        table = by_name.get(name)
        if table is not None:
            produced = {
                key for row in table.rule_rows for key in row.outputs.keys()  # type: ignore[attr-defined]
            }
            if output not in produced:
                raise SchemaWiringError(
                    f"C10検証失敗: ドット参照 {ref!r} の判定 {name!r} に出力 {output!r} が"
                    f"ありません(出力: {sorted(produced)})。"
                )
            continue
        entry = registry.find_decision_table(decision_name=name)
        if entry is None:
            raise SchemaWiringError(
                f"C10検証失敗: ドット参照 {ref!r} の判定 {name!r} が IR にも"
                "登録簿の層1にも実在しません。"
            )
        produced = registry.output_names_of(entry.get("decision_table_id", ""))
        if output not in produced:
            raise SchemaWiringError(
                f"C10検証失敗: ドット参照 {ref!r} の判定 {name!r} に出力 {output!r} が"
                f"ありません(層2の行から集めた出力: {sorted(produced)})。"
            )


def _check_c1_facts_available(
    required_facts: list[str], ir: ClauseIR, registry: Registry
) -> None:
    """C1: required_facts のすべてが facts.new_facts か層3にある。"""
    new_names = {f.variable for f in ir.facts.new_facts}
    missing = [
        v for v in required_facts if v not in new_names and registry.find_fact(v) is None
    ]
    if missing:
        raise SchemaWiringError(
            f"C1検証失敗: required_facts {sorted(missing)} に対応する fact の定義が"
            "facts.new_facts にも登録簿の層3にもありません。"
        )


def _check_c2_new_facts_used(ir: ClauseIR) -> None:
    """C2: facts.new_facts のすべてが、どこかの決定表で使われている。

    基準は required_facts ではなく `all_fact_keys()`(ドット参照を除く)。
    到達しない中間表への行追加で使う fact を誤って弾かないため。
    """
    used = _flat_fact_keys(ir)
    unused = [f.variable for f in ir.facts.new_facts if f.variable not in used]
    if unused:
        raise SchemaWiringError(
            f"C2検証失敗: facts.new_facts の {sorted(unused)} が、どの決定表からも"
            "参照されていません。"
        )


def _check_c5_cell_values(
    ir: ClauseIR, registry: Registry, fact_defs: dict[str, FactDef]
) -> None:
    """C5: 決定表の各条件の値(「-」以外)が、その fact の possible_values にある。

    `new_decision_tables` の行と `rule_row_additions` の両方を見る。ドット参照の列は
    fact ではない(他の判定の出力)ため対象外(その値の検証は設計書に無い。要報告事項)。
    """
    rows: list[tuple[str, RuleRowDraft]] = [
        (f"決定表 {row.decision_table_id} の行 {row.row_id}", row)
        for row in ir.rule_row_additions
    ]
    for table in ir.new_decision_tables:
        for row in table.rule_rows:
            rows.append((f"決定表 {table.decision_table_id} の行 {row.row_id}", row))

    for where, row in rows:
        for column, cell in row.conditions.items():
            if _is_dot_reference(column):
                continue
            values = _cell_values(cell, where=f"{where} の列 {column}")
            if not values:
                continue
            fact = fact_defs.get(column)
            if fact is None:
                raise SchemaWiringError(
                    f"C5検証失敗: {where} が参照する列 {column!r} の fact 定義が"
                    "見つかりません(facts.new_facts にも登録簿の層3にもありません)。"
                )
            allowed = set(fact.possible_values) | set(
                ir.facts.value_additions.get(column, [])
            )
            unknown = [v for v in values if v not in allowed]
            if unknown:
                raise SchemaWiringError(
                    f"C5検証失敗: {where} の列 {column!r} が参照する値 {unknown} が、"
                    f"その fact の possible_values {sorted(allowed)} にありません"
                    "(この行は実行時に一度もヒットしません)。"
                )


def _check_c7_target_exists(ir: ClauseIR, dmn_xml: str, updated: Registry) -> None:
    """C7: target.entry_decision_id が DMN XML と層1に実在する。

    DMN XML 側の確認は `final.action = new_table` のときのみ行う。既存モデルへの
    行挿入(レベル2)は今回の範囲外で、`add_rows` では DMN XML を生成しないため
    (要報告事項)。
    """
    entry_id = ir.target.entry_decision_id
    if updated.find_decision_table(decision_id=entry_id) is None:
        raise SchemaWiringError(
            f"C7検証失敗: target.entry_decision_id={entry_id!r} が更新後の登録簿の層1に"
            "実在しません。"
        )
    if ir.level_determination.final.action == "new_table":
        if f'id="{entry_id}"' not in dmn_xml:
            raise SchemaWiringError(
                f"C7検証失敗: target.entry_decision_id={entry_id!r} の decision が"
                "生成した DMN XML に存在しません。"
            )


def _check_c8_routing_matches_bindings(routing_table: dict, bindings: dict) -> None:
    """C8: ルーティング表の required_facts とバインディング設定の fact 集合が一致する。

    どちらも同じ required_facts から導出しているため構造的に保証されるが、将来の
    リファクタで片方だけ変わった場合に気付けるよう明示的に確認する。
    """
    routed = {f for route in routing_table.get("routes", []) for f in route["required_facts"]}
    bound = {f["variable"] for f in bindings.get("facts", [])}
    if routed != bound:
        raise SchemaWiringError(
            f"C8検証失敗: ルーティング表の required_facts {sorted(routed)} と"
            f"バインディング設定の fact {sorted(bound)} が一致しません。"
        )


# --------------------------------------------------------------------------
# DMN XML
# --------------------------------------------------------------------------


def render_dmn_xml(
    ir: ClauseIR,
    registry: Registry,
    *,
    definitions_id: str,
    definitions_name: str,
    namespace: str,
) -> str:
    """IRの新規決定表から、完結した1つのDMNファイルのXMLを生成する。

    ## ドット参照の href(v1 からのバグ修正)

    v1 は「判定名.出力名」の接頭辞をそのまま `href="#<接頭辞>"` にしていた。しかし
    FEEL式が参照する接頭辞は decision の**名前**で、`href` に必要なのは decision の
    **ID** である。IR と登録簿から名前->ID を解決して `href` に入れる。
    `inputExpression/text` と input の id はドット付きの名前のまま残す
    (`dmn_engine.py` の `_resolve_expression` が名前で引くため)。
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
    by_name = _ir_tables_by_decision_name(ir)

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
            req = ET.SubElement(
                decision, f"{{{DMN_NS}}}informationRequirement", {"id": _uid("ir")}
            )
            if _is_dot_reference(col):
                dep_name = _dot_prefix(col)
                dep_table = by_name.get(dep_name)
                if dep_table is not None:
                    dep_id = dep_table.decision_id  # type: ignore[attr-defined]
                else:
                    entry = registry.find_decision_table(decision_name=dep_name)
                    if entry is None:
                        raise SchemaWiringError(
                            f"ドット参照 {col!r} の判定 {dep_name!r} の decision_id を"
                            "解決できません(IR にも登録簿の層1にもありません)。"
                        )
                    dep_id = entry.get("decision_id")
                ET.SubElement(req, f"{{{DMN_NS}}}requiredDecision", {"href": f"#{dep_id}"})
            else:
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
                entry_el = ET.SubElement(rule, f"{{{DMN_NS}}}inputEntry", {"id": _uid("in")})
                ET.SubElement(entry_el, f"{{{DMN_NS}}}text").text = _feel_literal(
                    row.conditions.get(col, "-")
                )
            for name in output_names:
                entry_el = ET.SubElement(rule, f"{{{DMN_NS}}}outputEntry", {"id": _uid("out")})
                ET.SubElement(entry_el, f"{{{DMN_NS}}}text").text = _feel_literal(
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
        ET.SubElement(definitions, f"{{{DMN_NS}}}knowledgeSource", {"id": ks, "name": ks})

    ET.indent(definitions, space="  ")
    xml_body = ET.tostring(definitions, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8" ?>\n{xml_body}\n'


# --------------------------------------------------------------------------
# 4点セットの生成
# --------------------------------------------------------------------------


def _collect_fact_defs(
    ir: ClauseIR, registry: Registry, required_facts: list[str]
) -> dict[str, FactDef]:
    """required_facts と決定表が参照する fact の定義を集める。

    `facts.new_facts` にあればそれを、なければ層3の定義を使う(設計書4.3)。
    C5 は required_facts に含まれない列(到達しない表への行追加)も見るため、
    決定表が参照する fact も合わせて集める。
    """
    defs: dict[str, FactDef] = {f.variable: f for f in ir.facts.new_facts}
    wanted = set(required_facts) | _flat_fact_keys(ir)
    for variable in wanted:
        if variable in defs:
            continue
        entry = registry.find_fact(variable)
        if entry is not None:
            defs[variable] = FactDef.from_json(entry)
    return defs


def _build_bindings(
    ir: ClauseIR, required_facts: list[str], fact_defs: dict[str, FactDef]
) -> dict:
    """バインディング設定(設計書4.3)。

    対象は required_facts のすべて。`value_additions` があれば possible_values に加える。
    設計書4.3が `dmn_model` を出力項目に挙げているため、{dmn_model, facts} の
    エンベロープにしている(v1 は裸の配列。要報告事項)。
    """
    facts_json = []
    for variable in required_facts:
        fact = fact_defs[variable]
        payload = fact.to_json()
        additions = ir.facts.value_additions.get(variable)
        if additions:
            merged = list(payload["possible_values"])
            for value in additions:
                if value not in merged:
                    merged.append(value)
            payload["possible_values"] = merged
        facts_json.append(payload)
    return {"dmn_model": ir.target.dmn_model, "facts": facts_json}


def _build_routing_table(ir: ClauseIR, required_facts: list[str]) -> dict:
    """ルーティング表(設計書4.4)。

    v1 の `actor_action` の入れ子と `input_columns` は廃止し、actor・action・
    dmn_model・decision_id・required_facts のフラットな1行にする(D8)。
    """
    return {
        "routes": [
            {
                "actor": ir.actor,
                "action": ir.action.normalized,
                "dmn_model": ir.target.dmn_model,
                "decision_id": ir.target.entry_decision_id,
                "required_facts": list(required_facts),
            }
        ]
    }


def _build_updated_registry(
    ir: ClauseIR, registry: Registry, fact_defs: dict[str, FactDef]
) -> Registry:
    """入力登録簿にこの条文の内容をマージした、更新後の登録簿を作る(設計書4.5)。"""
    updated = registry.copy()
    provenance = ir.provenance.to_json()

    # 層1: 新しい決定表のメタ情報(dmn_model を追加。D8)
    used_by: dict[str, list[str]] = {}
    for table in ir.new_decision_tables:
        updated.decision_tables.append(
            {
                "decision_table_id": table.decision_table_id,
                "decision_id": table.decision_id,
                "decision_name": table.decision_name,
                "dmn_model": ir.target.dmn_model,
                "hit_policy": "FIRST",
                "input_columns": list(table.input_columns),
                "depends_on_decisions": list(table.depends_on_decisions),
                "depends_on_inputs": list(table.depends_on_inputs),
                "knowledge_sources": list(table.knowledge_sources),
                "provenance": provenance,
            }
        )
        for col in table.input_columns:
            if not _is_dot_reference(col):
                used_by.setdefault(col, []).append(table.decision_table_id)

    # 層2: 新規決定表の行と、既存表への追加行の両方
    for row in ir.rule_row_additions:
        updated.rule_rows.append(_rule_row_to_registry_entry(row, provenance))
        for col in row.conditions:
            if not _is_dot_reference(col):
                used_by.setdefault(col, []).append(row.decision_table_id)
    for table in ir.new_decision_tables:
        for row in table.rule_rows:
            updated.rule_rows.append(_rule_row_to_registry_entry(row, provenance))

    # 層3: 新しい fact を追加し、value_additions を既存エントリに反映
    for fact in ir.facts.new_facts:
        entry = fact.to_json()
        entry["defined_by"] = ir.clause_ref
        entry["used_by"] = sorted(set(used_by.get(fact.variable, [])))
        entry["provenance"] = provenance
        entry["history"] = []
        updated.fact_registry.append(entry)

    for variable, additions in ir.facts.value_additions.items():
        entry = updated.find_fact(variable)
        if entry is None:
            continue  # 実在確認は C6 の責務
        values = list(entry.get("possible_values", []))
        added = [v for v in additions if v not in values]
        entry["possible_values"] = values + added
        if added:
            history = list(entry.get("history", []))
            history.append(
                {
                    "change": "value_addition",
                    "added_values": added,
                    "provenance": provenance,
                }
            )
            entry["history"] = history

    # 既存 fact を使った場合も used_by を更新する
    for variable, tables in used_by.items():
        if any(f.variable == variable for f in ir.facts.new_facts):
            continue
        entry = updated.find_fact(variable)
        if entry is None:
            continue
        merged = sorted(set(list(entry.get("used_by", [])) + tables))
        entry["used_by"] = merged

    # review: ゲート名の一覧(gates)と説明文(gate_reasons)の両方を持たせる
    updated.review = {
        "clause_ref": ir.clause_ref,
        "human_review_status": ir.human_review_status,
        "should_review": ir.review_gates.should_review,
        "gates": ir.review_gates.gate_names(),
        "gate_reasons": ir.review_gates.reasons(),
    }
    return updated


def generate_wiring_bundle(ir: ClauseIR, *, registry: Registry) -> WiringBundle:
    """IRと登録簿を入力として4点セットを1回の呼び出しで生成する。

    クロス検証に失敗すると `SchemaWiringError` で停止する(設計書5章 C1〜C10)。
    """
    # --- 生成前の検証(IRと登録簿だけで判定できるもの) ---
    _check_c3_compound_fact(ir)
    _check_c9_level_determination(ir, registry)
    _check_c4_sources_match_primitive(ir, registry)
    _check_c6_referenced_facts_exist(ir, registry)
    _check_c10_dot_references(ir, registry)
    _check_c2_new_facts_used(ir)

    # --- required_facts の導出(設計書4.1) ---
    required_facts = derive_required_facts(ir, registry)
    fact_defs = _collect_fact_defs(ir, registry, required_facts)
    _check_c1_facts_available(required_facts, ir, registry)

    # --- 4点セットの生成 ---
    _check_c5_cell_values(ir, registry, fact_defs)

    dmn_model = ir.target.dmn_model
    dmn_xml = render_dmn_xml(
        ir,
        registry,
        definitions_id=f"{dmn_model}_definitions",
        definitions_name=dmn_model,
        namespace=f"{DMN_NAMESPACE_BASE}/{dmn_model}",
    )
    bindings = _build_bindings(ir, required_facts, fact_defs)
    routing_table = _build_routing_table(ir, required_facts)
    updated = _build_updated_registry(ir, registry, fact_defs)

    # --- 生成後の検証(成果物を突合するもの) ---
    _check_c7_target_exists(ir, dmn_xml, updated)
    _check_c8_routing_matches_bindings(routing_table, bindings)

    return WiringBundle(
        dmn_xml=dmn_xml,
        bindings=bindings,
        routing_table=routing_table,
        registry=updated.to_json(),
    )
