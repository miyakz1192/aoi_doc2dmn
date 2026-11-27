"""層3(fact 台帳)を前段から読み書きする型(前段設計書10章・P37・P42)。

事実の抽出(第2要素)は、**層3に登録済みの fact の値と完全に一致するか**で既存の fact を
使うかを決める(P37)。LLM には「どの fact と同じ列か」を聞くが、その答えでは寄せない。
この module は、その「値の一致」をプログラムで確かめるための台帳である。

第1要素の `action_ledger.py` と同じ作りにしてある(登録簿が規範で、評価では正解を足し
ながら育てる。P42)。違いは次の2点。

| 点 | Action 台帳 | fact 台帳(層3) |
| --- | --- | --- |
| 寄せる根拠 | 中心の動詞の一致(P27) | **値の完全一致**(P37) |
| 置き場所 | 登録簿の `action_ledger` | 登録簿の `fact_registry`(層3。D2) |

## 値を比べるときに除く値

- **予約語「その他」**(D45)は、条文が挙げた値ではなく「知っている状態だが条文の値では
  ない」ことを表す構造上の語なので、一致の相手から外す。条文から抜き出した語句が
  「その他」に一致することは、意味の上であり得ない
- 比較型(exact_match など)の `possible_values` はプリミティブの定義から決まる
  (D59)。P37 は一致の相手を **passthrough の fact** と定めているので、比較型は
  `match_value()` の相手にしない。ただし、値が比較型の fact の値と同じだったことは
  `duplicate_check()` が拾い、`fact_possible_duplicate`(疑い)で人に見せる
  (P41。あおいの初期登録では「一致」を3つの比較型の fact が共有しており、値だけでは
  どの fact かが決まらない)
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Optional

from src.stage3_generate.ir_schema import (
    OTHER_VALUE,
    FactDef,
    derived_possible_values,
)

# 「該当なし」の選択(P5 と同じ扱い)。手順3b の答えの選択肢に入れる。
NO_MATCH = "該当なし"

# 新規候補の名前が既存の fact に近いかのしきい値(P41)。fact の名前は英語の
# snake_case なので、Action の名前(日本語)と同じ difflib の比で見る。0.6 は
# usage_target／usage_purpose(0.71)を拾い、operator_role／work_type(0.26)を
# 拾わない値として置いた。
FACT_NAME_RATIO_THRESHOLD = 0.6


def fact_name_ratio(a: str, b: str) -> float:
    """fact の名前の文字列の類似(0〜1)。"""
    return difflib.SequenceMatcher(None, a, b).ratio()


@dataclass
class FactEntry:
    """層3の1件(IR v2 設計書4.3節の出力の形)。

    登録簿の `fact_registry` のエントリをそのまま持つ。比較型の `possible_values` は
    プリミティブの定義から導いた値が入っている(D59・D70)。
    """

    variable: str
    primitive: str
    possible_values: list[str] = field(default_factory=list)
    other_value_note: Optional[str] = None
    sources: dict = field(default_factory=dict)
    comparison_kind: Optional[str] = None
    approved_by: Optional[str] = None
    defined_by: str = ""
    provenance: dict = field(default_factory=dict)

    @property
    def is_passthrough(self) -> bool:
        return self.primitive == "passthrough"

    def clause_values(self) -> list[str]:
        """条文の語句と比べる値(予約語「その他」を除く。D45)。"""
        return [v for v in self.possible_values if v != OTHER_VALUE]

    def to_json(self) -> dict:
        out = {
            "variable": self.variable,
            "primitive": self.primitive,
            "possible_values": list(self.possible_values),
            "sources": dict(self.sources),
            "comparison_kind": self.comparison_kind,
            "approved_by": self.approved_by,
            "other_value_note": self.other_value_note,
            "defined_by": self.defined_by,
            "provenance": dict(self.provenance),
        }
        return out

    @classmethod
    def from_json(cls, payload: dict) -> "FactEntry":
        return cls(
            variable=payload["variable"],
            primitive=payload["primitive"],
            possible_values=list(payload.get("possible_values") or []),
            other_value_note=payload.get("other_value_note"),
            sources=dict(payload.get("sources") or {}),
            comparison_kind=payload.get("comparison_kind"),
            approved_by=payload.get("approved_by"),
            defined_by=payload.get("defined_by", ""),
            provenance=dict(payload.get("provenance") or {}),
        )

    @classmethod
    def from_fact_def(cls, fact: FactDef, *, clause_ref: str = "") -> "FactEntry":
        """IR の `new_facts` の1件を層3のエントリにする(配線ジェネレータと同じ形)。"""
        payload = fact.to_json()
        entry = cls.from_json(payload)
        entry.defined_by = clause_ref
        entry.provenance = {"origin": "regulation_clause", "clause_ref": clause_ref}
        return entry


@dataclass
class FactDuplicateCheck:
    """新規候補の fact が既存の fact に近いか(P41 の fact_possible_duplicate)。

    見るのは3つで、1つでも当たれば近いとする(ゲートを1つのスコアに混ぜない思想)。

    1. 名前が既存の fact と同じ(`collides`)
    2. 名前の文字列の類似がしきい値を超える
    3. 候補の値が既存の fact の値と同じものを含む(**比較型も見る**)
    """

    variable: str
    closest: Optional[str] = None
    name_ratio: float = 0.0
    collides: bool = False
    shared_values: list[dict] = field(default_factory=list)
    reason: str = ""

    @property
    def flagged(self) -> bool:
        return bool(self.collides or self.shared_values or self.reason)

    def to_json(self) -> dict:
        return {
            "variable": self.variable,
            "closest": self.closest,
            "name_ratio": round(self.name_ratio, 3),
            "collides": self.collides,
            "shared_values": [dict(s) for s in self.shared_values],
            "flagged": self.flagged,
            "reason": self.reason,
            "threshold": FACT_NAME_RATIO_THRESHOLD,
        }


@dataclass
class ValueMatch:
    """手順3a の記録: 1つの値が層3の passthrough の fact と一致したか(P37)。"""

    value: str
    matches: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def chosen(self) -> Optional[str]:
        """一致が1つならその fact、2つ以上なら宣言順の1つ目(手順3a)。"""
        return self.matches[0] if self.matches else None

    @property
    def ambiguous(self) -> bool:
        """一致が2つ以上(1つ目に寄せたうえで人に回す)。"""
        return len(self.matches) > 1

    def to_json(self) -> dict:
        return {
            "value": self.value,
            "matches": list(self.matches),
            "chosen": self.chosen,
            "ambiguous": self.ambiguous,
            "reason": self.reason,
        }


@dataclass
class FactLedger:
    """層3(fact 台帳)。宣言順(登録順)を保つ。"""

    entries: list[FactEntry] = field(default_factory=list)

    # ---- 入出力 ----

    @classmethod
    def from_json(cls, payload: list[dict]) -> "FactLedger":
        return cls(entries=[FactEntry.from_json(e) for e in payload])

    def to_json(self) -> list[dict]:
        return [e.to_json() for e in self.entries]

    def copy(self) -> "FactLedger":
        return FactLedger.from_json(self.to_json())

    # ---- 検索 ----

    def names(self) -> list[str]:
        return [e.variable for e in self.entries]

    def find(self, variable: str) -> Optional[FactEntry]:
        for entry in self.entries:
            if entry.variable == variable:
                return entry
        return None

    def passthrough_entries(self) -> list[FactEntry]:
        return [e for e in self.entries if e.is_passthrough]

    def comparison_entries(self) -> list[FactEntry]:
        return [e for e in self.entries if not e.is_passthrough]

    # ---- 手順3a: 値の完全一致(P37) ----

    def match_value(self, value: str) -> ValueMatch:
        """正規化した値が、層3の **passthrough** の fact の値と完全一致するか(P37)。

        一致が1つならその fact を使う(referenced_facts)。2つ以上なら宣言順の1つ目に
        寄せたうえで人に回す(`fact_possible_duplicate`)。一致が無ければ手順3b へ。
        """
        if not value:
            return ValueMatch(value=value, matches=[], reason="値が空(抜き出しに失敗)")
        matches = [
            e.variable for e in self.passthrough_entries() if value in e.clause_values()
        ]
        if len(matches) == 1:
            reason = f"値 {value!r} が {matches[0]!r} の値と完全一致"
        elif len(matches) > 1:
            reason = (
                f"値 {value!r} が2つ以上の fact の値と一致({'、'.join(matches)})ので"
                "1つ目に寄せて人に回す"
            )
        else:
            reason = f"値 {value!r} と完全一致する値が層3の passthrough の fact に無い"
        return ValueMatch(value=value, matches=matches, reason=reason)

    def comparison_value_matches(self, value: str) -> list[str]:
        """その値を持つ**比較型**の fact(P37 の一致の相手ではない。記録と疑い用)。"""
        if not value:
            return []
        return [
            e.variable
            for e in self.comparison_entries()
            if value in (e.possible_values or list(derived_possible_values(e.primitive) or ()))
        ]

    # ---- 更新(教師あり。P42) ----

    def add(self, entry: FactEntry) -> FactEntry:
        if self.find(entry.variable) is not None:
            raise ValueError(f"fact {entry.variable!r} はすでに層3にあります")
        self.entries.append(entry)
        return entry

    def add_fact_def(self, fact: FactDef, *, clause_ref: str = "") -> FactEntry:
        return self.add(FactEntry.from_fact_def(fact, clause_ref=clause_ref))

    def add_values(self, variable: str, values: list[str]) -> list[str]:
        """既存の fact に値を足す(D14 の value_additions)。足した値を返す。"""
        entry = self.find(variable)
        if entry is None:
            raise KeyError(variable)
        added = []
        for value in values:
            if value in entry.possible_values:
                continue
            # 予約語「その他」は末尾に置く(D45)ので、その手前に入れる
            if entry.possible_values and entry.possible_values[-1] == OTHER_VALUE:
                entry.possible_values.insert(len(entry.possible_values) - 1, value)
            else:
                entry.possible_values.append(value)
            added.append(value)
        return added

    # ---- P41: 近さの判定 ----

    def duplicate_check(self, variable: str, values: list[str]) -> FactDuplicateCheck:
        """新規候補の fact が既存の fact に近いか(P41)。"""
        check = FactDuplicateCheck(variable=variable)
        reasons: list[str] = []
        if self.find(variable) is not None:
            check.collides = True
            check.closest = variable
            check.name_ratio = 1.0
            reasons.append(f"名前 {variable!r} が既存の fact と同じ")
        best: tuple[float, str] = (0.0, "")
        for entry in self.entries:
            ratio = fact_name_ratio(variable, entry.variable)
            if ratio > best[0]:
                best = (ratio, entry.variable)
        if not check.collides and best[1]:
            check.closest = best[1]
            check.name_ratio = best[0]
            if best[0] >= FACT_NAME_RATIO_THRESHOLD:
                reasons.append(
                    f"名前の類似 {best[0]:.2f} >= {FACT_NAME_RATIO_THRESHOLD}"
                    f"(既存の {best[1]!r})"
                )
        for value in values:
            for entry in self.entries:
                pool = (
                    entry.clause_values()
                    if entry.is_passthrough
                    else list(entry.possible_values)
                )
                if value in pool:
                    check.shared_values.append(
                        {
                            "value": value,
                            "fact": entry.variable,
                            "primitive": entry.primitive,
                        }
                    )
        if check.shared_values:
            shown = "、".join(
                f"{s['value']}={s['fact']}" for s in check.shared_values[:3]
            )
            reasons.append(f"候補の値が既存の fact の値と同じ({shown})")
        check.reason = "／".join(reasons)
        return check

    # ---- LLM へ渡す形 ----

    def prompt_block(self) -> str:
        """手順3b・手順4 に渡す層3の一覧(名前・型・値だけ。W1 の教訓)。

        定義の文は渡さない。第1要素では、定義を読ませると LLM が「一番近い既存」を
        必ず選び、寄せてはいけない場面でも寄せた(W1・10.1.1節)。ここでは寄せるかどうかは
        プログラムが値の一致で決める(P37)ので、LLM には名前と値だけを見せる。
        """
        if not self.entries:
            return "（層3に fact はありません）"
        lines = []
        for i, entry in enumerate(self.entries, 1):
            kind = "区分の値" if entry.is_passthrough else f"比較({entry.primitive})"
            values = "、".join(entry.possible_values) or "（まだ無い）"
            lines.append(f"{i}. {entry.variable}（{kind}）\n   取りうる値: {values}")
        return "\n".join(lines)
