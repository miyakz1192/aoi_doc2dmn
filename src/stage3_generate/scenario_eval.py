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


# --------------------------------------------------------------------------
# 禁止の継承と合成(D74・D75・前段設計書 P19)
# --------------------------------------------------------------------------

# 結論の厳しさ(大きいほど厳しい)。禁止 ＞ 条件付き許可 ＞ 許可(D75)。
# `escalate`(D46)は「人が見るまで通さない」ので、最も厳しい側に置く。実行時の
# 仕組み(U4)が無いので、この並びは設計書の3値にエスカレーションを足した判断である。
DECISION_STRICTNESS: dict[str, int] = {
    "許可": 1,
    "条件付き許可": 2,
    "禁止": 3,
    ESCALATE_DECISION: 4,
}
# 継承側から取り入れる結論(制限的なものだけ。許可は及ばない。D74・D60)
INHERITED_RESTRICTIVE = ("禁止", "条件付き許可", ESCALATE_DECISION)


def strictness(decision: str) -> int:
    """結論の厳しさを返す。知らない結論は最も厳しい扱いにする(安全側。D46と同じ)。"""
    return DECISION_STRICTNESS.get(decision, max(DECISION_STRICTNESS.values()))


def compose_decisions(own: Optional[dict], inherited: list[dict]) -> dict:
    """自分の判定と継承する判定の結論を合成する(D74・D75)。

    - 継承側からは**禁止・条件付き許可だけ**を取り入れる。親の許可は子に及ばない(D60)
    - 取り入れた結論と自分の結論のうち、最も厳しいものを採る
    - 自分の判定が無い具体的な行為(`own=None`)では、継承側の制限だけがかかる。
      制限が1つも無ければ「許可」になる(禁止する条文が1つも無い行為は許可)

    戻り値は採った結論に `_composed_from`(どの判定から採ったか)と `_evaluated`
    (評価した判定と結論の一覧)を足したもの。先頭が `_` のキーは評価の内訳である
    (`_matched_rule`・`_unknown_facts` と同じ扱い)。
    """
    evaluated: list[dict] = []
    candidates: list[tuple[int, str, dict]] = []
    if own is not None:
        evaluated.append({"source": "own", "decision": own.get("decision")})
        candidates.append((strictness(str(own.get("decision"))), "own", own))
    for item in inherited:
        result = item["result"]
        decision = str(result.get("decision"))
        taken = decision in INHERITED_RESTRICTIVE
        evaluated.append(
            {
                "source": item.get("decision_id"),
                "decision": decision,
                # 親の許可は取り入れない(D60)。取り入れたかどうかを記録に残す
                "taken": taken,
            }
        )
        if taken:
            candidates.append((strictness(decision), str(item.get("decision_id")), result))
    if not candidates:
        return {
            "decision": "許可",
            "reason_code": "no_restriction",
            "_composed_from": None,
            "_evaluated": evaluated,
        }
    candidates.sort(key=lambda c: c[0], reverse=True)
    _rank, source, chosen = candidates[0]
    out = dict(chosen)
    out["_composed_from"] = source
    out["_evaluated"] = evaluated
    return out


def evaluate_route(
    route: dict,
    facts: dict,
    *,
    model_of,
    bindings: Optional[dict] = None,
    own_model=None,
) -> dict:
    """ルーティング表の1行を、継承する判定も含めて評価する(D74・D75)。

    `model_of(dmn_model)` は DMN モデル名から評価器を返す呼び出し可能オブジェクト
    (run_ir_gold は持ち回り用のディレクトリから読む)。`own_model` を渡した場合は
    自分の判定にそれを使う(いま生成した DMN をファイルに書く前に評価したいとき)。

    実行時の仕組み(オーケストレータ。U4)はまだ無いので、この関数はシナリオ評価の
    入口としてだけ使う(第31回 HANDOFF 2章 タスクA3)。
    """
    own_decision_id = route.get("decision_id")
    own = None
    if own_decision_id:
        model = own_model if own_model is not None else model_of(route.get("dmn_model"))
        own = evaluate_scenario(model, own_decision_id, facts, bindings=bindings)
    inherited: list[dict] = []
    for decision in route.get("inherited_decisions", []):
        model = model_of(decision.get("dmn_model"))
        inherited.append(
            {
                "dmn_model": decision.get("dmn_model"),
                "decision_id": decision.get("decision_id"),
                "result": evaluate_scenario(
                    model, decision["decision_id"], facts, bindings=bindings
                ),
            }
        )
    return compose_decisions(own, inherited)
