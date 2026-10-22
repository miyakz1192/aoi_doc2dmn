"""Scope/Decision 登録簿(層1・層2・層3・review)。

決定表とルール行を「どの条文に由来し、誰がいつ登録し、人間レビューはどうなっているか」と
共に記録する台帳。v2 では配線ジェネレータの**入力**でもある(既存の決定表と fact 台帳を
参照してレベル判定と fact の再利用を判断する。設計書4章冒頭)。

仕様: docs/design/aoi_doc2dmn_IRv2_design.md 4.5節。

| 層 | 内容 | v1 からの変更 |
|---|---|---|
| 層1 `decision_tables` | 決定表ごとのメタ情報 | `dmn_model` を追加(D8) |
| 層2 `rule_rows` | ルール行1本ずつの台帳 | なし |
| 層3 `fact_registry` | fact 台帳 | **新設**(D2) |
| `review` | 条文単位のレビュー状態 | `gates`(ゲート名の一覧)を追加 |

## 永続化の方針

入力(永続台帳)は `registry/registry.json` に置き、リポジトリにコミットする。
配線ジェネレータが返す**更新後**の登録簿は `out/` に書き出し、永続台帳への反映
(promotion)は人間レビューを経た別操作とする(今回の範囲外)。

## 層1に出力列の一覧が無い件

設計書4.5の層1は `input_columns` を持つが出力列の一覧を持たない。C10(「判定名.出力名」の
判定が実在し、その出力名を持つ)を登録簿のみに存在する判定について検証するには出力名が
必要なので、`output_names_of()` で層2の行から集める。層1に `output_columns` を足す提案は
設計書への報告事項とした。
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

    def output_names_of(self, decision_table_id: str) -> set[str]:
        """層2の行から、指定した決定表の出力名を集める(C10用)。

        層2の行は、出力が `{decision, reason_code}` のときだけトップレベルに展開され、
        それ以外は `outputs` マップに入る(設計書4.5 層2)。両方の形を見る。
        """
        names: set[str] = set()
        for row in self.rows_of(decision_table_id):
            if "outputs" in row and isinstance(row["outputs"], dict):
                names.update(row["outputs"].keys())
            for key in ("decision", "reason_code"):
                if key in row:
                    names.add(key)
        return names
