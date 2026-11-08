"""Action 台帳(前段設計書3章・P2・P3)。

エージェントが実行時に要求する行為(Action)の一覧。人の従業員でいえば職務記述書に
当たる。Action 正規化は、この台帳の中から1つ選ぶか「該当なし」を選ぶ閉じた選択に
なる(P5)ので、台帳は LLM に渡す選択肢そのものである。

## 置き場所

登録簿(`registry/registry.json`)の新しいキー `action_ledger` に、層1〜3と並べて置く
(設計書3章「登録簿の中に、層1〜3と並べて置く」)。別ファイルにしなかった理由は2つ。

1. ルーティング表は層1の `entry_points` の action と台帳の name を突き合わせる。
   2つが別のファイルに分かれると、どちらが正かが曖昧になる
2. 配線ジェネレータは登録簿1つを入力に取る(設計書4章)。台帳を別ファイルにすると、
   前段と後半で入力の数が変わる

## 項目

設計書3.1(v0.4)の項目をそのまま持つ。定義の根拠の語句は
`provenance.definition_basis` に入れる(項目を増やさないため)。

| 項目 | v0.4 での変更 |
| --- | --- |
| `kind`(concrete / abstract) | **新設**(P14・D73)。実行時に要求されるのは concrete だけ |
| `parent` | 意味が変わった。親の禁止・条件付き許可は子に及ぶ(P19・D74) |
| `excludes` | **新設**(P8・7.3節)。この Action に含まない別の Action の名前 |
| `name_status`・`definition_status` | `status` を2つに分けた(P18) |
| `surfaces` の各項目 | `core_verb`(中心の動詞)を持つ。動詞の一致の検査に使う(P15) |

## 階層のたどり方

親子をたどるのは `src/stage3_generate/registry.py` の `action_ancestors()` /
`action_descendants()` である。後半の配線ジェネレータ(ルーティング表の継承。D75・D76)と
前段(評価の分類。5.1節)が同じ規則でたどる必要があるため、こちらはそこに委譲する。
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Optional

from src.stage3_generate.registry import (
    ACTION_ABSTRACT,
    ACTION_CONCRETE,
    action_ancestors,
    action_descendants,
)

# 「該当なし」の選択(P5)。閉じた選択の中の1つの値として扱う。
NO_MATCH = "該当なし"

# --------------------------------------------------------------------------
# 近さの判定のしきい値(P8。第29回 HANDOFF 5.2節で判断を任された)
# --------------------------------------------------------------------------
# 名前の文字列の類似(difflib.SequenceMatcher の比)。日本語は語の区切りが無いので、
# 文字単位の比を使う。0.6 は「送信」と「外部送信」(0.80)・「情報提供」と「情報開示」
# (0.50)の間に置いた値。実測した組: データ送信／外部送信 0.44、口頭伝達／外部送信
# 0.25、アカウント共用／外部送信 0.00。
NAME_RATIO_THRESHOLD = 0.6
# 定義の語の重なり(文字2-gram の Jaccard 係数)。0.3 は、言い換えの定義どうしを拾い、
# 別の行為の定義(「1つのアカウントを2人以上で使うこと」と「患者に関する情報を、
# 患者・代理人などの外部に送ること」= 0.03)を拾わない値として置いた。
DEFINITION_OVERLAP_THRESHOLD = 0.3
# 片方の名前がもう片方を含む場合(例:「送信」と「データ送信」)は、比に関わらず近いと
# する。行為の種類が同じで修飾が違うだけの可能性が高い。
_SUBSTRING_IS_CLOSE = True
# 名前の末尾が何文字一致したら近いとみなすか。設計書4.3節は新規候補の名前を体言に
# すると定めており(〜操作・〜使用・〜提供・〜伝達)、行為の種類は末尾の体言が担う。
# 「データ送信」と「外部送信」は比では 0.44 だが、末尾の「送信」が同じなので、
# 修飾が違うだけの同じ行為である可能性を人に見せる。
SHARED_SUFFIX_THRESHOLD = 2


def shared_suffix_length(a: str, b: str) -> int:
    """2つの名前の末尾が何文字一致するか(行為の種類を担う体言を見る)。"""
    n = 0
    while n < min(len(a), len(b)) and a[len(a) - 1 - n] == b[len(b) - 1 - n]:
        n += 1
    return n


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(text.split())
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def name_ratio(a: str, b: str) -> float:
    """名前の文字列の類似(0〜1)。"""
    return difflib.SequenceMatcher(None, a, b).ratio()


def definition_overlap(a: str, b: str) -> float:
    """定義の語の重なり(文字2-gram の Jaccard 係数。0〜1)。"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


@dataclass
class ActionEntry:
    """台帳の1件(設計書3.1 v0.4)。"""

    name: str
    definition: str
    kind: str = ACTION_CONCRETE
    parent: Optional[str] = None
    excludes: list[str] = field(default_factory=list)
    surfaces: list[dict] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    name_status: str = "provisional"
    definition_status: str = "provisional"
    approved_by: Optional[str] = None
    provenance: dict = field(default_factory=dict)

    @property
    def is_abstract(self) -> bool:
        return self.kind == ACTION_ABSTRACT

    def core_verbs(self) -> list[str]:
        """過去の語句の中心の動詞(動詞の一致の検査に使う。P15)。"""
        return [s["core_verb"] for s in self.surfaces if s.get("core_verb")]

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "definition": self.definition,
            "kind": self.kind,
            "parent": self.parent,
            "excludes": list(self.excludes),
            "surfaces": [dict(s) for s in self.surfaces],
            "actors": list(self.actors),
            "name_status": self.name_status,
            "definition_status": self.definition_status,
            "approved_by": self.approved_by,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_json(cls, payload: dict) -> "ActionEntry":
        return cls(
            name=payload["name"],
            definition=payload.get("definition", ""),
            kind=payload.get("kind") or ACTION_CONCRETE,
            parent=payload.get("parent"),
            excludes=list(payload.get("excludes", [])),
            surfaces=[dict(s) for s in payload.get("surfaces", [])],
            actors=list(payload.get("actors", [])),
            name_status=payload.get("name_status", "provisional"),
            definition_status=payload.get("definition_status", "provisional"),
            approved_by=payload.get("approved_by"),
            provenance=dict(payload.get("provenance", {})),
        )


@dataclass
class DuplicateCheck:
    """新規候補と既存の Action の近さ(P8)。"""

    closest: Optional[str]
    name_ratio: float
    definition_overlap: float
    flagged: bool
    reason: str = ""

    def to_json(self) -> dict:
        return {
            "closest": self.closest,
            "name_ratio": round(self.name_ratio, 3),
            "definition_overlap": round(self.definition_overlap, 3),
            "flagged": self.flagged,
            "reason": self.reason,
            "thresholds": {
                "name_ratio": NAME_RATIO_THRESHOLD,
                "definition_overlap": DEFINITION_OVERLAP_THRESHOLD,
                "shared_suffix": SHARED_SUFFIX_THRESHOLD,
            },
        }


@dataclass
class ActionLedger:
    """Action 台帳。宣言順(登録順)を保つ。"""

    entries: list[ActionEntry] = field(default_factory=list)

    # ---- 入出力 ----

    @classmethod
    def from_json(cls, payload: list[dict]) -> "ActionLedger":
        return cls(entries=[ActionEntry.from_json(e) for e in payload])

    def to_json(self) -> list[dict]:
        return [e.to_json() for e in self.entries]

    def copy(self) -> "ActionLedger":
        return ActionLedger.from_json(self.to_json())

    # ---- 検索・更新 ----

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def find(self, name: str) -> Optional[ActionEntry]:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def add(self, entry: ActionEntry) -> ActionEntry:
        """台帳に1件足す。同じ名前があれば `ValueError`。"""
        if self.find(entry.name) is not None:
            raise ValueError(f"Action {entry.name!r} はすでに台帳にあります")
        self.entries.append(entry)
        return entry

    def record_surface(
        self, name: str, surface: str, clause_ref: str, core_verb: str = ""
    ) -> None:
        """この Action に正規化された条文の語句を surfaces に足す(設計書3.1)。

        LLM への例として渡し、`core_verb` は動詞の一致の検査に使う(P15)。同じ語句は
        重ねない。
        """
        entry = self.find(name)
        if entry is None:
            raise KeyError(name)
        if any(s.get("text") == surface for s in entry.surfaces):
            return
        entry.surfaces.append(
            {"text": surface, "clause_ref": clause_ref, "core_verb": core_verb}
        )

    def approved(self) -> list[ActionEntry]:
        """名前が承認済みの Action だけ(実行時のルーティング表に載せる。3.1節)。"""
        return [e for e in self.entries if e.name_status == "approved"]

    # ---- 階層(P14・D73) ----

    def ancestors(self, name: str) -> list[str]:
        """祖先を近い順に返す(登録簿と同じ規則でたどる)。"""
        return action_ancestors(self.to_json(), name)

    def descendants(self, name: str) -> list[str]:
        """子孫を宣言順に返す。"""
        return action_descendants(self.to_json(), name)

    def relation(self, a: str, b: str) -> str:
        """`a` から見た `b` の関係。評価の分類に使う(5.1節)。

        - `"same"`: 同じ Action
        - `"descendant"`: `b` は `a` の子孫(抽象 → 具体の向き。適用漏れの判定に使う)
        - `"ancestor"`: `b` は `a` の祖先(具体 → 抽象の向き。過剰適用)
        - `"unrelated"`: 親子関係にない(誤統合)
        """
        if a == b:
            return "same"
        if b in self.descendants(a):
            return "descendant"
        if b in self.ancestors(a):
            return "ancestor"
        return "unrelated"

    # ---- P8: 近さの判定 ----

    def duplicate_check(self, name: str, definition: str) -> DuplicateCheck:
        """新規候補が既存の Action に近いかを調べる(P8)。

        名前の文字列の類似、片方が片方を含むこと、末尾の体言の一致、定義の語の
        重なりの4つを見て、いずれかがしきい値を超えたら近いとする
        (OR。ゲートを1つのスコアに混ぜない思想と同じ)。
        """
        best: Optional[tuple[float, float, str, str]] = None
        for entry in self.entries:
            ratio = name_ratio(name, entry.name)
            overlap = definition_overlap(definition, entry.definition)
            reasons = []
            if ratio >= NAME_RATIO_THRESHOLD:
                reasons.append(f"名前の類似 {ratio:.2f} >= {NAME_RATIO_THRESHOLD}")
            if _SUBSTRING_IS_CLOSE and (name in entry.name or entry.name in name):
                reasons.append(f"名前が互いに含まれる({name!r}／{entry.name!r})")
            suffix = shared_suffix_length(name, entry.name)
            if suffix >= SHARED_SUFFIX_THRESHOLD:
                reasons.append(f"名前の末尾の体言が同じ({name[-suffix:]!r})")
            if overlap >= DEFINITION_OVERLAP_THRESHOLD:
                reasons.append(
                    f"定義の語の重なり {overlap:.2f} >= {DEFINITION_OVERLAP_THRESHOLD}"
                )
            score = max(ratio, overlap, suffix / max(len(name), len(entry.name), 1))
            candidate = (score, ratio, overlap, entry.name, "／".join(reasons))
            if best is None or score > best[0]:
                best = candidate  # type: ignore[assignment]
        if best is None:
            return DuplicateCheck(None, 0.0, 0.0, False, "台帳が空")
        _score, ratio, overlap, closest, reason = best  # type: ignore[misc]
        return DuplicateCheck(
            closest=closest,
            name_ratio=ratio,
            definition_overlap=overlap,
            flagged=bool(reason),
            reason=reason or "しきい値を超える既存の Action は無い",
        )

    # ---- LLM へ渡す形 ----

    def prompt_block(self) -> str:
        """プロンプトに入れる台帳の一覧(P11・P14)。

        v0.4 では段(kind)・親(parent)・excludes も渡す。手順3は「動詞が手段を問わない
        なら抽象的な段、手段が決まっているなら具体的な段」を判断するので、段が見えて
        いなければ選べない。
        """
        if not self.entries:
            return "（台帳は空です。既存の Action はありません）"
        lines = []
        for i, entry in enumerate(self.entries, 1):
            kind = "抽象的な行為(手段を問わない)" if entry.is_abstract else "具体的な行為"
            line = f"{i}. {entry.name}（{kind}）\n   定義: {entry.definition}"
            if entry.parent:
                line += f"\n   親(より抽象的な行為): {entry.parent}"
            if entry.excludes:
                line += f"\n   この Action に含まないもの: {'、'.join(entry.excludes)}"
            examples = "、".join(
                f"{s['text']}（中心の動詞: {s['core_verb']}）"
                if s.get("core_verb")
                else s["text"]
                for s in entry.surfaces
            )
            if examples:
                line += f"\n   これまでにこの Action に正規化された条文の語句: {examples}"
            lines.append(line)
        return "\n".join(lines)
