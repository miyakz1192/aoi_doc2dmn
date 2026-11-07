"""規範の様相(modality)の語の検出(前段設計書 P4・IR v2 設計書 D71)。

`action.surface` は「条文中の行為を表す語句を逐語で抜き出したもの」で、規範の様相
(してはならない・しないこと・禁止する、「させない」の「ない」)を含めない。様相は
決定表の**結論**の側の情報であり、行為の側に混ぜると、同じ行為に対する禁止の条文と
許可の条文が別の Action になってしまう。

## 様相の語の一覧をどう決めたか

設計書は4つの例(してはならない・しないこと・禁止する・「させない」の「ない」)しか
挙げていないので、一覧の決め方は実装の判断に任されている(第29回 HANDOFF 5.2節)。
次の方針で決めた。

1. **禁止・義務の語尾**と**禁止を表す体言**だけを入れる。「速やかに」「努める」など、
   様相以外の規範の強さを表す語は入れない(surface から外す対象ではない)
2. 打ち消しの「ない」は単独で入れる。設計書が「させない」の「ない」を様相として
   挙げているため。長いものから照合して、報告する語はより具体的なものにする
3. 「べき」「義務」も入れる。原典に「〜すべき」「〜する義務を負う」の形があるため

**この一覧の限界**(報告事項): 打ち消しの「ない」は、様相ではない否定
(例:「許可されていないパソコン」の「ない」)とプログラムでは区別できない。条件の側の
否定を含む語句を抜き出すと、様相と同じ扱いで止まる。実際、承認済みの正解IR の
`ipa_secguide_v4_personnel_2` は「許可されていない組織、人に提供」ではなく
「組織、人に提供」を surface にしており、この一覧はその判断と同じ側に立っている。
"""
from __future__ import annotations

from dataclasses import dataclass

# 規範の様相の語。長いものから照合する(報告する語を具体的にするため)。
MODALITY_WORDS: tuple[str, ...] = (
    "してはならない",
    "してはいけない",
    "しなければならない",
    "しなくてはならない",
    "しなければいけない",
    "しないものとする",
    "するものとする",
    "しないこと",
    "しないよう",
    "なりません",
    "いけません",
    "ならない",
    "いけない",
    "べきである",
    "ないこと",
    "禁ずる",
    "禁じる",
    "厳禁",
    "禁止",
    "義務",
    "べき",
    "ない",
)


def find_modality(text: str) -> list[str]:
    """`text` に含まれる様相の語を、長いものを優先して重複なく返す。

    短い語(「ない」)が長い語(「しないこと」)に含まれる場合は、長い方だけを返す。
    """
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for word in MODALITY_WORDS:
        start = 0
        while True:
            i = text.find(word, start)
            if i < 0:
                break
            start = i + 1
            if any(s <= i and i + len(word) <= e for s, e in spans):
                continue  # すでに見つけた長い語の内側
            spans.append((i, i + len(word)))
            if word not in found:
                found.append(word)
    return found


@dataclass
class SurfaceCheck:
    """抜き出した語句のプログラム側の検査(前段設計書4.2節 手順2)。"""

    surface: str
    in_clause: bool
    modality_found: list[str]

    @property
    def ok(self) -> bool:
        return self.in_clause and not self.modality_found

    def reasons(self) -> list[str]:
        out = []
        if not self.in_clause:
            out.append(f"抜き出した語句 {self.surface!r} が条文に逐語で実在しない")
        if self.modality_found:
            out.append(f"規範の様相の語を含む: {self.modality_found}")
        return out


# 逐語の照合の前に剥がす記号(②の分類器の _QUOTE_STRIP_CHARS と同じ扱い)。
_STRIP_CHARS = "「」『』\"'“” 　\n\t。、"


def check_surface(surface: str, clause_text: str) -> SurfaceCheck:
    """抜き出した語句が条文の部分文字列か、様相の語を含まないかを確かめる。"""
    cleaned = surface.strip().strip(_STRIP_CHARS).strip()
    return SurfaceCheck(
        surface=cleaned,
        in_clause=bool(cleaned) and cleaned in clause_text,
        modality_found=find_modality(cleaned),
    )
