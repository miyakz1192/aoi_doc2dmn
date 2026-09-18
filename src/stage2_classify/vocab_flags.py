"""既知の語彙的盲点への安全網フラグ(ルールベース)。

軸1の残存ミス(第60条・5.6、HANDOFF_20260818_round6_to_web.md参照)は、いずれも
evidence_quote/quote_verified(判定根拠の逐語引用検証)では捕まえられない性質を持つ。
LLMは実在する文言を正しく引用した上で、その解釈(承認要求・柔らかい閾値表現を
ハード制約と誤読する)を誤っている。これはハルシネーション検出では原理的に防げない。

そこで、4回の反復調整(プロンプト修正3回＋軸1の2段階再構造化1回)でも解消しなかった
既知のパターンに対応する語彙が条文中に含まれる場合、判定結果・confidenceに関わらず
④人間レビューへ強制的に回すルールベースのフラグを用意する。quote_verifiedを補完する、
独立した第三のゲート。

語幹ベースの正規表現を用いる。活用形の違い(「申し出る」「申出」「承認を受けなければ」
「承認を受ける」等)を吸収するため、辞書形の完全一致文字列ではなく語幹で照合する
(例: "承認を受ける"という辞書形の文字列一致では、第18条の「承認を受けなければ
ならない」を取りこぼす。動詞語幹"受け"までで照合することで両方を拾う)。
"""
from __future__ import annotations

import re

# (フラグ名, 語幹パターン)。マッチした語幹をrationale等に出す用途も想定し、名前も持たせる。
_VOCAB_PATTERNS: tuple[tuple[str, str], ...] = (
    ("認められる", r"認められ"),
    ("申出/申し出", r"申し?出"),
    ("許可を得る", r"許可を得"),
    ("承認を受ける/得る", r"承認を(受け|得)"),
)


def check_vocab_flag(clause_text: str) -> tuple[bool, list[str]]:
    """条文テキストが既知の語彙的盲点パターンに該当するかを判定する。

    戻り値: (flagged, matched_pattern_names)
    """
    matched = [name for name, pattern in _VOCAB_PATTERNS if re.search(pattern, clause_text)]
    return bool(matched), matched
