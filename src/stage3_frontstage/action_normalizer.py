"""Action 正規化(前段設計書4章 v0.4)。③a 前段の第1要素。

条文から行為の語句を逐語で抜き出し、Action 台帳の中から1つ選ぶか「該当なし」を選ぶ
(閉じた選択。P5)。LLM には②と同じローカルのモデルを使い(P11)、結果は独立したゲートで
人のレビューに回す(4.5節)。

## 手順(4.2節 v0.4)と、この実装での対応

| 手順 | 中身 | 実装 |
| --- | --- | --- |
| 1 | 行為の語句を逐語で抜き出す(LLM。候補は2つまで・中心の動詞も) | `_extract_surface` |
| 2 | 末尾の様相をプログラムで削り、条文にあるかを確かめる | `modality.check_surface` |
| 3 | 台帳から選ぶか「該当なし」を選ぶ(LLM。段に合わせる) | `_choose_action` |
| 4 | 動詞の一致をプログラムで確かめる | `_check_verb` |
| 5 | 「該当なし」なら新規候補の名前・定義・段(LLM) | `_propose_action` |

1件あたりの LLM の呼び出しは2回(新規候補なら3回)である(k=1。P7)。

## v0.4(第31回)での変更

- **k=1 にした**(P7)。初回評価で、誤り7件はすべて3回とも同じ答えだったので、
  自己一貫性は系統的な誤りに効かなかった。`action_mapping_unstable` は廃止
- **様相を削るのはプログラム**(P4)。LLM は逐語の語句を抜き出すだけ
- **候補を2つまで**出させ、2つなら `action_surface_ambiguous`(P17)
- **動詞の一致の検査**を足した(P15)。既存に寄せたとき、語句の中心の動詞が、
  選んだ Action の過去の語句の動詞にあるかを見る
- **抽象的な段を選んだら** `action_abstract_selected`(P15)
- **名前と定義に様相の語があれば** `action_name_rule`(P16)

## 判断したこと(設計書に書かれていない点)

- **中心の動詞の比べ方**: 完全一致、またはどちらかがもう片方を含むこと
  (「従事」と「従事させ」のような送り仮名の差を同じ動詞として扱う)。形態素解析は
  使わない(②と同じく、依存を増やさずに済む範囲で済ませる)
- **出力に使う語句**: 候補が2つ出たときは1つ目を使う。2つとも Trace に残し、
  `action_surface_ambiguous` で人に回す(どちらが規制の対象かは人の判断。P17)
- **選択のプロンプトに渡す語句**: 1つ目の候補(様相を削った後)
- **名前・定義の規則の検査**: 様相の語だけをプログラムで見る(P16)。条件
  (誰が・どこで・いつ)が入っているかは、プログラムでは判定できないので人に回す
- **台帳にない名前を返してきたら**「該当なし」として扱い、理由にその旨を残す(P5)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from src.stage3_generate.ir_schema import ActionRef, ReviewGates
from src.stage3_generate.registry import ACTION_ABSTRACT, ACTION_CONCRETE

from .action_ledger import NO_MATCH, ActionLedger, DuplicateCheck
from .modality import (
    MODALITY_WORDS,
    NORMATIVE_MODALITY_WORDS,
    SurfaceCheck,
    check_surface,
    find_normative_modality,
)

# P7: 正規化は1回だけ実行する。再現性のため温度は 0.0(②と同じ方針)。
TEMPERATURE = 0.0
# P17: 行為の語句の候補は2つまで。
MAX_SURFACE_CANDIDATES = 2


class JsonLlm(Protocol):
    """JSON を返す LLM。単体テストでは固定の応答を返す実装に差し替える(5.2節)。"""

    def complete_json(self, prompt: str, *, temperature: float) -> dict: ...


# --------------------------------------------------------------------------
# 出力の型
# --------------------------------------------------------------------------


@dataclass
class SurfaceCandidate:
    """行為の語句の候補1つ(手順1・手順2の結果)。"""

    check: SurfaceCheck
    core_verb: str

    @property
    def surface(self) -> str:
        return self.check.surface

    def to_json(self) -> dict:
        return {**self.check.to_json(), "core_verb": self.core_verb}


@dataclass
class VerbCheck:
    """動詞の一致の検査(手順4。P15)。"""

    core_verb: str
    compared: list[str]
    matched: bool
    reason: str

    def to_json(self) -> dict:
        return {
            "core_verb": self.core_verb,
            "compared": list(self.compared),
            "matched": self.matched,
            "reason": self.reason,
        }


@dataclass
class NormalizationRun:
    """1回分の結果(設計書4.4 の run)。"""

    temperature: float
    candidates: list[SurfaceCandidate]
    choice: str
    reason: str
    definition_part: str
    considered: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "temperature": self.temperature,
            "candidates": [c.to_json() for c in self.candidates],
            "surface": self.candidates[0].surface if self.candidates else "",
            "core_verb": self.candidates[0].core_verb if self.candidates else "",
            "removed_modality": (
                list(self.candidates[0].check.removed_modality) if self.candidates else []
            ),
            "choice": self.choice,
            "reason": self.reason,
            "definition_part": self.definition_part,
            "considered": [dict(c) for c in self.considered],
        }


@dataclass
class NewActionProposal:
    """新規候補の名前・定義・段の案と、親の案(設計書4.4。親の案はここだけ。P6)。"""

    name: str
    definition: str
    kind: str = ACTION_CONCRETE
    parent_suggestion: Optional[str] = None
    basis_quotes: list[str] = field(default_factory=list)
    basis_verified: list[bool] = field(default_factory=list)
    name_modality: list[str] = field(default_factory=list)
    definition_modality: list[str] = field(default_factory=list)

    @property
    def breaks_name_rule(self) -> bool:
        """名前または定義に規範の様相の語がある(P16)。"""
        return bool(self.name_modality or self.definition_modality)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "definition": self.definition,
            "kind": self.kind,
            "parent_suggestion": self.parent_suggestion,
            "basis_quotes": list(self.basis_quotes),
            "basis_verified": list(self.basis_verified),
            "name_modality": list(self.name_modality),
            "definition_modality": list(self.definition_modality),
        }


@dataclass
class ActionNormalizationTrace:
    """レビューと評価のための記録(設計書4.4)。IR には入れない。"""

    run: NormalizationRun
    verb_check: Optional[VerbCheck] = None
    candidates_considered: list[dict] = field(default_factory=list)
    new_action_proposal: Optional[NewActionProposal] = None
    duplicate_check: Optional[DuplicateCheck] = None
    elapsed_seconds: float = 0.0
    llm_calls: int = 0

    def to_json(self) -> dict:
        return {
            "run": self.run.to_json(),
            "verb_check": self.verb_check.to_json() if self.verb_check else None,
            "candidates_considered": [dict(c) for c in self.candidates_considered],
            "new_action_proposal": (
                self.new_action_proposal.to_json() if self.new_action_proposal else None
            ),
            "duplicate_check": (
                self.duplicate_check.to_json() if self.duplicate_check else None
            ),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "llm_calls": self.llm_calls,
        }


@dataclass
class ActionNormalizationResult:
    """前段の第1要素の出力。IR の ActionRef と、その外側の記録・ゲート。"""

    action: ActionRef
    trace: ActionNormalizationTrace
    gates: ReviewGates
    # 既存を選んだ場合の、その Action の段(台帳から引く)。新規の場合は使わない
    _existing_kind: str = ACTION_CONCRETE

    @property
    def chosen_kind(self) -> str:
        """選んだ(または提案した)Action の段。"""
        proposal = self.trace.new_action_proposal
        if proposal is not None:
            return proposal.kind
        return self._existing_kind

    def to_json(self) -> dict:
        return {
            "action": {
                "surface": self.action.surface,
                "normalized": self.action.normalized,
                "parent": self.action.parent,
                "is_new": self.action.is_new,
                "approved_by": self.action.approved_by,
            },
            "kind": self.chosen_kind,
            "trace": self.trace.to_json(),
            "gates": self.gates.gate_names(),
            "gate_reasons": self.gates.reasons(),
        }


# --------------------------------------------------------------------------
# プロンプト(②で効いた形を踏襲: 逐語で根拠の語句を出させる。P11)
# --------------------------------------------------------------------------


def _context_block(context: Optional[list[dict]]) -> str:
    """出典付きの context をプロンプトに入れる(IR v2 1.4節)。

    context は「条文の外から出典付きで明示的に付与した情報」で、条文本文ではない。
    逐語で抜き出す対象は条文本文だけなので、その区別をプロンプトに書く。
    """
    if not context:
        return ""
    lines = [f"- {c['text']}（出典: {c.get('where', '不明')}）" for c in context]
    return (
        "\n出典付きの補足情報(条文がどこに書かれているかを示すもの。"
        "行為の語句はここからではなく、条文本文から抜き出すこと):\n"
        + "\n".join(lines)
        + "\n"
    )


SURFACE_PROMPT = """あなたは就業規則・情報セキュリティ規程などの業務規程の条文を読み、
その条文が規制している「行為」を取り出すアシスタントです。

条文(参照: {clause_ref}):
\"\"\"{clause_text}\"\"\"
{context_block}
この条文が規制の対象にしている行為を表す語句を、条文本文から**一言一句そのまま**
抜き出してください。次の規則を守ってください。

1. 条文本文に書かれている文字列をそのまま使う(要約・言い換え・語の追加をしない)
2. 「してはならない」「しないこと」などの語尾は、**そのまま付けたままでよい**
   (こちらのプログラムで外します)
3. 誰が・どこで・いつ・どの区分かといった条件は、できるだけ含めない
   (それらは別の欄で扱います)
4. **文末の述語だけを見ないこと。**「〜は」「〜が」の直後にある体言が規制の対象の
   行為であることがあります
   (例:「特権操作は多要素認証を用い、…私有機器を使用しないこと」では、規制の対象の
    行為は文頭の「特権操作」で、「私有機器を使用」はその手段の制約です)
5. どちらが規制の対象か決められない場合に限り、候補を**2つまで**挙げてよい
   (3つ以上は挙げない。1つに決まるならば1つだけ挙げる)
6. それぞれの候補について、その行為の**中心の動詞**を、候補の語句の中から
   一言一句そのまま抜き出す(例:「口頭で伝達」→「伝達」、「放置しない」→「放置」)

出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):
{{"candidates": [{{"surface": "条文から一言一句そのまま抜き出した行為の語句",
   "core_verb": "その語句の中心の動詞(語句の中から一言一句そのまま)"}}],
 "rationale": "なぜその語句を選んだかを日本語で1文"}}
"""

CHOICE_PROMPT = """あなたは業務規程の条文に出てくる行為を、既存の「Action 台帳」の
どれに当たるかを判断するアシスタントです。Action 台帳は、あるAIエージェントが
実行時に要求する行為の一覧(職務記述書)です。

台帳は2段からなります。
- **具体的な行為**: 手段が決まっている行為(例: 口頭で伝える、電子メールで送る)
- **抽象的な行為**: 手段を問わない行為(例: 手段を問わず情報を渡す)。具体的な行為を
  子に持ち、抽象的な行為への禁止は子すべてに及びます

条文(参照: {clause_ref}):
\"\"\"{clause_text}\"\"\"
{context_block}
この条文から抜き出した行為の語句: 「{surface}」（中心の動詞: 「{core_verb}」）

Action 台帳:
{ledger_block}

この行為が、台帳のどれに当たるかを1つだけ選んでください。台帳のどれにも当たらない
場合は「{no_match}」を選んでください。次の規則を守ってください。

1. 選べるのは、台帳にある名前そのままか、「{no_match}」のどちらかだけです。
   新しい名前を作ってはいけません
2. 行為の**種類**が同じかどうかで判断してください。誰が・どこで・いつ・どの区分かと
   いった条件が違っても、行為の種類が同じなら同じ Action です
   (例:「時間外労働に従事させる」と「休日労働に従事させる」は同じ行為)
3. **段を合わせてください。**条文の動詞が手段を問わないなら抽象的な行為を、
   手段が決まっているなら具体的な行為を選びます
   (例:「提供してはならない」は手段を問わないので抽象的な行為。
    「口頭で伝達してはならない」は手段が口頭なので具体的な行為)
4. 具体的な行為どうしでは、**手段**や**向き**が違えば別の Action です
   (例:「口頭で伝える」と「電子メールで送る」は手段が違う。
    「送る」と「取り出す」は向きが違う)
5. 行為の**主体**が違えば、別の Action です
   (例:「自ら働く」と「人を働かせる」は主体が違う)
6. 候補の「この Action に含まないもの」に書かれている行為であれば、それを選ばない
7. 迷ったときは「{no_match}」を選んでください。誤って既存の Action に寄せると、
   実行時にその条文の規制が別の判定に入ってしまいます

出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):
{{"choice": "台帳にある Action の名前、または {no_match}",
 "reason": "選んだ理由を日本語で1〜2文",
 "definition_part": "選んだ Action の定義のどの部分に当たるかを、定義から一言一句そのまま抜き出したもの(「{no_match}」のときは空文字列)",
 "considered": [{{"name": "検討したが選ばなかった Action の名前",
  "why_not": "選ばなかった理由を日本語で1文"}}]}}
"""

PROPOSAL_PROMPT = """あなたは業務規程の条文に出てくる行為に、新しい Action の名前と
定義を付けるアシスタントです。

条文(参照: {clause_ref}):
\"\"\"{clause_text}\"\"\"
{context_block}
この条文から抜き出した行為の語句: 「{surface}」（中心の動詞: 「{core_verb}」）

既存の Action 台帳(この行為はどれにも当たらないと判断されました):
{ledger_block}

この行為に付ける新しい Action の名前・定義・段の案を出してください。次の規則を
守ってください。

1. 名前は体言にする(例: 〜操作、〜使用、〜提供、〜伝達)
2. 名前は行為の種類を表す。目的語や手段は、行為の種類を区別するのに必要なときだけ
   含める(例:「口頭で伝達」→ 口頭伝達。手段を含めないと電子メールでの送信と
   区別できないため)
3. **規範の様相(禁止・不可・不許可など)を名前と定義に入れない。**
   Action は「行為の種類」であって「禁止された行為」ではありません
   (例: ×「区域立ち入り禁止」→ ○「立入り」、×「労働従事禁止」→ ○「労働指示」)
4. **条件(誰が・どこで・いつ・どの区分か)を名前と定義に入れない。**
   それらは別の欄(決定表の列)で扱います
   (例: ×「本番環境において特権操作を行うこと」→ ○「特権/管理者権限で操作すること」、
    ×「酒気を帯びて就業すること」→ ○「業務に就くこと」)
5. 数字は半角にする
6. 定義は1文で、その行為が何を指すかを述べる。条文に出てこない**一般的な言い換えや
   上位の語は使ってよい**(例:「放置」→「情報や物を、管理されない状態で置いたままに
   すること」)。ただし、条文に書かれていない具体例・範囲・業務知識は足さない
   (例: ×「USBメモリやクラウドに送ること」)
7. 定義の根拠にした条文中の語句を、一言一句そのまま basis_quotes に挙げる
8. 段は、この行為の動詞が手段を問わないなら "abstract"、手段が決まっているなら
   "concrete"
9. 親の案は、既存の Action の名前か、上位の行為の種類を表す体言。思い当たらなければ
   null

出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):
{{"name": "新しい Action の名前(体言)",
 "definition": "その行為が何を指すかの1文の定義",
 "kind": "concrete または abstract",
 "parent": "親の案、または null",
 "basis_quotes": ["定義の根拠にした条文中の語句を、一言一句そのまま抜き出したもの"]}}
"""


# --------------------------------------------------------------------------
# 正規化
# --------------------------------------------------------------------------


def core_verbs_match(core_verb: str, past_verbs: list[str]) -> tuple[bool, str]:
    """中心の動詞が、過去の語句の動詞のどれかと一致するか(手順4。P15)。

    完全一致、またはどちらかがもう片方を含むことを「一致」とする。送り仮名の差
    (「従事」と「従事させ」)を同じ動詞として扱うためで、形態素解析は使わない。
    """
    if not core_verb:
        return False, "中心の動詞が空(抜き出しに失敗している)"
    if not past_verbs:
        return False, "選んだ Action に過去の語句(中心の動詞)が無い"
    for past in past_verbs:
        if core_verb == past:
            return True, f"過去の語句の動詞 {past!r} と一致"
        if core_verb in past or past in core_verb:
            return True, f"過去の語句の動詞 {past!r} と送り仮名の差だけで一致"
    return False, f"過去の語句の動詞 {past_verbs} のどれとも一致しない"


class ActionNormalizer:
    """Action 正規化の本体。LLM は差し替えられる(単体テスト用。5.2節)。"""

    def __init__(self, llm: JsonLlm, *, temperature: float = TEMPERATURE) -> None:
        self.llm = llm
        self.temperature = temperature
        self.calls = 0

    # ---- 各手順 ----

    def _ask(self, prompt: str) -> dict:
        self.calls += 1
        return self.llm.complete_json(prompt, temperature=self.temperature)

    def _extract_surface(
        self,
        clause_text: str,
        clause_ref: str,
        context: Optional[list[dict]],
    ) -> tuple[list[SurfaceCandidate], str]:
        """手順1と手順2。候補ごとに様相を削り、条文にあるかを確かめる。"""
        prompt = SURFACE_PROMPT.format(
            clause_ref=clause_ref,
            clause_text=clause_text,
            context_block=_context_block(context),
        )
        parsed = self._ask(prompt)
        raw = parsed.get("candidates")
        if not isinstance(raw, list) or not raw:
            # 1つだけ返す形(旧版のプロンプトに合わせた応答)も受ける
            raw = [{"surface": parsed.get("surface", ""), "core_verb": parsed.get("core_verb", "")}]
        candidates: list[SurfaceCandidate] = []
        for item in raw[:MAX_SURFACE_CANDIDATES]:
            if not isinstance(item, dict):
                continue
            check = check_surface(str(item.get("surface", "")), clause_text)
            verb, _removed = _strip_modality_from_verb(str(item.get("core_verb", "")))
            candidates.append(SurfaceCandidate(check=check, core_verb=verb))
        return candidates, str(parsed.get("rationale", ""))

    def _choose_action(
        self,
        clause_text: str,
        clause_ref: str,
        context: Optional[list[dict]],
        candidate: SurfaceCandidate,
        ledger: ActionLedger,
    ) -> dict:
        prompt = CHOICE_PROMPT.format(
            clause_ref=clause_ref,
            clause_text=clause_text,
            context_block=_context_block(context),
            surface=candidate.surface,
            core_verb=candidate.core_verb,
            ledger_block=ledger.prompt_block(),
            no_match=NO_MATCH,
        )
        return self._ask(prompt)

    def _propose_action(
        self,
        clause_text: str,
        clause_ref: str,
        context: Optional[list[dict]],
        candidate: SurfaceCandidate,
        ledger: ActionLedger,
    ) -> NewActionProposal:
        prompt = PROPOSAL_PROMPT.format(
            clause_ref=clause_ref,
            clause_text=clause_text,
            context_block=_context_block(context),
            surface=candidate.surface,
            core_verb=candidate.core_verb,
            ledger_block=ledger.prompt_block(),
        )
        parsed = self._ask(prompt)
        quotes = [str(q) for q in (parsed.get("basis_quotes") or [])]
        parent = parsed.get("parent")
        name = str(parsed.get("name", "")).strip()
        definition = str(parsed.get("definition", "")).strip()
        kind = str(parsed.get("kind") or ACTION_CONCRETE).strip()
        return NewActionProposal(
            name=name,
            definition=definition,
            kind=kind if kind in (ACTION_CONCRETE, ACTION_ABSTRACT) else ACTION_CONCRETE,
            parent_suggestion=(str(parent) if parent else None),
            basis_quotes=quotes,
            # 定義の根拠の語句が条文に実在するかをプログラムで確かめる(PU4)
            basis_verified=[q.strip().strip("「」") in clause_text for q in quotes],
            # P16: 名前と定義に規範の様相の語が入っていないか。打ち消しだけの
            # 「ない」は見ない(定義は否定で状態を説明することがある。modality.py)
            name_modality=find_normative_modality(name),
            definition_modality=find_normative_modality(definition),
        )

    # ---- 本体 ----

    def normalize(
        self,
        clause_text: str,
        clause_ref: str,
        ledger: ActionLedger,
        *,
        context: Optional[list[dict]] = None,
    ) -> ActionNormalizationResult:
        """1条文を正規化する。台帳は書き換えない(台帳の更新は呼び出し側の判断)。"""
        started = time.monotonic()
        self.calls = 0
        allowed = set(ledger.names())

        candidates, _rationale = self._extract_surface(clause_text, clause_ref, context)
        if not candidates:
            # 抜き出しに失敗(応答が空)。条文そのものを語句として扱い、ゲートに乗せる
            candidates = [
                SurfaceCandidate(check=check_surface("", clause_text), core_verb="")
            ]
        primary = candidates[0]

        chosen = self._choose_action(
            clause_text, clause_ref, context, primary, ledger
        )
        choice = str(chosen.get("choice", "")).strip()
        if choice not in allowed and choice != NO_MATCH:
            # 閉じた選択を外れた答え(台帳にない名前)は「該当なし」として扱い、
            # 理由にその旨を残す(新しい名前を作らせないため。P5)
            reason = (
                f"[台帳にない名前 {choice!r} が返されたので「{NO_MATCH}」として"
                f"扱った] " + str(chosen.get("reason", ""))
            )
            choice = NO_MATCH
        else:
            reason = str(chosen.get("reason", ""))

        run = NormalizationRun(
            temperature=self.temperature,
            candidates=candidates,
            choice=choice,
            reason=reason,
            definition_part=str(chosen.get("definition_part", "")),
            considered=[
                {"name": str(c.get("name", "")), "why_not": str(c.get("why_not", ""))}
                for c in (chosen.get("considered") or [])
                if isinstance(c, dict)
            ],
        )

        proposal: Optional[NewActionProposal] = None
        duplicate: Optional[DuplicateCheck] = None
        verb_check: Optional[VerbCheck] = None
        existing_kind = ACTION_CONCRETE
        if choice == NO_MATCH:
            proposal = self._propose_action(
                clause_text, clause_ref, context, primary, ledger
            )
            duplicate = ledger.duplicate_check(proposal.name, proposal.definition)
            normalized = proposal.name
            is_new = True
        else:
            normalized = choice
            is_new = False
            entry = ledger.find(choice)
            past_verbs = entry.core_verbs() if entry else []
            matched, why = core_verbs_match(primary.core_verb, past_verbs)
            verb_check = VerbCheck(
                core_verb=primary.core_verb,
                compared=past_verbs,
                matched=matched,
                reason=why,
            )
            existing_kind = entry.kind if entry else ACTION_CONCRETE

        action = ActionRef(
            surface=primary.surface,
            normalized=normalized,
            parent=None,  # P6: 前段は親を書かない
            is_new=is_new,
            approved_by=None,  # D53: 承認は人
        )
        gates = ReviewGates(
            new_action_unapproved=is_new and action.approved_by is None,
            action_surface_unverified=not primary.check.ok,
            action_surface_ambiguous=len(candidates) > 1,
            action_verb_mismatch=bool(verb_check and not verb_check.matched),
            action_abstract_selected=(
                existing_kind == ACTION_ABSTRACT
                if not is_new
                else proposal is not None and proposal.kind == ACTION_ABSTRACT
            ),
            action_name_rule=bool(proposal and proposal.breaks_name_rule),
            action_possible_duplicate=bool(duplicate and duplicate.flagged),
        )
        trace = ActionNormalizationTrace(
            run=run,
            verb_check=verb_check,
            candidates_considered=run.considered,
            new_action_proposal=proposal,
            duplicate_check=duplicate,
            elapsed_seconds=time.monotonic() - started,
            llm_calls=self.calls,
        )
        return ActionNormalizationResult(
            action=action, trace=trace, gates=gates, _existing_kind=existing_kind
        )


def _strip_modality_from_verb(core_verb: str) -> tuple[str, list[str]]:
    """中心の動詞から様相を落とす(「放置しない」→「放置」)。

    中心の動詞は語句の中から抜き出させるので、禁止の条文では様相が付いてくる。
    動詞の一致の検査(P15)で比べるのは行為の側なので、ここでも落とす。
    """
    from .modality import strip_modality

    cleaned = core_verb.strip().strip("「」『』\"'“” 　\n\t。、").strip()
    return strip_modality(cleaned)


# --------------------------------------------------------------------------
# LLM の実装
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


def modality_examples() -> tuple[str, ...]:
    """プログラムが削る様相の語(報告用)。"""
    return MODALITY_WORDS


def name_rule_words() -> tuple[str, ...]:
    """名前・定義の検査に使う様相の語(報告用。P16)。"""
    return NORMATIVE_MODALITY_WORDS
