"""testdata/clauses/*.json を読み込むための共通ユーティリティ。

stage1_filter / stage2_classify / eval のいずれからも、
この関数経由でオラクルデータを読み込む想定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClauseOracle:
    clause_id: str
    article_no: str
    article_title: str
    text_raw: str
    text_filled: str
    parameter_slots: list
    stage1_relevant: bool
    axis1: str | None
    axis2: str | None
    cell_id: str | None
    is_ambiguous: bool
    alt_labels: dict | None
    confidence_note: str

    def text(self, variant: str = "raw") -> str:
        """variant: 'raw' または 'filled'。text_raw/text_filledのどちらを使うか
        eval側から明示的に選ばせるためのヘルパー。"""
        if variant == "raw":
            return self.text_raw
        if variant == "filled":
            return self.text_filled
        raise ValueError(f"unknown variant: {variant!r} (expected 'raw' or 'filled')")


def load_clauses(path: str | Path) -> list[ClauseOracle]:
    """testdata/clauses/clauses_v1.json 形式のファイルを読み込む。"""
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    clauses = []
    for c in data["clauses"]:
        oracle = c["oracle"]
        clauses.append(
            ClauseOracle(
                clause_id=c["clause_id"],
                article_no=c["article_no"],
                article_title=c["article_title"],
                text_raw=c["text_raw"],
                text_filled=c["text_filled"],
                parameter_slots=c.get("parameter_slots", []),
                stage1_relevant=oracle["stage1_relevant"],
                axis1=oracle["axis1"],
                axis2=oracle["axis2"],
                cell_id=oracle["cell_id"],
                is_ambiguous=c["is_ambiguous"],
                alt_labels=c.get("alt_labels"),
                confidence_note=oracle["confidence_note"],
            )
        )
    return clauses


if __name__ == "__main__":
    # 動作確認用: python -m src.common.clause_io
    default_path = Path(__file__).resolve().parents[2] / "testdata" / "clauses" / "clauses_v1.json"
    loaded = load_clauses(default_path)
    print(f"loaded {len(loaded)} clauses from {default_path}")
    for c in loaded[:3]:
        print(f"  - {c.clause_id}: relevant={c.stage1_relevant} axis1={c.axis1} axis2={c.axis2}")
    slots = [c for c in loaded if c.parameter_slots]
    print(f"clauses with parameter_slots: {[c.clause_id for c in slots]}")
