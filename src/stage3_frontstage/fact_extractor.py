"""事実の抽出(前段設計書10章 v0.13)。③a 前段の第2要素。

Action 正規化が済んだ条文から、決定表の**列(fact)とその値**を取り出し、IR の
`facts` 欄(`FactUsage`。IR v2 の型をそのまま使う)を埋める(P34)。

## 手順(10.3節)と、この実装での対応

| 手順 | 誰が | 中身 | 実装 |
| --- | --- | --- | --- |
| 1 | LLM | 条件の語句を逐語で抜き出し、4種類(P35)のどれかを付ける。行為の対象は必ず含める | `_extract_conditions` |
| 2 | プログラム | 語句が条文にあるかを確かめる(括弧は P24)。値を正規化する(FU1) | `check_condition` |
| 3a | プログラム | 正規化した値が、**同じ行為の決定表が使う** passthrough の fact の値と完全一致するか(P37 改訂) | `FactLedger.match_value` |
| 3b | LLM | 一致が無い値について、どの fact と同じ列かを提案させる。**寄せない**(P37) | `_suggest_reuse` |
| 4 | LLM | 新規候補(variable・possible_values・other_value_note・primitive)の案(P38) | `_propose_facts` |
| 5 | プログラム | sources を機械的に作る(P39)。referenced_facts を D25 の基準で求める | `build_sources` |

1件あたりの LLM の呼び出しは3回(抜き出し・提案・新規候補)。3a ですべての値が既存に
一致すれば1回である(10.3節)。

## 第1要素から引き継いだ形(10.1.1節)

- **寄せるのはプログラム**(P37)。LLM の判定は提案にとどめ、`fact_reuse_suggested`
  (設計上の確認)で人に見せる。第1要素で、LLM に台帳を読ませて「一番近い既存」を
  選ばせると、寄せてはいけない場面でも必ず何かを選んだ(第37回・第39回・第41回)
- **層3を見せるのは名前と値だけ**(W1)。定義の文は渡さない
- **逐語の抜き出しはプログラムで確かめる**(P36)。確かめるのは**値の根拠の語句**で、
  値そのものは正規化してよい(D44)。条文に「システム開発を担当する者」とある条文の
  値は「システム開発担当」でよく、根拠の語句は逐語でなければならない

## この実装で判断したこと(設計書に書かれていない点)

- **手順1 に出させるのは3つ組**(根拠の語句・値・種類)。D44 が値の正規化(体言化・
  並列の展開)を許し、FU1 がそれを手順1 の LLM に任せると決めているので、逐語の
  語句と値を別に出させないと、どちらも確かめられない。プログラムは語句を条文と
  照合し、値には全角→半角と括弧の除去だけを当てる(FU1)
- **値の正規化**(FU1): 丸括弧とその中身を除き(P24 と同じ規則)、全角の英数記号を
  半角にする(D56)。体言化・並列の展開は手順1 の LLM の仕事である
- **sources の形**(P39): `from` を `action_request`、`path` を fact の名前にする。
  P39 の「`action_request.<variable>` の形」を、`from` と `path` を合わせて読んだ
  ときにその文字列になる形と読んだ。承認済みの正解 IR も
  `{"from": "action_request", "path": "operator.role"}` の形である。比較型は
  引数ごとに `<variable>.<引数名>` を置く。**前段では評価しない**(P39)ので、
  仮の値であることを Trace に残す
- **referenced_facts**(D25): D25 は「IR が書いた行と表から求める」と定めるが、この
  要素は決定表を作らない(10.5節)。手順3a で既存の fact に一致した値は、その fact を
  列として使うことなので、**3a で一致した fact の名前**を宣言順に並べる。決定表を
  作る段が入ったら、D25 の手順そのもので求め直す
- **比較型の候補の歯止め**(P38・D3・D23): 条文に比べる語(一致・照合・確認できない
  など)が無ければ、LLM が比較型を返しても passthrough に戻す。`new_facts` で宣言
  できないプリミティブ(identifiability_classifier。D23)と、知らないプリミティブも
  passthrough に戻す。戻したことは Trace に残す
- **値の付け足しの歯止め**: 手順4 が返した値のうち、手順1 の語句から作った値に無い
  ものは候補の値に入れない(条文に無い値を層3に入れないため)。捨てた値は Trace に
  残し、`fact_value_unverified` を立てる
- **比較型の候補にはこの歯止めを当てない**(Q7・第52回): 比較型の値はプリミティブの
  定義で決まる(D59)ので、手順4 が「一致・不一致・未提供」を返しても条文に無い値を
  足したことにはならない。候補の値には入れず、`comparison_values_ignored` に記録
  するだけにする
- **寄せ先の確認**(Q1・第52回): P37 改訂で寄せ先を同じ行為の fact に限ったので、別の
  行為の fact と値が一致した値は新規候補に入り、その候補は必ず `fact_possible_duplicate`
  で近いと判定される。ゲートは立てたまま、それが寄せ先の確認だけかどうかを
  `duplicate_scope_only` に残す(評価では疑いの誤報に数えない)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from src.stage3_generate.ir_schema import (
    BOOTSTRAP_ONLY_PRIMITIVES,
    PRIMITIVE_ARGS,
    ComparisonFact,
    FactUsage,
    PassthroughFact,
    PRIMITIVE_VALUES,
    ReviewGates,
    SourceRef,
)

from .fact_ledger import NO_MATCH, FactDuplicateCheck, FactLedger, ValueMatch
from .modality import strip_parentheses

# P7 と同じ。再現性のため温度は 0.0、k=1。
TEMPERATURE = 0.0

# P35: 取り出す条件の4種類。
CONDITION_KINDS: tuple[str, ...] = ("対象", "主体の属性", "状況", "状態")

# P38: 比較型の候補を許す語(2つの値を比べる語)。条文にこのどれかが無ければ、
# LLM が比較型を返しても passthrough に戻す。
COMPARISON_WORDS: tuple[str, ...] = (
    "一致",
    "不一致",
    "照合",
    "確認できない",
    "確認でき",
    "同一",
    "合致",
    "突合",
)

# 逐語の照合の前に剥がす記号(modality.py の _STRIP_CHARS と同じ扱い)。
_STRIP_CHARS = "「」『』\"'“” 　\n\t。、"

# 全角の英数記号 → 半角(D56)。括弧は先に除くので、ここでは残りを一括で直す。
_ZEN_TO_HAN = str.maketrans(
    {chr(code): chr(code - 0xFEE0) for code in range(0xFF01, 0xFF5F)}
)


class JsonLlm(Protocol):
    """JSON を返す LLM(単体テストでは固定の応答を返す実装に差し替える)。"""

    def complete_json(self, prompt: str, *, temperature: float) -> dict: ...


# --------------------------------------------------------------------------
# 手順2: 語句の確かめと値の正規化(FU1・P24・D56)
# --------------------------------------------------------------------------


def normalize_value(text: str) -> tuple[str, list[str]]:
    """値を正規化する(FU1)。括弧とその中身を除き、全角の英数記号を半角にする。

    体言化(「〜を担当する者」→「〜担当」)と並列で共有された語の展開(「休日若しくは
    深夜…労働」→「休日労働」「深夜労働」)は、プログラムでは当てられないので手順1 の
    LLM に任せる(FU1)。ここで当てた正規化の名前を一緒に返す(Trace に残す)。
    """
    cleaned = text.strip().strip(_STRIP_CHARS).strip()
    applied: list[str] = []
    without_paren = strip_parentheses(cleaned).strip()
    if without_paren != cleaned:
        applied.append("括弧の除去")
    out = without_paren.translate(_ZEN_TO_HAN)
    if out != without_paren:
        applied.append("全角→半角")
    return out.strip().strip(_STRIP_CHARS).strip(), applied


def quote_in_clause(quote: str, clause_text: str) -> bool:
    """根拠の語句が条文に逐語であるか(手順2。括弧の扱いは P24)。"""
    cleaned = strip_parentheses(quote.strip().strip(_STRIP_CHARS).strip()).strip()
    return bool(cleaned) and cleaned in strip_parentheses(clause_text)


@dataclass
class ConditionValue:
    """条件1つ分(手順1の答えと、手順2の確かめ)。"""

    quote: str
    value: str
    kind: str
    in_clause: bool
    normalization: list[str] = field(default_factory=list)
    raw_value: str = ""

    @property
    def ok(self) -> bool:
        """ゲート `fact_value_unverified` を立てない条件(根拠が逐語であること)。"""
        return self.in_clause and bool(self.value)

    def to_json(self) -> dict:
        return {
            "quote": self.quote,
            "value": self.value,
            "raw_value": self.raw_value,
            "kind": self.kind,
            "in_clause": self.in_clause,
            "normalization": list(self.normalization),
        }


def check_condition(quote: str, value: str, kind: str, clause_text: str) -> ConditionValue:
    """手順2: 根拠の語句を条文と照合し、値を正規化する。"""
    raw = value.strip().strip(_STRIP_CHARS).strip()
    normalized, applied = normalize_value(raw or quote)
    return ConditionValue(
        quote=quote.strip().strip(_STRIP_CHARS).strip(),
        value=normalized,
        kind=kind if kind in CONDITION_KINDS else "状況",
        in_clause=quote_in_clause(quote, clause_text),
        normalization=applied,
        raw_value=raw,
    )


# --------------------------------------------------------------------------
# 手順3b・手順4 の記録
# --------------------------------------------------------------------------


@dataclass
class ReuseSuggestion:
    """手順3b の記録: LLM の「どの fact と同じ列か」の提案(P37。寄せない)。"""

    value: str
    offered: list[str]
    answer: Optional[str]
    reason: str = ""
    raw_answer: str = ""
    off_list: bool = False
    # 答えの見出しを渡した値に当てた方法(exact／contains／ambiguous／none。Q5)
    matched_by: str = "none"

    def to_json(self) -> dict:
        return {
            "value": self.value,
            "offered": list(self.offered),
            "answer": self.answer,
            "reason": self.reason,
            "raw_answer": self.raw_answer,
            "off_list": self.off_list,
            "matched_by": self.matched_by,
        }


@dataclass
class NewFactProposal:
    """手順4 の記録: 新規候補1件の案(P38)。"""

    variable: str
    primitive: str
    values: list[str] = field(default_factory=list)
    other_value_note: str = ""
    comparison_kind: Optional[str] = None
    quotes: list[str] = field(default_factory=list)
    dropped_values: list[str] = field(default_factory=list)
    # 比較型の候補で手順4 が返した値(Q7)。比較型の値はプリミティブの定義で決まる
    # (D59)ので、条文に無い値を落とす歯止め(K50)は当てず、記録だけ残す
    comparison_values_ignored: list[str] = field(default_factory=list)
    # 手順3a で既存の fact に寄せた値(手順4 には渡していないので、候補の値には
    # 入れない)。条文に無い値ではないので fact_value_unverified は立てない
    reused_values: list[str] = field(default_factory=list)
    primitive_demoted_from: Optional[str] = None
    demote_reason: str = ""
    duplicate: Optional[FactDuplicateCheck] = None

    @property
    def is_comparison(self) -> bool:
        return self.primitive != "passthrough"

    @property
    def is_usable(self) -> bool:
        """IR の `new_facts` に入れられる候補か。

        区分の値の fact は、値が1つも残らなければ列にならない(D4)。手順3a で値が
        すべて既存の fact に寄った場合に起きる。候補そのものは Trace に残し、
        近さの判定(`fact_possible_duplicate`)で人に見せる。
        """
        return self.is_comparison or bool(self.values)

    def to_json(self) -> dict:
        return {
            "variable": self.variable,
            "primitive": self.primitive,
            "values": list(self.values),
            "other_value_note": self.other_value_note,
            "comparison_kind": self.comparison_kind,
            "quotes": list(self.quotes),
            "dropped_values": list(self.dropped_values),
            "comparison_values_ignored": list(self.comparison_values_ignored),
            "reused_values": list(self.reused_values),
            "primitive_demoted_from": self.primitive_demoted_from,
            "demote_reason": self.demote_reason,
            "duplicate": self.duplicate.to_json() if self.duplicate else None,
        }


@dataclass
class FactExtractionTrace:
    """レビューと評価のための記録(10.4節)。IR には入れない。"""

    temperature: float = TEMPERATURE
    conditions: list[ConditionValue] = field(default_factory=list)
    rationale: str = ""
    matches: list[ValueMatch] = field(default_factory=list)
    suggestions: list[ReuseSuggestion] = field(default_factory=list)
    proposals: list[NewFactProposal] = field(default_factory=list)
    comparison_value_hits: list[dict] = field(default_factory=list)
    # 値は一致したが別の行為の fact だったもの(寄せずに手順3b へ。P37 改訂)
    out_of_scope_hits: list[dict] = field(default_factory=list)
    # 手順3a で寄せてよい fact の範囲(その行為の決定表が使っている fact)
    reuse_scope: list[str] = field(default_factory=list)
    # fact_possible_duplicate が立った理由が「別の行為の fact と値が一致した」ことだけか
    # (Q1。誤報ではなく寄せ先の確認として数える)
    duplicate_scope_only: bool = False
    sources_note: str = ""
    elapsed_seconds: float = 0.0
    llm_calls: int = 0

    def to_json(self) -> dict:
        return {
            "temperature": self.temperature,
            "conditions": [c.to_json() for c in self.conditions],
            "rationale": self.rationale,
            "matches": [m.to_json() for m in self.matches],
            "suggestions": [s.to_json() for s in self.suggestions],
            "proposals": [p.to_json() for p in self.proposals],
            "comparison_value_hits": [dict(h) for h in self.comparison_value_hits],
            "out_of_scope_hits": [dict(h) for h in self.out_of_scope_hits],
            "reuse_scope": list(self.reuse_scope),
            "duplicate_scope_only": self.duplicate_scope_only,
            "sources_note": self.sources_note,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "llm_calls": self.llm_calls,
        }


@dataclass
class FactExtractionResult:
    """第2要素の出力。IR の `facts` 欄と、その外側の記録・ゲート。"""

    facts: FactUsage
    trace: FactExtractionTrace
    gates: ReviewGates

    def values_by_fact(self) -> dict[str, list[str]]:
        """出力した値を fact ごとに並べる(評価の突き合わせに使う。10.4.1節)。"""
        out: dict[str, list[str]] = {}
        for fact in self.facts.new_facts:
            if isinstance(fact, PassthroughFact):
                out.setdefault(fact.variable, []).extend(fact.possible_values)
            else:
                out.setdefault(fact.variable, [])
        for match in self.trace.matches:
            if match.chosen is not None:
                out.setdefault(match.chosen, []).append(match.value)
        return out

    def to_json(self) -> dict:
        return {
            "facts": {
                "new_facts": [f.to_json() for f in self.facts.new_facts],
                "referenced_facts": list(self.facts.referenced_facts),
                "value_additions": {
                    k: [v.to_json() for v in vs]
                    for k, vs in self.facts.value_additions.items()
                },
            },
            "values_by_fact": self.values_by_fact(),
            "trace": self.trace.to_json(),
            "gates": self.gates.gate_names(),
            "gate_reasons": self.gates.reasons(),
        }


# --------------------------------------------------------------------------
# プロンプト(②・第1要素で効いた形を踏襲: 逐語で根拠の語句を出させる。P11・P36)
# --------------------------------------------------------------------------


def _context_block(context: Optional[list[dict]]) -> str:
    """出典付きの context をプロンプトに入れる(IR v2 1.4節)。"""
    if not context:
        return ""
    lines = []
    for entry in context:
        text = entry.get("text") or entry.get("value") or entry.get("item", "")
        where = entry.get("where") or entry.get("source", "不明")
        lines.append(f"- {text}（出典: {where}）")
    return (
        "\n出典付きの補足情報(条文の外から出典付きで付けた情報。条件の語句はここからで"
        "はなく、条文本文から抜き出すこと):\n" + "\n".join(lines) + "\n"
    )


CONDITION_PROMPT = """あなたは就業規則・情報セキュリティ規程などの業務規程の条文を読み、
その条文が「どんな場合に」その規範が当てはまるのかを決めている**条件**を取り出す
アシスタントです。

条文(参照: {clause_ref}):
\"\"\"{clause_text}\"\"\"
{context_block}
この条文が規制している行為: 「{action_name}」（条文中の語句: 「{action_surface}」）

この条文が条件にしている語句を、条文本文から抜き出してください。次の規則を守って
ください。

1. 条件は次の4種類のどれかに分ける
   - 対象: 何を・何に(行為の相手や対象物)
   - 主体の属性: 誰が(役割・立場・区分)
   - 状況: どこで・いつ・どの権限で・何の目的で
   - 状態: 許可の有無・同意の有無・確認できたかどうか
2. **行為の対象(何を・何に)が条文にあれば、必ず挙げること**
3. 条件1つごとに、次の2つを出す
   - `quote`: その条件の根拠になる語句を、条文本文から**一言一句そのまま**抜き出す
   - `value`: その条件の値を、区分の名前の形にしたもの。言い換え・要約はしないが、
     次の直しはしてよい
     - 体言にする(例:「システム開発を担当する者」→「システム開発担当」、
       「許可なく」→「許可なし」)
     - 並列で共有された語を展開する(例:「国内若しくは海外出張」→「国内出張」と
       「海外出張」の2件に分ける)
     - 指示語や主体の限定を外す(例:「その複製物」→「複製物」)
4. **条文に書かれていない条件を足さないこと。**条文が片方の状態しか書いていなければ、
   その片方だけを出す(反対側の状態は、こちらのプログラムが別に扱います)
5. 「〜を行う」「〜をする」の形は、**手前の体言が行為**です。行為は条件ではないので
   条件に入れない(例:「操作を行う」の「操作」は行為。「特権/管理者権限で」は条件)
6. 規範の語尾(してはならない・しないこと・禁止する)は条件ではないので入れない
7. 同じ列に入る値が2つ以上あるときは、`value` を分けて別の条件として並べる
   (例:「会社の施設、物品等」→「会社の施設」と「物品等」)
8. **並べて書かれた語は、1つずつ別の条件にする。**条文が「A及びB」「A又はB」
   「A、B」「AもしくはB」のように2つ以上を並べているときは、並んでいる項を
   **すべて**別の条件として出す。代表の1つだけを出したり、1つにまとめたりしない
   (例:「出張旅費及び宿泊費」→「出張旅費」と「宿泊費」の2件)。
   - 後ろの語が共有されているときは、各項に補って展開する
     (例:「書面又は電子データの提出」→「書面の提出」と「電子データの提出」の2件)
   - 条文がその並びに括弧で言い換えの名前を付けているとき(「(以下「〇〇」という。)」)
     は、**言い換えの名前ではなく、並んでいた元の語をそれぞれ**出す
9. **行為のきっかけは条件にしない。**「〜に対し」「〜を受けて」「〜の求めにより」
   「〜の際に」のように、行為が起きた発端や経緯を述べている語句は、規範が当てはまる
   場面を狭めていないので条件に入れない(例:「取引先からの依頼に対し見積書を提出する」
   の「取引先からの依頼」は、きっかけなので条件ではない)。狭めているかどうかは
   **その語句が無くてもこの規範が当てはまるか**で見る。当てはまるなら条件ではない。
   ただし、行為の相手や対象**そのものの属性**(誰に・何に・どの区分の)は条件である

出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):
{{"conditions": [{{"quote": "条文から一言一句そのまま抜き出した語句",
   "value": "その条件の値(区分の名前の形)",
   "kind": "対象 または 主体の属性 または 状況 または 状態"}}],
 "rationale": "なぜその条件を選んだかを日本語で1文"}}
"""


REUSE_PROMPT = """あなたは業務規程から作った決定表の「列(fact)」を管理するアシスタント
です。新しい条文から取り出した条件の値が、**すでにある列と同じ列の値**かどうかだけを
判断してください。

今回の条文から取り出した条件の値:
{value_block}

すでにある列(名前と、その列が取りうる値):
{ledger_block}

値1つごとに、**同じ列に入る**と考えられる列の名前を1つ選んでください。思い当たらな
ければ「{no_match}」を選んでください。次の規則を守ってください。

1. 同じ列とは、その値が入るべき入力欄が同じことです
   (例: 既存の列 operator_role が「システム開発担当」を持つとき、値「保守担当」は
    同じ列の別の値です)
2. 値の種類が違えば別の列です
   (例: 場所の値と権限の値は別の列。「許可なし」と「同意なし」も別の列)
3. 迷ったときは「{no_match}」を選んでください。**この答えで列を決めることはしません。**
   人が確かめるための参考として記録します

出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):
{{"assignments": [{{"value": "判断した値", "fact": "列の名前、または {no_match}",
   "reason": "そう判断した理由を日本語で1文"}}]}}
"""


PROPOSAL_PROMPT = """あなたは業務規程の条文から、決定表の「列(fact)」を新しく設計する
アシスタントです。

条文(参照: {clause_ref}):
\"\"\"{clause_text}\"\"\"
{context_block}
この条文が規制している行為: 「{action_name}」

この条文から取り出した条件の値(既存の列には当てはまらないと判断されました):
{value_block}

すでにある列(名前と取りうる値。重ならない名前にするための参考):
{ledger_block}

これらの値を入れる**新しい列**の案を出してください。次の規則を守ってください。

1. 同じ列に入る値はまとめ、別の種類の値は別の列に分ける
   (例:「システム開発担当」は主体の列、「本番環境」は場所の列、「特権」は権限の列)
2. 列の名前(`variable`)は英語の snake_case にする
   (例: operator_role、target_environment、requested_privilege、usage_purpose)
3. `values` には、上に挙げた値を**そのまま**書く(言い換え・追加をしない)
4. `other_value_note` は、「知っている状態だが、この列に挙げた値ではない」ときに何を
   指すかを1文で書く(例: 値が「許可なし」だけなら「許可を得ている」)。条文に無い
   具体例は足さない
5. `primitive` は原則 "passthrough"(区分の値をそのまま渡す)にする。
   **条文が2つの値を比べている**とき(「一致」「照合」「確認できない」などの語がある)
   だけ、比べる型({comparison_primitives})にしてよい
6. 列の名前に、条文に無い業務知識や具体例を入れない

出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):
{{"facts": [{{"variable": "列の名前(英語の snake_case)",
   "primitive": "passthrough など",
   "values": ["この列に入る値"],
   "other_value_note": "「その他」が指す状態の1文",
   "comparison_kind": null}}],
 "rationale": "列の分け方の理由を日本語で1文"}}
"""


# --------------------------------------------------------------------------
# 手順5: sources(P39)
# --------------------------------------------------------------------------

SOURCES_NOTE = (
    "sources は前段では評価しない(P39)。from は action_request、path は fact の名前"
    "(比較型は <名前>.<引数名>)を機械的に置いた**仮の値**である。取得元の根拠は、"
    "エージェントの行動要求の形(U4)が決まってから付ける"
)


def build_sources(variable: str, primitive: str) -> dict[str, SourceRef]:
    """手順5: sources を機械的に作る(P39)。評価の対象ではない。"""
    args = PRIMITIVE_ARGS.get(primitive, ("value",))
    if primitive == "passthrough":
        return {"value": SourceRef(from_="action_request", path=variable)}
    return {
        arg: SourceRef(from_="action_request", path=f"{variable}.{arg}") for arg in args
    }


def clause_has_comparison_word(clause_text: str) -> list[str]:
    """条文に「2つの値を比べる語」があるか(P38)。見つかった語を返す。"""
    return [w for w in COMPARISON_WORDS if w in clause_text]


# --------------------------------------------------------------------------
# 本体
# --------------------------------------------------------------------------


def match_answer(
    value: str,
    by_value: dict[str, dict],
    *,
    position: Optional[int] = None,
    answers: Optional[list[dict]] = None,
    total: Optional[int] = None,
) -> tuple[dict, str]:
    """手順3b の答えの見出しを、渡した値に当てる(Q5。第50回パートC)。

    LLM は答えの `value` に、渡した値をそのまま書かないことがある。見出しと渡した値の
    **どちらかがもう一方を含む**なら、その値の提案として扱う(`contains`)。2つ以上に
    当たるときはどれか決められないので提案なしとする(`ambiguous`)。

    さらに、観測された型として、**見出しにも「該当なし」と書く**答え方がある
    (第50回の実測では、渡した値15件のうち10件がこれだった)。この形は、答えの数が
    渡した値の数と同じで、かつ当てようとしている答えの `fact` が「該当なし」のときだけ、
    並び順で当てる(`no_match_answer`)。**`fact` が実在の列名のときは並び順で当てない**
    ので、この当て方が提案を作ることはない(記録が読めるようになるだけである)。
    """
    if value in by_value:
        return by_value[value], "exact"
    hits = [k for k in by_value if k and (k in value or value in k)]
    if len(hits) == 1:
        return by_value[hits[0]], "contains"
    if len(hits) > 1:
        return {}, "ambiguous"
    if (
        answers is not None
        and position is not None
        and total is not None
        and len(answers) == total
        and position < len(answers)
    ):
        entry = answers[position]
        if str(entry.get("fact", "")).strip() == NO_MATCH:
            return entry, "no_match_answer"
    return {}, "none"


class FactExtractor:
    """事実の抽出の本体。LLM は差し替えられる(単体テスト用)。"""

    def __init__(self, llm: JsonLlm, *, temperature: float = TEMPERATURE) -> None:
        self.llm = llm
        self.temperature = temperature
        self.calls = 0

    # ---- 各手順 ----

    def _ask(self, prompt: str) -> dict:
        self.calls += 1
        return self.llm.complete_json(prompt, temperature=self.temperature)

    def _extract_conditions(
        self,
        clause_text: str,
        clause_ref: str,
        action_name: str,
        action_surface: str,
        context: Optional[list[dict]],
    ) -> tuple[list[ConditionValue], str]:
        """手順1(LLM)と手順2(プログラム)。"""
        prompt = CONDITION_PROMPT.format(
            clause_ref=clause_ref,
            clause_text=clause_text,
            context_block=_context_block(context),
            action_name=action_name,
            action_surface=action_surface,
        )
        parsed = self._ask(prompt)
        raw = parsed.get("conditions")
        if not isinstance(raw, list):
            raw = []
        conditions: list[ConditionValue] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            quote = str(entry.get("quote", ""))
            value = str(entry.get("value", "") or quote)
            kind = str(entry.get("kind", ""))
            if not (quote or value):
                continue
            condition = check_condition(quote, value, kind, clause_text)
            # 同じ語句で同じ値を2度返したときだけ落とす。根拠の語句が違えば、値が
            # 同じでも別の条件である(例: 第14条の「生年月日」の一致と「連絡先」の
            # 一致は、どちらも値が「一致」で、入る列は別)
            seen = {(c.quote, c.value) for c in conditions}
            if condition.value and (condition.quote, condition.value) not in seen:
                conditions.append(condition)
        return conditions, str(parsed.get("rationale", ""))

    def _suggest_reuse(
        self, values: list[str], ledger: FactLedger
    ) -> list[ReuseSuggestion]:
        """手順3b(LLM): どの既存の fact と同じ列かを提案させる。**寄せない**(P37)。"""
        offered = ledger.names()
        if not values or not offered:
            return []
        value_block = "\n".join(f"- {v}" for v in values)
        prompt = REUSE_PROMPT.format(
            value_block=value_block,
            ledger_block=ledger.prompt_block(),
            no_match=NO_MATCH,
        )
        parsed = self._ask(prompt)
        answers = parsed.get("assignments")
        if not isinstance(answers, list):
            answers = []
        by_value: dict[str, dict] = {}
        ordered = [e for e in answers if isinstance(e, dict)]
        for entry in ordered:
            if entry.get("value"):
                by_value[str(entry["value"]).strip()] = entry
        out: list[ReuseSuggestion] = []
        for position, value in enumerate(values):
            entry, matched_by = match_answer(
                value,
                by_value,
                position=position,
                answers=ordered,
                total=len(values),
            )
            raw = str(entry.get("fact", "")).strip()
            answer = raw if raw in offered else None
            reason = str(entry.get("reason", ""))
            off_list = bool(raw) and answer is None and raw != NO_MATCH
            if matched_by == "contains":
                reason = (
                    f"[LLM が見出しを {list(by_value)!r} と言い直したので、"
                    "含む関係で渡した値に当てた(Q5)] " + reason
                )
            elif matched_by == "no_match_answer":
                reason = (
                    "[LLM が見出しにも「該当なし」と書いたので、並び順で当てた"
                    "（答えは「該当なし」なので提案は作らない。K71）] " + reason
                )
            elif matched_by == "ambiguous":
                reason = (
                    "[LLM の答えの見出しが2つ以上の値に当たるので提案なしとして"
                    "扱った(Q5)] " + reason
                )
            if off_list:
                # 層3に無い名前は「提案なし」として扱う(閉じた選択。P5 と同じ)
                reason = (
                    f"[層3に無い名前 {raw!r} が返されたので提案なしとして扱った] "
                    + reason
                )
            out.append(
                ReuseSuggestion(
                    value=value,
                    offered=list(offered),
                    answer=answer,
                    reason=reason,
                    raw_answer=raw,
                    off_list=off_list,
                    matched_by=matched_by,
                )
            )
        return out

    def _propose_facts(
        self,
        clause_text: str,
        clause_ref: str,
        action_name: str,
        context: Optional[list[dict]],
        conditions: list[ConditionValue],
        ledger: FactLedger,
        reused_values: Optional[list[str]] = None,
        out_of_scope_pairs: Optional[set[tuple[str, str]]] = None,
    ) -> list[NewFactProposal]:
        """手順4(LLM): 新規候補の案(P38)。値はプログラムが選び直す。

        `conditions` は手順3a で一致しなかった条件だけである。`reused_values` は
        3a で既存の fact に寄せた値で、LLM が候補の値に混ぜてきたら候補から外すが、
        条文に無い値ではないので `fact_value_unverified` は立てない。
        """
        allowed_values = [c.value for c in conditions]
        reused = list(reused_values or [])
        quote_of = {c.value: c.quote for c in conditions}
        value_block = "\n".join(
            f"- {c.value}（根拠の語句: 「{c.quote}」／種類: {c.kind}）"
            for c in conditions
        )
        prompt = PROPOSAL_PROMPT.format(
            clause_ref=clause_ref,
            clause_text=clause_text,
            context_block=_context_block(context),
            action_name=action_name,
            value_block=value_block,
            ledger_block=ledger.prompt_block(),
            comparison_primitives="、".join(
                p for p in PRIMITIVE_VALUES if p not in BOOTSTRAP_ONLY_PRIMITIVES
            ),
        )
        parsed = self._ask(prompt)
        raw = parsed.get("facts")
        if not isinstance(raw, list):
            raw = []
        comparison_words = clause_has_comparison_word(clause_text)
        proposals: list[NewFactProposal] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            variable = str(entry.get("variable", "")).strip()
            if not variable:
                continue
            primitive = str(entry.get("primitive") or "passthrough").strip()
            demoted_from: Optional[str] = None
            demote_reason = ""
            if primitive != "passthrough":
                if primitive in BOOTSTRAP_ONLY_PRIMITIVES:
                    demoted_from, demote_reason = (
                        primitive,
                        f"{primitive} は新しい条文の new_facts では宣言できない(D23)",
                    )
                elif primitive not in PRIMITIVE_VALUES:
                    demoted_from, demote_reason = (
                        primitive,
                        f"知らないプリミティブ {primitive!r}(新しい一致プリミティブは"
                        "③aの範囲外。D20)",
                    )
                elif not comparison_words:
                    demoted_from, demote_reason = (
                        primitive,
                        "条文に2つの値を比べる語が無い(P38)",
                    )
                if demoted_from is not None:
                    primitive = "passthrough"
            values: list[str] = []
            dropped: list[str] = []
            reused_here: list[str] = []
            ignored: list[str] = []
            for value in entry.get("values") or []:
                text = str(value).strip().strip(_STRIP_CHARS).strip()
                normalized, _applied = normalize_value(text)
                if primitive != "passthrough":
                    # Q7: 比較型の候補の値はプリミティブの定義で決まる(D59)ので、
                    # 条文に無い値を落とす歯止め(K50)は当てない。記録だけ残す
                    if normalized:
                        ignored.append(normalized)
                elif normalized in allowed_values:
                    if normalized not in values:
                        values.append(normalized)
                elif normalized in reused:
                    # 手順3a で既存の fact に寄せた値(P37)。新規候補には入れない
                    reused_here.append(normalized)
                elif normalized:
                    # 条文の語句から作った値に無い値は層3に入れない
                    dropped.append(normalized)
            proposals.append(
                NewFactProposal(
                    variable=variable,
                    primitive=primitive,
                    values=values,
                    other_value_note=str(entry.get("other_value_note") or "").strip(),
                    comparison_kind=(
                        str(entry["comparison_kind"])
                        if entry.get("comparison_kind")
                        else None
                    ),
                    quotes=[quote_of[v] for v in values if v in quote_of],
                    dropped_values=dropped,
                    comparison_values_ignored=ignored,
                    reused_values=reused_here,
                    primitive_demoted_from=demoted_from,
                    demote_reason=demote_reason,
                    duplicate=ledger.duplicate_check(
                        variable, values, out_of_scope_pairs=out_of_scope_pairs
                    ),
                )
            )
        return proposals

    # ---- 本体 ----

    def extract(
        self,
        clause_text: str,
        clause_ref: str,
        ledger: FactLedger,
        *,
        action_name: str,
        action_surface: str = "",
        context: Optional[list[dict]] = None,
    ) -> FactExtractionResult:
        """1条文から fact を取り出す。層3は書き換えない(更新は呼び出し側の判断)。"""
        started = time.monotonic()
        self.calls = 0

        # 手順1・2
        conditions, rationale = self._extract_conditions(
            clause_text, clause_ref, action_name, action_surface, context
        )

        # 手順3a(プログラム): 値の完全一致。寄せる相手は同じ行為の fact に限る
        # (P37 改訂【v0.13】)
        matches = [ledger.match_value(c.value, action=action_name) for c in conditions]
        # 一致したかは条件ごとに見る(同じ値の条件が2件あっても取り違えない)
        unmatched = [c for c, m in zip(conditions, matches) if m.chosen is None]

        # 値が比較型の fact の値と同じだったか(P37 の一致の相手ではない。記録と疑い)
        comparison_hits = [
            {"value": c.value, "facts": hits}
            for c in conditions
            for hits in [ledger.comparison_value_matches(c.value)]
            if hits
        ]

        # 値は一致したが別の行為の fact だったもの(寄せずに手順3b に回す。P37 改訂)
        out_of_scope_hits = [
            {
                "value": m.value,
                "facts": [o["fact"] for o in m.out_of_scope],
                "actions": sorted(
                    {a for o in m.out_of_scope for a in (o.get("actions") or [])}
                ),
                "ancestor_only": m.ancestor_only,
            }
            for m in matches
            if m.out_of_scope
        ]

        # 手順3b(LLM): 提案だけ。寄せない(P37)
        suggestions = self._suggest_reuse([c.value for c in unmatched], ledger)

        # 手順4(LLM): 新規候補
        reused_values = [m.value for m in matches if m.chosen is not None]
        # Q1: 寄せなかった一致の (fact, 値)。新規候補が「既存の fact の値と同じ」で
        # 近いと判定されたとき、それが寄せ先の確認かどうかを分けるために渡す
        out_of_scope_pairs = {
            (o["fact"], m.value) for m in matches for o in m.out_of_scope
        }
        proposals = (
            self._propose_facts(
                clause_text,
                clause_ref,
                action_name,
                context,
                unmatched,
                ledger,
                reused_values=reused_values,
                out_of_scope_pairs=out_of_scope_pairs,
            )
            if unmatched
            else []
        )

        # 手順5(プログラム): sources と referenced_facts
        new_facts: list[PassthroughFact | ComparisonFact] = []
        for proposal in proposals:
            if not proposal.is_usable:
                continue
            sources = build_sources(proposal.variable, proposal.primitive)
            if proposal.primitive == "passthrough":
                new_facts.append(
                    PassthroughFact(
                        variable=proposal.variable,
                        possible_values=list(proposal.values),
                        other_value_note=proposal.other_value_note,
                        sources=sources,
                        approved_by=None,  # D53: 承認は人
                    )
                )
            else:
                new_facts.append(
                    ComparisonFact(
                        variable=proposal.variable,
                        primitive=proposal.primitive,
                        sources=sources,
                        comparison_kind=proposal.comparison_kind,
                        approved_by=None,
                    )
                )
        referenced: list[str] = []
        for match in matches:
            if match.chosen is not None and match.chosen not in referenced:
                referenced.append(match.chosen)
        facts = FactUsage(
            new_facts=new_facts,
            referenced_facts=referenced,
            # P37: 提案を人が受け入れたときに値を足す。前段では足さない
            value_additions={},
        )

        # Q1: fact_possible_duplicate が立った理由が「別の行為の fact と値が一致した」
        # ことだけなら、誤報ではなく**寄せ先の確認**として数える(ゲートは立てたまま)
        flagged_duplicates = [
            p for p in proposals if p.duplicate is not None and p.duplicate.flagged
        ]
        duplicate_flagged = bool(
            any(m.ambiguous for m in matches) or flagged_duplicates
        )
        duplicate_scope_only = bool(
            duplicate_flagged
            and not any(m.ambiguous for m in matches)
            and flagged_duplicates
            and all(
                p.duplicate is not None and p.duplicate.scope_check_only
                for p in flagged_duplicates
            )
        )

        gates = ReviewGates(
            new_fact_unapproved=bool(
                [f for f in new_facts if f.approved_by is None]
            ),
            fact_value_unverified=(
                any(not c.ok for c in conditions)
                or any(p.dropped_values for p in proposals)
                or not conditions
            ),
            fact_possible_duplicate=duplicate_flagged,
            comparison_fact_proposed=any(p.is_comparison for p in proposals),
            fact_reuse_suggested=any(s.answer is not None for s in suggestions),
        )
        trace = FactExtractionTrace(
            temperature=self.temperature,
            conditions=conditions,
            rationale=rationale,
            matches=matches,
            suggestions=suggestions,
            proposals=proposals,
            comparison_value_hits=comparison_hits,
            out_of_scope_hits=out_of_scope_hits,
            reuse_scope=list(ledger.action_scope(action_name) or []),
            duplicate_scope_only=duplicate_scope_only,
            sources_note=SOURCES_NOTE,
            elapsed_seconds=time.monotonic() - started,
            llm_calls=self.calls,
        )
        return FactExtractionResult(facts=facts, trace=trace, gates=gates)


# --------------------------------------------------------------------------
# LLM の実装(第1要素と同じものを使う)
# --------------------------------------------------------------------------


class OllamaJsonLlm:
    """②と同じローカルのモデル(Ollama)で動かす(P11)。"""

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 600,
    ) -> None:
        from src.common.ollama_client import DEFAULT_MODEL, DEFAULT_OLLAMA_BASE_URL

        self.model = model or DEFAULT_MODEL
        self.base_url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        self.timeout = timeout

    def complete_json(self, prompt: str, *, temperature: float) -> dict:
        from src.common.ollama_client import call_ollama_json

        return call_ollama_json(
            prompt,
            model=self.model,
            base_url=self.base_url,
            timeout=self.timeout,
            temperature=temperature,
        )


class ScriptedJsonLlm:
    """決められた応答を順に返す LLM(単体テスト用。Ollama なしで通る)。"""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.temperatures: list[float] = []

    def complete_json(self, prompt: str, *, temperature: float) -> dict:
        self.prompts.append(prompt)
        self.temperatures.append(temperature)
        if not self.responses:
            raise AssertionError("ScriptedJsonLlm: 応答が足りません")
        return self.responses.pop(0)
