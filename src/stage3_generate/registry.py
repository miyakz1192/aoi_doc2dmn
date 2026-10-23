"""Scope/Decision 登録簿(層1・層2・層3・review)。

決定表とルール行を「どの条文に由来し、誰がいつ登録し、人間レビューはどうなっているか」と
共に記録する台帳。v2 では配線ジェネレータの**入力**でもある(既存の決定表と fact 台帳を
参照してレベル判定と fact の再利用を判断する。設計書4章冒頭)。

仕様: docs/design/aoi_doc2dmn_IRv2_design.md 4.5節。

| 層 | 内容 | v1 からの変更 |
|---|---|---|
| 層1 `decision_tables` | 決定表ごとのメタ情報 | `dmn_model`(D8)・`output_columns`(D12)・`entry_points`(D16) を追加 |
| 層2 `rule_rows` | ルール行1本ずつの台帳 | なし |
| 層3 `fact_registry` | fact 台帳 | **新設**(D2) |
| `review` | 条文単位のレビュー状態 | `gates`(ゲート名の一覧)を追加 |

## 永続化の方針

入力(永続台帳)は `registry/registry.json` に置き、リポジトリにコミットする。
配線ジェネレータが返す**更新後**の登録簿は `out/` に書き出し、永続台帳への反映
(promotion)は人間レビューを経た別操作とする(今回の範囲外)。

## 層1の出力列と呼び出し口(v1.2 で追加)

- `output_columns`(D12): 出力列の**名前**の一覧。行がまだ無い判定でも出力列が分かる。
  C10 はここを見る(v1.1 は層2の行から集めて代用していた)
- `entry_points`(D16): その最終判定を呼ぶ `{actor, action}` の組の一覧。中間判定は空。
  既存の表に行を足したとき、それを使う最終判定のルーティング表の行を作り直すのに使う

**出力の値**は層1に持たない(D12)。`possible_values` を「表に出てくる値だけ」にした D4 と
同じ考え方で、値は層2の行の `outputs` から集める(`output_values_of()`。C11用)。

## DMN ファイルの置き場所

既存モデルへの行挿入(D15)のため、DMN ファイルは `registry/dmn/<dmn_model>.dmn` に置く。
パスの解決は `dmn_path()` で行う。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Registry:
    """登録簿の全体。層1・層2・層3・review を保持する。"""

    decision_tables: list[dict] = field(default_factory=list)
    rule_rows: list[dict] = field(default_factory=list)
    fact_registry: list[dict] = field(default_factory=list)
    review: Optional[dict] = None

    # ---- 生成・入出力 ----

    @classmethod
    def empty(cls) -> "Registry":
        return cls()

    @classmethod
    def from_json(cls, payload: dict) -> "Registry":
        return cls(
            decision_tables=list(payload.get("decision_tables", [])),
            rule_rows=list(payload.get("rule_rows", [])),
            fact_registry=list(payload.get("fact_registry", [])),
            review=payload.get("review"),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        """JSON ファイルから読み込む。ファイルが無ければ空の登録簿を返す。"""
        p = Path(path)
        if not p.exists():
            return cls.empty()
        with p.open(encoding="utf-8") as f:
            return cls.from_json(json.load(f))

    def to_json(self) -> dict:
        out: dict = {
            "decision_tables": self.decision_tables,
            "rule_rows": self.rule_rows,
            "fact_registry": self.fact_registry,
        }
        if self.review is not None:
            out["review"] = self.review
        return out

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return p

    def copy(self) -> "Registry":
        """浅いコピーではなく、リストを作り直した独立のインスタンスを返す。

        配線ジェネレータは入力登録簿を壊さずに更新後の登録簿を作るため、これを使う。
        """
        return Registry(
            decision_tables=[dict(d) for d in self.decision_tables],
            rule_rows=[dict(r) for r in self.rule_rows],
            fact_registry=[dict(f) for f in self.fact_registry],
            review=dict(self.review) if self.review else None,
        )

    # ---- 検索 ----

    def find_decision_table(
        self,
        *,
        decision_id: Optional[str] = None,
        decision_name: Optional[str] = None,
        decision_table_id: Optional[str] = None,
    ) -> Optional[dict]:
        """層1のエントリを3つのキーのいずれかで引く。

        決定ID・決定名・決定表IDは別の識別子空間であり、設計書3.7条件2は
        `compared_table_id`(決定表ID)から `entry_decision_id`(決定ID)への解決を
        暗黙に要求するため、どのキーでも引けるようにしている。
        """
        for entry in self.decision_tables:
            if decision_id is not None and entry.get("decision_id") == decision_id:
                return entry
            if decision_name is not None and entry.get("decision_name") == decision_name:
                return entry
            if (
                decision_table_id is not None
                and entry.get("decision_table_id") == decision_table_id
            ):
                return entry
        return None

    def find_fact(self, variable: str) -> Optional[dict]:
        """層3のエントリを fact 名で引く。"""
        for entry in self.fact_registry:
            if entry.get("variable") == variable:
                return entry
        return None

    def rows_of(self, decision_table_id: str) -> list[dict]:
        """層2から、指定した決定表に属する行を集める。"""
        return [r for r in self.rule_rows if r.get("decision_table_id") == decision_table_id]

    def output_columns_of(self, decision_table_id: str) -> Optional[list[str]]:
        """層1の `output_columns` を引く(D12。C10用)。

        層1にエントリが無い、またはエントリに `output_columns` が無い場合は `None` を
        返す。呼び出し側(C10)はこれを「検証できない」として `SchemaWiringError` にする。
        値を層2から推測して代用すると、行がまだ無い判定で誤って通してしまう。
        """
        entry = self.find_decision_table(decision_table_id=decision_table_id)
        if entry is None:
            return None
        columns = entry.get("output_columns")
        if columns is None:
            return None
        return list(columns)

    def output_values_of(self, decision_table_id: str) -> dict[str, set[str]]:
        """層2の行から、指定した決定表の出力名ごとの値を集める(D12・C11用)。

        層2の行は、出力が `{decision, reason_code}` のときだけトップレベルに展開され、
        それ以外は `outputs` マップに入る(設計書4.5 層2)。両方の形を見る。
        """
        values: dict[str, set[str]] = {}
        for row in self.rows_of(decision_table_id):
            outputs = row.get("outputs")
            if isinstance(outputs, dict):
                for name, value in outputs.items():
                    values.setdefault(name, set()).add(str(value))
            for key in ("decision", "reason_code"):
                if key in row:
                    values.setdefault(key, set()).add(str(row[key]))
        return values

    def output_names_of(self, decision_table_id: str) -> set[str]:
        """指定した決定表の出力名を集める。層1優先、無ければ層2の行から。

        C10 は `output_columns_of()`(層1のみ)を使う。こちらは層1・層2のどちらかに
        情報があれば答える緩い版で、初期登録の検査などに使う。
        """
        columns = self.output_columns_of(decision_table_id)
        if columns is not None:
            return set(columns)
        return set(self.output_values_of(decision_table_id).keys())

    def entry_points_of(self, decision_table_id: str) -> list[dict]:
        """層1の `entry_points`(その最終判定を呼ぶ actor・action の組)を引く(D16)。

        中間判定は空リストになる。
        """
        entry = self.find_decision_table(decision_table_id=decision_table_id)
        if entry is None:
            return []
        return [dict(ep) for ep in entry.get("entry_points", [])]

    def dependents_of(self, decision_ids: set[str]) -> list[dict]:
        """層1の `depends_on_decisions` を逆にたどり、起点を使う判定を集める(D16)。

        起点の判定（間接的に使っているものも含む）を使っている層1のエントリを、
        **層1の宣言順**で返す。起点そのものは含まない。自己参照や循環があっても
        止まるよう、集合が増えなくなるまでの反復で実装する。
        """
        reached = set(decision_ids)
        changed = True
        while changed:
            changed = False
            for entry in self.decision_tables:
                decision_id = entry.get("decision_id")
                if decision_id is None or decision_id in reached:
                    continue
                if set(entry.get("depends_on_decisions", [])) & reached:
                    reached.add(decision_id)
                    changed = True
        return [
            entry
            for entry in self.decision_tables
            if entry.get("decision_id") in reached - set(decision_ids)
        ]


def dmn_path(dmn_model: str, base: str | Path = "registry/dmn") -> Path:
    """`registry/dmn/<dmn_model>.dmn` を返す(D15)。"""
    return Path(base) / f"{dmn_model}.dmn"
