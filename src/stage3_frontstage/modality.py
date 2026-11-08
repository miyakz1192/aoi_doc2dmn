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

## v0.4(第31回)での変更: 様相を削るのはプログラムの仕事

初回評価で、禁止の条文の語句から様相を落とすことを LLM にはできなかった(18件中10件で
残った)。禁止の条文には、様相を含まない**完成した語**が無いためである
(「立ち入らないこと」に「立ち入る」は現れず、正解の語句は「立ち入ら」で、送り仮名の
途中で切った形になる)。そこで P4 を改訂し、**LLM は逐語の語句を抜き出すだけ**とし、
末尾に付いた様相の語は `strip_modality()` が削る。

削るのは**末尾だけ**で、語句の途中にある様相の語(条件の側の否定を含む場合など)は
削らない。削った後の語句が条文の部分文字列でなければ `action_surface_unverified` を
立てる(4.5節)。語句の途中に様相が残ることは、ゲートにはせず評価の surface の3条件で
数える(どちらが規制の対象かの判断は人に回る)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
    # 「しない」は「しないこと」の「こと」が無い形。第31回で、様相を削った語句が
    # 「原稿、印刷物を放置し」のように送り仮名の「し」を残したため足した
    "しない",
    "禁ずる",
    "禁じる",
    "厳禁",
    "禁止",
    "義務",
    "べき",
    "ない",
)


# 名前・定義の検査(P16)に使う一覧。**打ち消しだけの語を除いた**ものである。
# 「ない」「しない」は、規範の様相ではない否定(例:「管理されない状態で置いたままに
# すること」)と区別できない。定義は状態を説明するのに否定を使うことがあるので、
# この2語で止めると正しい定義まで引っかかる(実測: 放置の定義)。「ないこと」
# 「しないこと」「してはならない」のように、規範であることが語形に出ているものは残す。
_PLAIN_NEGATIONS = ("ない", "しない")
NORMATIVE_MODALITY_WORDS: tuple[str, ...] = tuple(
    w for w in MODALITY_WORDS if w not in _PLAIN_NEGATIONS
)


def find_modality(text: str, words: tuple[str, ...] = MODALITY_WORDS) -> list[str]:
    """`text` に含まれる様相の語を、長いものを優先して重複なく返す。

    短い語(「ない」)が長い語(「しないこと」)に含まれる場合は、長い方だけを返す。
    `words` を `NORMATIVE_MODALITY_WORDS` にすると、打ち消しだけの語を見ない。
    """
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for word in words:
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


def strip_modality(text: str) -> tuple[str, list[str]]:
    """末尾に付いた様相の語を削る(P4。v0.4)。

    長い語から照合し、末尾に当たるものを1つずつ削る。削れなくなったら止める
    (「立ち入らないこと」→「ないこと」を削って「立ち入ら」)。削った語は順に返す。

    全部削ると空になる語句(様相の語だけを抜き出してきた場合)では、削る前の語句を
    そのまま返す。空の語句は条文の部分文字列として扱えず、何も報告できないため。
    """
    removed: list[str] = []
    current = text
    while True:
        for word in MODALITY_WORDS:
            if len(current) > len(word) and current.endswith(word):
                current = current[: -len(word)]
                removed.append(word)
                break
        else:
            break
    if not current:
        return text, []
    return current, removed


@dataclass
class SurfaceCheck:
    """抜き出した語句のプログラム側の検査(前段設計書4.2節 手順2)。

    `surface` は様相を削った後の語句で、前段の出力(`action.surface`)に入る。
    `raw` は LLM が返した語句、`removed_modality` は削った語である(Trace に残す)。

    `ok`(= ゲート `action_surface_unverified` を立てない条件)は、**削った後の語句が
    条文の部分文字列であること**だけである(P4・4.5節 v0.4)。語句の途中に残った
    様相は `modality_found` に入れて報告するが、ゲートにはしない。
    """

    surface: str
    in_clause: bool
    modality_found: list[str]
    raw: str = ""
    removed_modality: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.in_clause

    def reasons(self) -> list[str]:
        out = []
        if not self.in_clause:
            out.append(f"様相を削った語句 {self.surface!r} が条文に逐語で実在しない")
        if self.modality_found:
            out.append(f"語句の途中に規範の様相の語が残っている: {self.modality_found}")
        return out

    def to_json(self) -> dict:
        return {
            "raw": self.raw,
            "surface": self.surface,
            "removed_modality": list(self.removed_modality),
            "in_clause": self.in_clause,
            "modality_found": list(self.modality_found),
        }


# 逐語の照合の前に剥がす記号(②の分類器の _QUOTE_STRIP_CHARS と同じ扱い)。
_STRIP_CHARS = "「」『』\"'“” 　\n\t。、"


def find_normative_modality(text: str) -> list[str]:
    """Action の名前・定義に規範の様相の語が入っていないかを見る(P16)。"""
    return find_modality(text, NORMATIVE_MODALITY_WORDS)


def check_surface(surface: str, clause_text: str) -> SurfaceCheck:
    """末尾の様相を削り、残りが条文の部分文字列かを確かめる(手順2。P4)。"""
    cleaned = surface.strip().strip(_STRIP_CHARS).strip()
    stripped, removed = strip_modality(cleaned)
    stripped = stripped.strip().strip(_STRIP_CHARS).strip()
    return SurfaceCheck(
        surface=stripped,
        in_clause=bool(stripped) and stripped in clause_text,
        modality_found=find_modality(stripped),
        raw=cleaned,
        removed_modality=removed,
    )
