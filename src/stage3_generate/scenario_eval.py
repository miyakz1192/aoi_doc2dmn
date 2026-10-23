"""シナリオ評価の入口(D46)。

実行時、fact の値がバインディング設定の `possible_values`(予約語「その他」を含む)に
無い場合は、**DMN に渡さずに人の確認へ回す**(エスカレーション)。これが U2
(「既定の行が許可なので、知らない値も許可に落ちる」)への方針である。

- **知っている値で、禁止の組み合わせに当たらないもの** → 既定の行(許可)に落ちる。
  条文が片側の値しか挙げない fact の反対側は、予約語「その他」で表す(D45)
- **知らない値** → ここで止めて人に回す。安全側に倒す

実行時の仕組みそのもの(一致プリミティブ・オーケストレータ)は③aの範囲外(U4)なので、
この入口はシナリオ評価(`run_ir_gold` と受け入れ経路のテスト)でだけ使う。バインディング
設定の `on_unknown_value = escalate` が、実行時に同じ判定をすることの宣言である。

## 判定の対象

シナリオが渡した fact のうち、**バインディング設定にある fact だけ**を見る。
バインディング設定に無い fact(その判定が使わない事実)は、値が何であれ判定に関わらない
ので対象にしない。シナリオが渡していない fact も対象にしない(値が無いだけで、
知らない値ではない)。
"""
from __future__ import annotations

from typing import Optional

ESCALATE_DECISION = "escalate"
ESCALATE_REASON = "unknown_fact_value"


def unknown_facts(bindings: dict, facts: dict) -> list[str]:
    """値が possible_values(「その他」を含む)に無い fact を列挙する(D46。宣言順)。"""
    out: list[str] = []
    for fact in bindings.get("facts", []):
        variable = fact["variable"]
        if variable not in facts:
            continue
        if facts[variable] not in fact.get("possible_values", []):
            out.append(variable)
    return out


def evaluate_scenario(
    model,
    decision_id: str,
    facts: dict,
    *,
    bindings: Optional[dict] = None,
) -> dict:
    """バインディング設定の入口を通してから DMN を評価する(D46)。

    知らない値があれば DMN を呼ばず、`decision = escalate` を返す。どの fact が
    知らない値だったかは `_unknown_facts` に入れる(`_matched_rule` と同じく、
    先頭が `_` のキーは評価の内訳)。

    `bindings` を渡さない場合は入口の判定をせず、DMN をそのまま評価する
    (DMN 単体の挙動を見たいとき)。
    """
    if bindings is not None:
        unknown = unknown_facts(bindings, facts)
        if unknown:
            return {
                "decision": ESCALATE_DECISION,
                "reason_code": ESCALATE_REASON,
                "_unknown_facts": unknown,
            }
    return model.evaluate(decision_id, facts)
