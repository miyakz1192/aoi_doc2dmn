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

## v1.2 で追加した受け入れ経路(第17回 HANDOFF タスク3)

- **既存モデルへの行の挿入**(D15): `registry/dmn/<dmn_model>.dmn` を読み込み、
  `position` の位置に rule を挿入する。レベル2(`final.action=add_rows`)と
  中間判定への行追加(`intermediate.action=add_row`)の両方
- **列の追加**(D19): `intermediate.action=add_column` のとき、input と inputData を
  足し、既存の全 rule のその列を「-」で埋める
- **影響を受ける最終判定の作り直し**(D16): IR が変更した既存の表を起点に層1の
  `depends_on_decisions` を逆にたどり、影響を受ける最終判定をすべて洗い出して
  ルーティング表の行とバインディング設定を作り直す
- **同一モデルの原則**(D17): ドット参照の参照先・作り直す最終判定が別の DMN モデルに
  あれば `SchemaWiringError` で停止する
- **C11**(D13): ドット参照の列の条件値が参照先の判定の出力値にあること

## v1.3 で変えた点(設計書2章 D24〜D35)

- **D24**: D16 の洗い出しの起点は、IR が実際に行・列・出力値を足した既存の表だけ。
  `reuse_value` の表と `new_table` の `compared_table_id` は起点にしない
- **D25・D26**: C6 の網羅の基準を「IR が書いた行と表」にした。これで C6 は IR だけで
  判定できるので、生成前の1か所(C4 の後)に戻した。C1 は「IR が言及しない中間判定の
  fact の不足」を捕まえる歯止めとして残る
- **D27**: `required_facts` の手順1で、この IR が `add_column` で足す列を加える
- **D28**: FEEL のセル -> 値の集合 の変換は `_cell_values()` 1つだけを通す。
  値を比べる処理(C5・C11・初期登録の検査)はすべてこれを使う
- **D29**: `new_output_values` が dict(出力名 -> 値)になったので、C9条件3・C11 は
  出力名ごとに突き合わせる(v1.2 は「その表のすべての出力名に足す」と緩く扱っていた)
- **D30**: `final.action = no_change`。IR 自身のルートも既存の最終判定のルートになる
- **D31**: C3 は `compound_fact_application.rejected_in_primitive` を見る
- **D33**: 層3の `sources` が空の fact は、バインディング設定に `sources_undefined` を付ける
- **D35**: 既存の表への行の挿入は、その表の `hit_policy` が FIRST のときだけ行う

## クロス検証の実行順序(設計書5章)

設計書5章は2段構えを定めている。

1. 生成前(IR と登録簿だけで判定できるもの): C3 → C9 → C4 → C6 → C10 → C11 → C2
2. 生成後(成果物を突合するもの): C1 → C5 → C7 → C8

C9 が破れていると `required_facts` の算出自体が始められないため、この順にする。
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .ir_schema import (
    BOOTSTRAP_ONLY_PRIMITIVES,
    PRIMITIVE_ARGS,
    ClauseIR,
    FactDef,
    RuleRowDraft,
)
from .registry import Registry, dmn_path

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

# 既存の DMN ファイルの置き場所(D15)。`registry/dmn/<dmn_model>.dmn`。
DMN_DIR_DEFAULT = "registry/dmn"

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
    """決定表のセルから、参照している値の一覧を取り出す。

    **FEEL のセルを値の集合に変換するのはこの関数だけである(D28)。** 層2のセルは
    DMN の表記のまま(`"高"` や `"中","未"`)、IR の行は素の値(`高`)を持つため、
    値を比べる処理(C5・C11・初期登録の検査)はすべてここを通して揃える。
    比べる場所ごとに揃え方を書くと、検証の間でずれる。

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


def _with_added_columns(ir: ClauseIR, entry: dict) -> list[str]:
    """層1の `input_columns` に、この IR が `add_column` で足す列を加える(D19)。"""
    columns = list(entry.get("input_columns", []))
    table_id = entry.get("decision_table_id")
    for change in ir.level_determination.intermediate:
        if change.action != "add_column" or change.table_id != table_id:
            continue
        for column in change.new_input_columns:
            if column not in columns:
                columns.append(column)
    return columns


def _resolve_decision_columns(
    ir: ClauseIR,
    registry: Registry,
    *,
    decision_id: str | None = None,
    decision_name: str | None = None,
) -> list[str]:
    """判定の入力列を、IRの新規決定表 -> 登録簿の層1 の順で探す。

    登録簿の層1から引く場合は、この IR が `add_column` で足す列も加える(D19)。
    加えないと、足した列が `required_facts` から漏れ、実行時に誰もその事実を
    集めない(第16回の報告 #6)。

    見つからない場合は `SchemaWiringError`(`KeyError` を外に出さない)。
    """
    if decision_id is not None:
        table = _ir_tables_by_decision_id(ir).get(decision_id)
        if table is not None:
            return list(table.input_columns)  # type: ignore[attr-defined]
        entry = registry.find_decision_table(decision_id=decision_id)
        if entry is not None:
            return _with_added_columns(ir, entry)
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
            return _with_added_columns(ir, entry)
        raise SchemaWiringError(
            f"判定 decision_name={decision_name!r} が IR の new_decision_tables にも"
            "登録簿の層1にも見つかりません。"
        )
    raise SchemaWiringError("内部エラー: decision_id か decision_name のどちらかが必要です。")


_MAX_DECISION_DEPTH = 50


def derive_required_facts(
    ir: ClauseIR, registry: Registry, *, entry_decision_id: str | None = None
) -> list[str]:
    """最終判定を評価するために実際に集める事実を求める(設計書4.1)。

    1. 起点(既定は `target.entry_decision_id`)の決定表の入力列から始める
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

    D16 の作り直しでは、IR の起点ではない既存の最終判定についても求めるため、
    `entry_decision_id` で起点を差し替えられるようにしている。
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
        ir, registry, decision_id=entry_decision_id or ir.target.entry_decision_id
    )
    expand(entry_columns, ())
    return required


def _changed_existing_tables(ir: ClauseIR, registry: Registry) -> list[dict]:
    """IR が行・列・出力値を足した「既存の」決定表の層1エントリ(宣言順・重複なし)。

    D16 の洗い出しの起点になる。IR の `new_decision_tables` にある表(新規に作る表)は
    既存ではないので除く。

    設計書4.1の起点は「rule_row_additions の表・intermediate の add_row と
    add_column の表・new_output_values を足した表・final.action が add_rows の表」
    である(D24)。次の2つは比べた相手で、表には何も足さないので起点にしない。

    - `final.action = new_table` のときの `compared_table_id`: 既存の表と列集合を
      比べた結果「新しい表を作る」と決めた相手であり、その表は変更しない。
      含めると、新しい表を作るだけの IR が既存の DMN ファイルを要求してしまう
    - `intermediate.action = "reuse_value"`: 既存の出力値をそのまま使うので、
      その表には何も足さない(ただし `new_output_values` があれば起点に入れる)
    """
    ir_table_ids = {t.decision_table_id for t in ir.new_decision_tables}
    table_ids: list[str] = []
    for row in ir.rule_row_additions:
        if row.decision_table_id not in table_ids:
            table_ids.append(row.decision_table_id)
    for change in ir.level_determination.intermediate:
        if change.action == "reuse_value" and not change.new_output_values:
            continue
        if change.table_id not in table_ids:
            table_ids.append(change.table_id)
    final = ir.level_determination.final
    if (
        final.action == "add_rows"
        and final.compared_table_id is not None
        and final.compared_table_id not in table_ids
    ):
        table_ids.append(final.compared_table_id)

    out: list[dict] = []
    for table_id in table_ids:
        if table_id in ir_table_ids:
            continue
        entry = registry.find_decision_table(decision_table_id=table_id)
        if entry is not None:
            out.append(entry)
    return out


def derive_routes(ir: ClauseIR, registry: Registry) -> list[dict]:
    """作り直すルートを洗い出す(D16・設計書4.1「影響を受ける最終判定の洗い出し」)。

    1. IR の起点(`target.entry_decision_id`)のルートは必ず作る。actor・action は
       IR のもの(`ir.actor` / `ir.action.normalized`)を使う
    2. IR が変更した既存の表を起点に、層1の `depends_on_decisions` を逆にたどる
    3. 集めた判定のうち `entry_points` を持つもの(= 最終判定)のルートも作り直す。
       actor・action は層1の `entry_points` から引く

    中間判定は `entry_points` が空なので、2で集まっても3で除かれる。これは設計の
    とおりで、エラーにはしない(設計書4.5 層1「中間判定は空」)。

    `required_facts` はルートごとに、その判定を起点として求める。

    `final.action = no_change`(D30)では、IR 自身のルートも既存の最終判定のルートで
    ある。そのため1で作ったルートと3で作り直すルートが同じになるが、actor・action・
    dmn_model・decision_id がすべて同じ行は1つにまとめるので、ルーティング表には
    1行だけ出る(設計書4.1)。C9条件8 が、IR の actor・action が起点の判定の
    `entry_points` にあることを確かめている。
    """
    routes: list[dict] = [
        {
            "actor": ir.actor,
            "action": ir.action.normalized,
            "dmn_model": ir.target.dmn_model,
            "decision_id": ir.target.entry_decision_id,
        }
    ]
    changed = _changed_existing_tables(ir, registry)
    if changed:
        origins = {
            e.get("decision_id") for e in changed if e.get("decision_id") is not None
        }
        for entry in list(changed) + registry.dependents_of(origins):
            for entry_point in entry.get("entry_points", []):
                route = {
                    "actor": entry_point.get("actor"),
                    "action": entry_point.get("action"),
                    "dmn_model": entry.get("dmn_model"),
                    "decision_id": entry.get("decision_id"),
                }
                # 同じ actor・action・decision_id の行は1つにまとめる(設計書4.1)。
                # no_change では IR 自身のルートがここで再び出てくる(D30)
                if not any(
                    r["actor"] == route["actor"]
                    and r["action"] == route["action"]
                    and r["decision_id"] == route["decision_id"]
                    for r in routes
                ):
                    routes.append(route)

    for route in routes:
        if route["dmn_model"] != ir.target.dmn_model:
            raise SchemaWiringError(
                f"D17違反: 作り直す最終判定 {route['decision_id']!r} は DMN モデル "
                f"{route['dmn_model']!r} にありますが、この IR の target.dmn_model は "
                f"{ir.target.dmn_model!r} です(モデル間 import は未対応。U9)。"
            )
        route["required_facts"] = derive_required_facts(
            ir, registry, entry_decision_id=route["decision_id"]
        )
    return routes


# --------------------------------------------------------------------------
# クロス検証(設計書5章)
# --------------------------------------------------------------------------


def _check_c3_compound_fact(ir: ClauseIR) -> None:
    """C3: 一致プリミティブ側で複合判定(AND)を組む案は採らない(D31)。

    v1.2 は `option_taken == "rejected_compound_in_primitive"` で判定していたが、
    D31 で `option_taken` が導出に変わったため、手で書く印
    (`rejected_in_primitive`)を見る。
    """
    if ir.compound_fact_application.rejected_in_primitive:
        raise SchemaWiringError(
            "C3検証失敗(複合fact禁止原則違反): 一致プリミティブ側で複合判定(AND)を組む案は"
            "採用できません。複合判定は決定表側の行として表現してください。"
            f"(rationale: {ir.compound_fact_application.rationale})"
        )


def _output_columns_of_table(
    ir: ClauseIR, registry: Registry, table_id: str
) -> list[str] | None:
    """決定表の出力列の名前を、IR の新規決定表 -> 層1の `output_columns` の順で引く。

    どちらにも無ければ `None` を返す(呼び出し側が「検証できない」として止める)。
    層2の行から集めて代用しない理由は D12 のとおり(行がまだ無い判定で誤って通る)。
    """
    for table in ir.new_decision_tables:
        if table.decision_table_id != table_id:
            continue
        names: list[str] = []
        for row in table.rule_rows:
            for name in row.outputs:
                if name not in names:
                    names.append(name)
        return names
    return registry.output_columns_of(table_id)


def _check_c9_level_determination(ir: ClauseIR, registry: Registry) -> None:
    """C9: レベル判定の整合(設計書3.7の8条件。条件7・8 は D30)。"""
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
    #        行がある。new_output_values の出力名はその表の output_columns にあり、
    #        値は追加した行の**同じ出力名の列**に現れる(D29)
    for change in lv.intermediate:
        if change.action != "add_row":
            continue
        rows = [r for r in ir.rule_row_additions if r.decision_table_id == change.table_id]
        if not rows:
            raise SchemaWiringError(
                f"C9検証失敗(条件3): intermediate.action=add_row ですが、rule_row_additions に"
                f"決定表 {change.table_id!r} の行がありません。"
            )
        if not change.new_output_values:
            continue
        columns = _output_columns_of_table(ir, registry, change.table_id)
        if columns is None:
            raise SchemaWiringError(
                f"C9検証失敗(条件3): 決定表 {change.table_id!r} の出力列が、IR にも"
                "層1の output_columns にも見つかりません(D12)。new_output_values の"
                "出力名を確かめられません。"
            )
        for name, values in change.new_output_values.items():
            if name not in columns:
                raise SchemaWiringError(
                    f"C9検証失敗(条件3): new_output_values の出力名 {name!r} が、決定表 "
                    f"{change.table_id!r} の出力列 {columns} にありません(D29)。"
                    "出力名を取り違えると、どの列にも足されない値ができます。"
                )
            produced: set[str] = set()
            for row in rows:
                if name not in row.outputs:
                    continue
                produced.update(
                    _cell_values(
                        str(row.outputs[name]),
                        where=f"決定表 {change.table_id} の行 {row.row_id} の出力 {name}",
                    )
                )
            missing_values = [v for v in values if v not in produced]
            if missing_values:
                raise SchemaWiringError(
                    f"C9検証失敗(条件3): new_output_values の {name!r} に足す値 "
                    f"{missing_values} が、決定表 {change.table_id!r} へ追加する行の "
                    f"{name!r} 列に現れません(その列の値: {sorted(produced)})。"
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

    # 条件6: intermediate の action が add_column なら、new_input_columns が空でなく、
    #        追加した列が rule_row_additions のその表の行の条件に現れる(D19)
    for change in lv.intermediate:
        if change.action != "add_column":
            continue
        if not change.new_input_columns:
            raise SchemaWiringError(
                f"C9検証失敗(条件6): intermediate.action=add_column ですが、決定表 "
                f"{change.table_id!r} の new_input_columns が空です。どの列を足すのかを"
                "明示してください(required_facts の算出から漏れます)。"
            )
        used = {
            column
            for row in ir.rule_row_additions
            if row.decision_table_id == change.table_id
            for column in row.conditions
        }
        missing_columns = [c for c in change.new_input_columns if c not in used]
        if missing_columns:
            raise SchemaWiringError(
                f"C9検証失敗(条件6): 追加する列 {missing_columns} が、決定表 "
                f"{change.table_id!r} へ追加する行の条件に現れません"
                f"(行の条件: {sorted(used)})。"
            )

    # 条件7: final.action が no_change なら、intermediate に add_row か add_column が
    #        1つ以上あり、compared_table_id は None で、target.entry_decision_id は
    #        層1に実在する最終判定(entry_points が空でない)である(D30)
    if lv.final.action == "no_change":
        if not any(c.action in ("add_row", "add_column") for c in lv.intermediate):
            raise SchemaWiringError(
                "C9検証失敗(条件7): final.action=no_change ですが、intermediate に"
                "add_row も add_column もありません。どの表も変えない IR は、"
                "4点セットに出すものがありません。"
            )
        if lv.final.compared_table_id is not None:
            raise SchemaWiringError(
                f"C9検証失敗(条件7): final.action=no_change ですが compared_table_id="
                f"{lv.final.compared_table_id!r} が入っています(最終判定は変えないので"
                "比べた相手は持ちません)。"
            )
        entry_table = registry.find_decision_table(decision_id=entry_id)
        if entry_table is None:
            raise SchemaWiringError(
                f"C9検証失敗(条件7): final.action=no_change の target.entry_decision_id="
                f"{entry_id!r} が登録簿の層1に実在しません(影響を受ける既存の最終判定を"
                "1つ指してください)。"
            )
        if not entry_table.get("entry_points"):
            raise SchemaWiringError(
                f"C9検証失敗(条件7): target.entry_decision_id={entry_id!r} の "
                "entry_points が空です(中間判定は最終判定ではないので、no_change の"
                "起点にできません)。"
            )

    # 条件8: final.action が add_rows か no_change なら、IR の actor と
    #        action.normalized の組が、起点の判定の entry_points にある(D30)
    if lv.final.action in ("add_rows", "no_change"):
        entry_table = registry.find_decision_table(decision_id=entry_id)
        points = list(entry_table.get("entry_points", [])) if entry_table else []
        if not any(
            p.get("actor") == ir.actor and p.get("action") == ir.action.normalized
            for p in points
        ):
            raise SchemaWiringError(
                f"C9検証失敗(条件8): IR の actor・action("
                f"{ir.actor!r} × {ir.action.normalized!r})が、起点の判定 {entry_id!r} の"
                f"entry_points {points} にありません。既存のルートに足す条文が、"
                "別の行為を名乗らないようにするための検査です。"
            )


def _check_c4_sources_match_primitive(ir: ClauseIR, registry: Registry) -> None:
    """C4: sources のキーが primitive の引数名と一致する(設計書3.5)。

    未知の primitive は、引数名の定義が無いため検証できない。`covered=False` で
    新しいプリミティブを宣言する経路があり得るが、その場合も③aの範囲外なので
    エラーとして止める(要報告事項)。
    """
    for fact in ir.facts.new_facts:
        _check_one_fact_sources(fact, in_new_facts=True)
    # 層3から引く既存 fact も、台帳が壊れていないか同じ基準で確かめる。
    # 初期登録分(identifiability_classifier)はここを通るため、in_new_facts=False。
    for variable in ir.facts.referenced_facts:
        entry = registry.find_fact(variable)
        if entry is None:
            continue  # 実在確認は C6 の責務
        _check_one_fact_sources(FactDef.from_json(entry), in_new_facts=False)


def _check_one_fact_sources(fact: FactDef, *, in_new_facts: bool) -> None:
    if in_new_facts and fact.primitive in BOOTSTRAP_ONLY_PRIMITIVES:
        raise SchemaWiringError(
            f"C4検証失敗: fact {fact.variable!r} の primitive={fact.primitive!r} は"
            "あおいの初期登録分(provenance.origin=bootstrap)でだけ使える境界事例です"
            "(D23)。新しい条文では、複合fact禁止原則に従って決定表側の列に分けて"
            "ください。"
        )
    expected = PRIMITIVE_ARGS.get(fact.primitive)
    if expected is None:
        raise SchemaWiringError(
            f"C4検証失敗: fact {fact.variable!r} の primitive={fact.primitive!r} は"
            f"未知です(既知: {sorted(PRIMITIVE_ARGS)})。引数名の定義が無いため"
            "sources を検証できません。"
        )
    actual = set(fact.sources.keys())
    if not actual and not in_new_facts:
        # 初期登録分は sources が未設定(元の DMN に情報が無く補っていない。
        # bootstrap_aoi.SOURCE_GAP_NOTE)。台帳の既存エントリは通す。
        return
    if actual != set(expected):
        raise SchemaWiringError(
            f"C4検証失敗: fact {fact.variable!r}(primitive={fact.primitive!r})の sources の"
            f"キー {sorted(actual)} が、プリミティブの引数名 {sorted(expected)} と"
            "一致しません。"
        )


def existing_facts_used_by_ir(ir: ClauseIR) -> set[str]:
    """「IR が使う既存 fact」を、IR が書いた行と表から求める(D25)。

    集める対象は3つ。

    1. `new_decision_tables` の `input_columns`
    2. `rule_row_additions` の条件のうち、値が「-」(ワイルドカード)でない列
    3. `intermediate` の `new_input_columns`(add_column で足す列)

    そこから、ドット参照の列(他の判定の出力であり fact ではない)と、この条文で
    新しく定義する fact(`new_facts`)を除く。

    基準を `required_facts`(ルート全体)にすると、既存の最終判定に1行足すだけの
    条文に、その判定が使う全 fact の宣言を強いることになる(第18回のレベル2の
    テストが7件を列挙していた)。条文が言及した fact だけを列挙させるため、
    IR が書いた行と表を基準にする。
    """
    columns: set[str] = set()
    for table in ir.new_decision_tables:
        columns.update(table.input_columns)
    for row in ir.rule_row_additions:
        for column, cell in row.conditions.items():
            if str(cell).strip() != "-":
                columns.add(column)
    for change in ir.level_determination.intermediate:
        columns.update(change.new_input_columns)
    new_names = {f.variable for f in ir.facts.new_facts}
    return {
        column
        for column in columns
        if not _is_dot_reference(column) and column not in new_names
    }


def _check_c6_referenced_facts(ir: ClauseIR, registry: Registry) -> None:
    """C6: 参照 fact の実在と網羅(D21・D25・D26)。

    1. `referenced_facts` と `value_additions` のキーが層3に実在する
    2. D25 の「IR が使う既存 fact」と `referenced_facts` が**一致する**
       (足りなくても余っても停止する)

    D25 により IR だけで判定できるので、生成前に1回だけ行う(D26)。v1.2 は
    `required_facts` を必要としたため、実在と網羅を2か所に分けていた。
    """
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

    used = existing_facts_used_by_ir(ir)
    declared = set(ir.facts.referenced_facts)
    missing = sorted(used - declared)
    if missing:
        raise SchemaWiringError(
            f"C6検証失敗(網羅): 既存 fact {missing} を IR の行・表で使っていますが、"
            "facts.referenced_facts に宣言されていません(D21: IR が使う既存 fact は"
            "漏れなく列挙する)。"
        )
    extra = sorted(declared - used)
    if extra:
        raise SchemaWiringError(
            f"C6検証失敗(余り): facts.referenced_facts の {extra} を、IR のどの行・表も"
            "使っていません(D25: 条文が言及した fact だけを列挙する)。"
            f"IR が使う既存 fact は {sorted(used)} です。"
        )


def _check_c10_dot_references(ir: ClauseIR, registry: Registry) -> None:
    """C10: 「判定名.出力名」の判定が実在し、その出力名を持つ。

    接頭辞は decision の名前。出力名は、IR の新規決定表なら行の outputs のキー、
    登録簿のみに存在する判定なら**層1の output_columns**(D12)で確かめる。
    v1.1 は層2の行から集めて代用していたが、行がまだ無い判定で誤って通るため
    層1を見る形に置き換えた。
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
        table_id = entry.get("decision_table_id", "")
        columns = registry.output_columns_of(table_id)
        if columns is None:
            raise SchemaWiringError(
                f"C10検証失敗: ドット参照 {ref!r} の判定 {name!r}(決定表 {table_id!r})の"
                "層1エントリに output_columns がありません(D12)。出力列の一覧が無いと"
                "出力名を確かめられません。"
            )
        if output not in columns:
            raise SchemaWiringError(
                f"C10検証失敗: ドット参照 {ref!r} の判定 {name!r} に出力 {output!r} が"
                f"ありません(層1の output_columns: {columns})。"
            )


def _dot_reference_rows(ir: ClauseIR) -> list[tuple[str, RuleRowDraft]]:
    """決定表の行を「どこの行か」の説明付きで集める(C5・C11 共用)。"""
    rows: list[tuple[str, RuleRowDraft]] = [
        (f"決定表 {row.decision_table_id} の行 {row.row_id}", row)
        for row in ir.rule_row_additions
    ]
    for table in ir.new_decision_tables:
        for row in table.rule_rows:
            rows.append((f"決定表 {table.decision_table_id} の行 {row.row_id}", row))
    return rows


def _produced_output_values(
    ir: ClauseIR, registry: Registry, *, decision_name: str
) -> tuple[dict[str, set[str]], str]:
    """判定が出しうる出力値を、出力名ごとに集める(C11用。設計書5章 C11)。

    集める先は3つ。

    1. 登録簿の層2の行(既存の判定)
    2. IR の新規決定表の行・`rule_row_additions` の行
    3. `intermediate` の `new_output_values`(**出力名ごと**。D29)

    3は v1.2 では「その表のすべての出力名に足す」と緩く扱っていたが、D29 で
    `new_output_values` が dict(出力名 -> 値)になったので、宣言された出力名の分だけ
    足す。緩いままだと、identity_result に足した値を identity_level の参照で使う
    取り違え(あおいの「未」と同じ形)を C11 が見逃す。

    集めた値は `_cell_values()` で FEEL の表記を外して素の値に揃える(D28)。
    登録簿の層2は DMN の表記のまま(`"高"`)入っているのに対し、IR の行は素の値
    (`高`)を持つため、揃えないと同じ値が別物に見える。

    戻り値の2つめは、どこから集めたかの説明(エラーメッセージ用)。
    """
    values: dict[str, set[str]] = {}
    sources: list[str] = []

    def add(name: str, cell: str) -> None:
        for value in _cell_values(str(cell), where=f"判定 {decision_name} の出力 {name}"):
            values.setdefault(name, set()).add(value)

    ir_table = _ir_tables_by_decision_name(ir).get(decision_name)
    table_id = None
    if ir_table is not None:
        table_id = ir_table.decision_table_id  # type: ignore[attr-defined]
        for row in ir_table.rule_rows:  # type: ignore[attr-defined]
            for name, value in row.outputs.items():
                add(name, value)
        sources.append("IR の新規決定表の行")
    else:
        entry = registry.find_decision_table(decision_name=decision_name)
        if entry is not None:
            table_id = entry.get("decision_table_id")
            for name, vals in registry.output_values_of(table_id or "").items():
                for value in vals:
                    add(name, value)
            sources.append("登録簿の層2の行")

    if table_id is not None:
        added = False
        for row in ir.rule_row_additions:
            if row.decision_table_id != table_id:
                continue
            added = True
            for name, value in row.outputs.items():
                add(name, value)
        if added:
            sources.append("IR の rule_row_additions")
        for change in ir.level_determination.intermediate:
            if change.table_id != table_id or not change.new_output_values:
                continue
            for name, added_values in change.new_output_values.items():
                for value in added_values:
                    add(name, value)
            sources.append("intermediate の new_output_values")

    return values, "／".join(dict.fromkeys(sources)) or "(出力値の情報なし)"


def _check_c11_dot_reference_values(ir: ClauseIR, registry: Registry) -> None:
    """C11: ドット参照の列の条件値が、参照先の判定の出力値にある(D13)。

    C5(fact の値)の中間判定版。綴りの違いや中間判定への行の追加し忘れで、
    実行時に絶対に当たらない行ができるのを防ぐ。
    """
    for where, row in _dot_reference_rows(ir):
        for column, cell in row.conditions.items():
            if not _is_dot_reference(column):
                continue
            values = _cell_values(cell, where=f"{where} の列 {column}")
            if not values:
                continue
            name, output = _dot_prefix(column), _dot_suffix(column)
            produced, origin = _produced_output_values(
                ir, registry, decision_name=name
            )
            allowed = produced.get(output, set())
            unknown = [v for v in values if v not in allowed]
            if unknown:
                raise SchemaWiringError(
                    f"C11検証失敗: {where} の列 {column!r} が参照する値 {unknown} を、"
                    f"判定 {name!r} は出力しません(出力値: {sorted(allowed)}／"
                    f"出典: {origin})。この行は実行時に一度もヒットしません。"
                )


def _check_d17_same_dmn_model(ir: ClauseIR, registry: Registry) -> None:
    """D17: 中間判定を参照する最終判定は、参照先と同じ DMN モデルに入れる。

    DMN のモデル間 import は当面使わない(U9)ため、別モデルの判定をドット参照
    していたら停止する。既存の表に行を足す場合、その表も同じモデルでなければ
    `registry/dmn/<dmn_model>.dmn` への挿入ができない。
    """
    model = ir.target.dmn_model
    checked: list[tuple[str, str]] = []
    for ref in sorted({k for k in ir.all_fact_keys() if _is_dot_reference(k)}):
        checked.append(("ドット参照", _dot_prefix(ref)))
    for table in _changed_existing_tables(ir, registry):
        checked.append(("行・列を足す既存の表", table.get("decision_name", "")))
    for kind, name in checked:
        if not name or _ir_tables_by_decision_name(ir).get(name) is not None:
            continue
        entry = registry.find_decision_table(decision_name=name)
        if entry is None:
            continue  # 実在確認は C9条件4・C10 の責務
        other = entry.get("dmn_model")
        if other is not None and other != model:
            raise SchemaWiringError(
                f"D17違反: {kind} の判定 {name!r} は DMN モデル {other!r} にありますが、"
                f"この IR の target.dmn_model は {model!r} です。中間判定を参照する"
                "最終判定は同じモデルに入れてください(モデル間 import は未対応。U9)。"
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
    fact ではない(他の判定の出力)ため対象外で、その値は C11 が確かめる(D13)。
    """
    for where, row in _dot_reference_rows(ir):
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
            allowed = set(fact.possible_values) | {
                a.value for a in ir.facts.value_additions.get(column, [])
            }
            unknown = [v for v in values if v not in allowed]
            if unknown:
                raise SchemaWiringError(
                    f"C5検証失敗: {where} の列 {column!r} が参照する値 {unknown} が、"
                    f"その fact の possible_values {sorted(allowed)} にありません"
                    "(この行は実行時に一度もヒットしません)。"
                )


def check_registry_cell_values(registry: Registry) -> None:
    """登録簿そのものに C5・C11 を当てる(初期登録の検査用。D34)。

    初期登録は IR を通らずに層1〜層3を作るので、`generate_wiring_bundle` の
    クロス検証を受けない。元の DMN に矛盾があればそのまま台帳に入ってしまい、
    あとで正解 IR がその値を参照するたびに C11 に当たる。そこで、登録簿の層2の行を
    IR の行と同じ基準で確かめる。

    - **C5 相当**: fact の列(ドットを含まない)の条件値が、その fact の層3の
      `possible_values` にある
    - **C11 相当**: 「判定名.出力名」の列の条件値が、参照先の判定の出力値にある

    値の比べ方は D28 のとおり `_cell_values()` を通す。違反があれば
    `SchemaWiringError` で停止する。
    """
    by_name = {
        e.get("decision_name"): e for e in registry.decision_tables if e.get("decision_name")
    }
    for row in registry.rule_rows:
        table_id = row.get("decision_table_id", "")
        where = f"登録簿の決定表 {table_id} の行 {row.get('row_id')}"
        for column, cell in (row.get("conditions") or {}).items():
            values = _cell_values(str(cell), where=f"{where} の列 {column}")
            if not values:
                continue
            if not _is_dot_reference(column):
                fact = registry.find_fact(column)
                if fact is None:
                    raise SchemaWiringError(
                        f"C5検証失敗(登録簿): {where} が参照する列 {column!r} の fact が"
                        "層3(fact台帳)にありません。"
                    )
                allowed = set(fact.get("possible_values", []))
                unknown = [v for v in values if v not in allowed]
                if unknown:
                    raise SchemaWiringError(
                        f"C5検証失敗(登録簿): {where} の列 {column!r} が参照する値 "
                        f"{unknown} が、その fact の possible_values {sorted(allowed)} に"
                        "ありません(この行は実行時に一度もヒットしません)。"
                    )
                continue
            name, output = _dot_prefix(column), _dot_suffix(column)
            entry = by_name.get(name)
            if entry is None:
                raise SchemaWiringError(
                    f"C11検証失敗(登録簿): {where} のドット参照 {column!r} の判定 "
                    f"{name!r} が層1にありません。"
                )
            columns = entry.get("output_columns") or []
            if output not in columns:
                raise SchemaWiringError(
                    f"C11検証失敗(登録簿): {where} のドット参照 {column!r} の出力 "
                    f"{output!r} が、判定 {name!r} の output_columns {columns} に"
                    "ありません。"
                )
            produced: set[str] = set()
            for ref_row in registry.rows_of(entry.get("decision_table_id", "")):
                outputs = ref_row.get("outputs") or {}
                if output in outputs:
                    produced.update(
                        _cell_values(
                            str(outputs[output]),
                            where=f"判定 {name} の出力 {output}",
                        )
                    )
                elif output in ref_row:
                    produced.update(
                        _cell_values(
                            str(ref_row[output]),
                            where=f"判定 {name} の出力 {output}",
                        )
                    )
            unknown = [v for v in values if v not in produced]
            if unknown:
                raise SchemaWiringError(
                    f"C11検証失敗(登録簿): {where} の列 {column!r} が参照する値 "
                    f"{unknown} を、判定 {name!r} は出力しません"
                    f"(出力値: {sorted(produced)})。この行は実行時に一度もヒットしません。"
                )


def _check_c7_target_exists(ir: ClauseIR, dmn_xml: str, updated: Registry) -> None:
    """C7: target.entry_decision_id が DMN XML と層1に実在する。

    v1.2 では `add_rows`(レベル2)でも既存モデルへ行を挿入して DMN XML を出力する
    ようになったため(D15)、DMN XML 側の確認を `new_table` に限らず常に行う。
    """
    entry_id = ir.target.entry_decision_id
    if updated.find_decision_table(decision_id=entry_id) is None:
        raise SchemaWiringError(
            f"C7検証失敗: target.entry_decision_id={entry_id!r} が更新後の登録簿の層1に"
            "実在しません。"
        )
    if f'id="{entry_id}"' not in dmn_xml:
        raise SchemaWiringError(
            f"C7検証失敗: target.entry_decision_id={entry_id!r} の decision が"
            "出力した DMN XML に存在しません"
            f"(final.action={ir.level_determination.final.action})。"
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
    _register_namespaces()

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

    return _serialize(definitions)


# --------------------------------------------------------------------------
# 既存の DMN モデルへの行の挿入・列の追加(D15・D19)
# --------------------------------------------------------------------------


def _register_namespaces() -> None:
    ET.register_namespace("", DMN_NS)
    ET.register_namespace("dmndi", DMNDI_NS)
    ET.register_namespace("dc", DC_NS)
    ET.register_namespace("feel", FEEL_NS)
    ET.register_namespace("kie", KIE_NS)
    ET.register_namespace("di", DI_NS)


def _serialize(definitions: ET.Element) -> str:
    ET.indent(definitions, space="  ")
    xml_body = ET.tostring(definitions, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8" ?>\n{xml_body}\n'


def _tag(name: str) -> str:
    return f"{{{DMN_NS}}}{name}"


def _decision_tables_in(definitions: ET.Element) -> dict[str, tuple[ET.Element, ET.Element]]:
    """DMN XML の decisionTable を、決定表ID -> (decision, decisionTable) で引く。"""
    out: dict[str, tuple[ET.Element, ET.Element]] = {}
    for decision in definitions.findall(_tag("decision")):
        table = decision.find(_tag("decisionTable"))
        if table is not None and table.get("id"):
            out[table.get("id")] = (decision, table)
    return out


def _table_input_columns(table: ET.Element) -> list[str]:
    columns = []
    for inp in table.findall(_tag("input")):
        text = inp.find(f"{_tag('inputExpression')}/{_tag('text')}")
        columns.append("" if text is None or text.text is None else text.text.strip())
    return columns


def _table_output_names(table: ET.Element) -> list[str]:
    return [o.get("name") or "" for o in table.findall(_tag("output"))]


def _insert_after(parent: ET.Element, anchors: list[ET.Element], element: ET.Element) -> None:
    """`anchors` の最後の要素の直後に挿入する(無ければ先頭)。"""
    if anchors:
        parent.insert(list(parent).index(anchors[-1]) + 1, element)
    else:
        parent.insert(0, element)


def _add_input_column(
    definitions: ET.Element, decision: ET.Element, table: ET.Element, column: str
) -> None:
    """既存の決定表に入力列を1つ足し、既存の全 rule のその列を「-」で埋める(D19)。"""
    if column in _table_input_columns(table):
        raise SchemaWiringError(
            f"列の追加に失敗: 決定表 {table.get('id')!r} には列 {column!r} が既にあります"
            "(add_column で足す列は、既存の列と重なってはいけません)。"
        )

    inp = ET.Element(
        _tag("input"), {"id": f"in_{column.replace('.', '_')}", "label": column}
    )
    expr = ET.SubElement(
        inp, _tag("inputExpression"), {"id": _uid("ie"), "typeRef": "string"}
    )
    ET.SubElement(expr, _tag("text")).text = column
    _insert_after(table, table.findall(_tag("input")), inp)

    # 既存の全 rule の末尾の inputEntry のあとに「-」を1つ足す
    for rule in table.findall(_tag("rule")):
        entry = ET.Element(_tag("inputEntry"), {"id": _uid("in")})
        ET.SubElement(entry, _tag("text")).text = "-"
        _insert_after(rule, rule.findall(_tag("inputEntry")), entry)

    if _is_dot_reference(column):
        # 他の判定の出力を足す場合は inputData ではなく requiredDecision になるが、
        # 中間判定に他の判定を足す経路は今回の受け入れ範囲外なので止める。
        raise SchemaWiringError(
            f"列の追加に失敗: ドット参照の列 {column!r} の追加(判定の依存を増やす)は"
            "未対応です。add_column では fact の列だけを足してください。"
        )

    requirement = ET.Element(_tag("informationRequirement"), {"id": _uid("ir")})
    ET.SubElement(requirement, _tag("requiredInput"), {"href": f"#id_{column}"})
    existing = decision.findall(_tag("informationRequirement"))
    if existing:
        _insert_after(decision, existing, requirement)
    else:
        _insert_after(decision, decision.findall(_tag("variable")), requirement)

    if not any(
        i.get("name") == column for i in definitions.findall(_tag("inputData"))
    ):
        data = ET.SubElement(
            definitions, _tag("inputData"), {"id": f"id_{column}", "name": column}
        )
        ET.SubElement(
            data,
            _tag("variable"),
            {"id": f"var_id_{column}", "name": column, "typeRef": "string"},
        )


def _insert_rule(table: ET.Element, row: RuleRowDraft) -> None:
    """既存の決定表に rule を1本、`position` の位置に挿入する(D15)。

    FIRST ヒットポリシーでは行の順序が意味を持つため、位置を守って挿入する。
    `position` が既存の行数を超える場合は末尾に足す。
    """
    policy = (table.get("hitPolicy") or "UNIQUE").upper()
    if policy != "FIRST":
        raise SchemaWiringError(
            f"D35違反: 決定表 {table.get('id')!r} の hit_policy は {policy!r} です。"
            "既存の表への行の挿入は FIRST の表だけに行います(position による挿入は"
            "FIRST でしか意味を持たないため)。"
        )
    existing = table.findall(_tag("rule"))
    if any(r.get("id") == row.row_id for r in existing):
        raise SchemaWiringError(
            f"行の挿入に失敗: 決定表 {table.get('id')!r} には行 {row.row_id!r} が"
            "既にあります。"
        )
    input_columns = _table_input_columns(table)
    unknown = [c for c in row.conditions if c not in input_columns]
    if unknown:
        raise SchemaWiringError(
            f"行の挿入に失敗: 行 {row.row_id!r} の条件の列 {sorted(unknown)} が、"
            f"決定表 {table.get('id')!r} の入力列 {input_columns} にありません"
            "(綴り違いの列は「-」として黙って無視されるため停止します)。"
        )

    rule = ET.Element(_tag("rule"), {"id": row.row_id})
    for column in input_columns:
        entry = ET.SubElement(rule, _tag("inputEntry"), {"id": _uid("in")})
        ET.SubElement(entry, _tag("text")).text = _feel_literal(
            row.conditions.get(column, "-")
        )
    for name in _table_output_names(table):
        entry = ET.SubElement(rule, _tag("outputEntry"), {"id": _uid("out")})
        ET.SubElement(entry, _tag("text")).text = _feel_literal(
            row.outputs.get(name, "-")
        )

    index = row.position - 1
    if 0 <= index < len(existing):
        table.insert(list(table).index(existing[index]), rule)
    elif existing:
        table.insert(list(table).index(existing[-1]) + 1, rule)
    else:
        table.append(rule)


def patch_existing_dmn(ir: ClauseIR, registry: Registry, *, dmn_dir: str | Path) -> str:
    """`registry/dmn/<dmn_model>.dmn` を読み込み、IR の変更を反映した XML を返す(D15)。

    反映する順序は、列の追加(D19) -> 行の挿入(D15) -> 新規の表の追加(D17)。
    列を先に足すのは、挿入する行がその列の値を持つためである。

    元のファイルは書き換えない(出力は `out/` に書き出す。永続台帳への反映は
    人間レビューを経た別操作。設計書4章)。
    """
    path = dmn_path(ir.target.dmn_model, dmn_dir)
    if not path.exists():
        raise SchemaWiringError(
            f"既存モデルへの反映に失敗: DMN ファイル {path} がありません。"
            f"既存の表に行・列を足す IR では、registry/dmn/{ir.target.dmn_model}.dmn が"
            "必要です(D15)。"
        )
    _register_namespaces()
    definitions = ET.parse(str(path)).getroot()
    tables = _decision_tables_in(definitions)

    # 1) 列の追加(D19)
    for change in ir.level_determination.intermediate:
        if change.action != "add_column":
            continue
        found = tables.get(change.table_id)
        if found is None:
            raise SchemaWiringError(
                f"列の追加に失敗: 決定表 {change.table_id!r} が DMN ファイル {path} に"
                "ありません。"
            )
        decision, table = found
        for column in change.new_input_columns:
            _add_input_column(definitions, decision, table, column)

    # 2) 行の挿入(D15)。同じ表の複数行は position の昇順で入れる
    ir_table_ids = {t.decision_table_id for t in ir.new_decision_tables}
    by_table: dict[str, list[RuleRowDraft]] = {}
    for row in ir.rule_row_additions:
        if row.decision_table_id in ir_table_ids:
            continue  # 新規に作る表の行は render_dmn_xml 側で出力される
        by_table.setdefault(row.decision_table_id, []).append(row)
    for table_id, rows in by_table.items():
        found = tables.get(table_id)
        if found is None:
            raise SchemaWiringError(
                f"行の挿入に失敗: 決定表 {table_id!r} が DMN ファイル {path} に"
                "ありません(新規に作る表なら new_decision_tables に入れてください)。"
            )
        for row in sorted(rows, key=lambda r: r.position):
            _insert_rule(found[1], row)

    # 3) 新規の表を同じモデルに足す(D17)。render_dmn_xml の出力から decision・
    #    inputData・knowledgeSource を移し、既にある id は足さない
    if ir.new_decision_tables:
        model = ir.target.dmn_model
        rendered = ET.fromstring(
            render_dmn_xml(
                ir,
                registry,
                definitions_id=f"{model}_definitions",
                definitions_name=model,
                namespace=f"{DMN_NAMESPACE_BASE}/{model}",
            )
        )
        existing_ids = {e.get("id") for e in definitions.iter() if e.get("id")}
        for child in list(rendered):
            if child.get("id") in existing_ids:
                continue
            definitions.append(child)

    return _serialize(definitions)


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

    対象は、作り直すすべてのルートの required_facts の和集合(D16)。`value_additions`
    があれば possible_values に加える。設計書4.3が `dmn_model` を出力項目に挙げている
    ため、{dmn_model, facts} のエンベロープにしている(v1 は裸の配列)。

    `sources` が空の fact には `sources_undefined: true` を付ける(D33)。初期登録で
    取得元が分からなかった fact(あおいの7件)がこれに当たる。空のまま出して隠さない
    ことで、一致プリミティブの実装(U4)のときに埋める対象が見える。新しい条文の
    fact は C4 が空の sources を通さないので、ここに来るのは層3から引いた分だけ。
    """
    facts_json = []
    for variable in required_facts:
        fact = fact_defs[variable]
        payload = fact.to_json()
        if not payload["sources"]:
            payload["sources_undefined"] = True
        additions = ir.facts.value_additions.get(variable)
        if additions:
            merged = list(payload["possible_values"])
            for addition in additions:
                if addition.value not in merged:
                    merged.append(addition.value)
            payload["possible_values"] = merged
        facts_json.append(payload)
    return {"dmn_model": ir.target.dmn_model, "facts": facts_json}


def _build_routing_table(routes: list[dict]) -> dict:
    """ルーティング表(設計書4.4)。

    v1 の `actor_action` の入れ子と `input_columns` は廃止し、actor・action・
    dmn_model・decision_id・required_facts のフラットな1行にする(D8)。

    D16 で複数の最終判定を作り直す場合も、**1つのファイルに複数の行**として出す
    (ファイルは分けない)。ルーティング表は「actor×action から判定を引く1枚の表」
    であり、分割すると引く側が全ファイルを読む必要が出るため。先頭は必ず IR の
    起点のルートで、以降は D16 で洗い出した既存の最終判定のルートが続く。
    """
    return {
        "routes": [
            {
                "actor": route["actor"],
                "action": route["action"],
                "dmn_model": route["dmn_model"],
                "decision_id": route["decision_id"],
                "required_facts": list(route["required_facts"]),
            }
            for route in routes
        ]
    }


def _build_updated_registry(
    ir: ClauseIR, registry: Registry, fact_defs: dict[str, FactDef]
) -> Registry:
    """入力登録簿にこの条文の内容をマージした、更新後の登録簿を作る(設計書4.5)。"""
    updated = registry.copy()
    provenance = ir.provenance.to_json()

    # 層1: 新しい決定表のメタ情報(dmn_model は D8、output_columns は D12、
    # entry_points は D16)
    used_by: dict[str, list[str]] = {}
    for table in ir.new_decision_tables:
        output_columns: list[str] = []
        for row in table.rule_rows:
            for name in row.outputs:
                if name not in output_columns:
                    output_columns.append(name)
        # entry_points は最終判定(= この IR の起点)だけに入れる。中間判定は空
        is_entry = table.decision_id == ir.target.entry_decision_id
        updated.decision_tables.append(
            {
                "decision_table_id": table.decision_table_id,
                "decision_id": table.decision_id,
                "decision_name": table.decision_name,
                "dmn_model": ir.target.dmn_model,
                "entry_points": (
                    [{"actor": ir.actor, "action": ir.action.normalized}]
                    if is_entry
                    else []
                ),
                "hit_policy": "FIRST",
                "input_columns": list(table.input_columns),
                "output_columns": output_columns,
                "depends_on_decisions": list(table.depends_on_decisions),
                "depends_on_inputs": list(table.depends_on_inputs),
                "knowledge_sources": list(table.knowledge_sources),
                "provenance": provenance,
            }
        )
        for col in table.input_columns:
            if not _is_dot_reference(col):
                used_by.setdefault(col, []).append(table.decision_table_id)

    # 層1: 既存の表に足した列を input_columns に反映し、既存の行を「-」で埋める(D19)
    for change in ir.level_determination.intermediate:
        if change.action != "add_column" or not change.new_input_columns:
            continue
        entry = updated.find_decision_table(decision_table_id=change.table_id)
        if entry is None:
            continue  # 実在確認は C9条件4 の責務
        columns = list(entry.get("input_columns", []))
        for column in change.new_input_columns:
            if column not in columns:
                columns.append(column)
            used_by.setdefault(column, []).append(change.table_id)
        entry["input_columns"] = columns
        for row in updated.rows_of(change.table_id):
            conditions = dict(row.get("conditions", {}))
            for column in change.new_input_columns:
                conditions.setdefault(column, "-")
            row["conditions"] = conditions
        history = list(entry.get("history", []))
        history.append(
            {
                "change": "column_addition",
                "added_columns": list(change.new_input_columns),
                "provenance": provenance,
            }
        )
        entry["history"] = history

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
        added = [a for a in additions if a.value not in values]
        entry["possible_values"] = values + [a.value for a in added]
        if added:
            history = list(entry.get("history", []))
            history.append(
                {
                    "change": "value_addition",
                    # 値ごとに承認者を残す(D14)。あとから承認した場合の追跡に使う
                    "added_values": [a.to_json() for a in added],
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


def generate_wiring_bundle(
    ir: ClauseIR, *, registry: Registry, dmn_dir: str | Path = DMN_DIR_DEFAULT
) -> WiringBundle:
    """IRと登録簿を入力として4点セットを1回の呼び出しで生成する。

    クロス検証に失敗すると `SchemaWiringError` で停止する(設計書5章 C1〜C11)。

    `dmn_dir` は既存の DMN ファイルの置き場所(D15)。既存の表に行・列を足す IR の
    ときだけ読み込む。新しい表を作るだけの IR(ゴールデンパス)では使わない。
    """
    # --- 生成前の検証(IRと登録簿だけで判定できるもの。設計書5章) ---
    _check_c3_compound_fact(ir)
    _check_c9_level_determination(ir, registry)
    _check_c4_sources_match_primitive(ir, registry)
    _check_c6_referenced_facts(ir, registry)
    _check_c10_dot_references(ir, registry)
    _check_c11_dot_reference_values(ir, registry)
    _check_c2_new_facts_used(ir)
    _check_d17_same_dmn_model(ir, registry)

    # --- 作り直すルートと required_facts の導出(設計書4.1・D16) ---
    routes = derive_routes(ir, registry)
    # バインディング設定は全ルートの和集合(設計書4.3)。順序は起点のルートから
    # 現れた順にして決定的にする
    required_facts: list[str] = []
    for route in routes:
        for variable in route["required_facts"]:
            if variable not in required_facts:
                required_facts.append(variable)
    fact_defs = _collect_fact_defs(ir, registry, required_facts)
    # C1 は作り直すすべてのルートの required_facts の和集合を見る(D26)。C6 が
    # IR だけで判定できるようになった(D25)ので、IR が言及しない中間判定の fact の
    # 不足を捕まえるのは C1 だけになった
    _check_c1_facts_available(required_facts, ir, registry)

    # --- 4点セットの生成 ---
    _check_c5_cell_values(ir, registry, fact_defs)

    dmn_model = ir.target.dmn_model
    if _needs_existing_dmn(ir, registry):
        # 既存モデルへの行の挿入・列の追加(D15・D19)
        dmn_xml = patch_existing_dmn(ir, registry, dmn_dir=dmn_dir)
    else:
        dmn_xml = render_dmn_xml(
            ir,
            registry,
            definitions_id=f"{dmn_model}_definitions",
            definitions_name=dmn_model,
            namespace=f"{DMN_NAMESPACE_BASE}/{dmn_model}",
        )
    bindings = _build_bindings(ir, required_facts, fact_defs)
    routing_table = _build_routing_table(routes)
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


def _needs_existing_dmn(ir: ClauseIR, registry: Registry) -> bool:
    """既存の DMN ファイルを読み込む必要があるか(D15)。

    IR が既存の表に行・列を足しているなら読み込む。新しい表を作るだけなら、
    既存ファイルは不要で、完結した1つの DMN を生成する。
    """
    return bool(_changed_existing_tables(ir, registry))
