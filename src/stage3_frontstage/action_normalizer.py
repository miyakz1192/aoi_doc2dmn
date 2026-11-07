"""Action 正規化(前段設計書4章)。③a 前段の第1要素。

条文から行為の語句を逐語で抜き出し、Action 台帳の中から1つ選ぶか「該当なし」を選ぶ
(閉じた選択。P5)。LLM には②と同じローカルのモデルを使い(P11)、結果は独立したゲートで
人のレビューに回す(4.5節)。

## 手順(4.2節)と、この実装での対応

| 手順 | 中身 | 実装 |
| --- | --- | --- |
| 1 | 行為の語句を逐語で抜き出す(LLM) | `_extract_surface` |
| 2 | 条文の部分文字列か・様相の語を含まないかを確かめる(プログラム) | `modality.check_surface` |
| 3 | 台帳から選ぶか「該当なし」を選ぶ(LLM) | `_choose_action` |
| 4 | k 回実行して選択の一致を見る(プログラム) | `normalize` の多数決 |
| 5 | 「該当なし」なら新規候補の名前と定義の案(LLM) | `_propose_action` |

## 判断したこと(設計書に書かれていない点。第29回 HANDOFF 5.2節)

- **k 回の実行で温度を変える**: `call_ollama_json` は再現性のため temperature=0.0 で
  呼ぶ。0.0 は決定論的デコードなので、同じ入力を3回送っても同じ答えが返り、
  自己一貫性(P7)が常に 3/3 になって**ゲートが機能しない**(確信度の自己申告が常に
  高くて使えなかったのと同じ失敗になる)。そこで1回目は 0.0、2回目以降は 0.3 で
  呼ぶ。1回目の答えは従来どおり再現でき、2回目以降でデコード経路を変えて揺れを
  観測する。`TEMPERATURES` を参照
- **手順1と手順3を別の呼び出しにする**: 1回の呼び出しでまとめると、手順2(逐語の
  確認)が選択の後になり、設計書4.2の順序が崩れる。1件あたりの呼び出し数は
  k×2(＋「該当なし」なら1)になる
- **多数決が割れたとき**: 最も多い選択を採り、同数なら1回目(temperature=0.0)の
  選択を採る。どちらの場合も `action_mapping_unstable` が立つので、人が見る
- **surface の採り方**: 多数決で採った選択をした最初の実行の surface を出力に使う。
  実行ごとに surface が違う場合は `trace.runs` に全部残す
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Protocol

from src.stage3_generate.ir_schema import ActionRef, ReviewGates

from .action_ledger import NO_MATCH, ActionEntry, ActionLedger, DuplicateCheck
from .modality import MODALITY_WORDS, SurfaceCheck, check_surface

# k 回の実行の温度(上の docstring 参照)。len(TEMPERATURES) が k の既定値になる。
TEMPERATURES: tuple[float, ...] = (0.0, 0.3, 0.3)


class JsonLlm(Protocol):
    """JSON を返す LLM。単体テストでは固定の応答を返す実装に差し替える(5.2節)。"""

    def complete_json(self, prompt: str, *, temperature: float) -> dict: ...


# --------------------------------------------------------------------------
# 出力の型
# --------------------------------------------------------------------------


@dataclass
class NormalizationRun:
    """k 回のうちの1回分(設計書4.4 の runs)。"""

    index: int
    temperature: float
    surface: str
    surface_check: SurfaceCheck
    choice: str
    reason: str
    definition_part: str
    considered: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "index": self.index,
            "temperature": self.temperature,
            "surface": self.surface,
            "surface_ok": self.surface_check.ok,
            "surface_problems": self.surface_check.reasons(),
            "choice": self.choice,
            "reason": self.reason,
            "definition_part": self.definition_part,
            "considered": [dict(c) for c in self.considered],
        }


@dataclass
class NewActionProposal:
    """新規候補の名前・定義の案・親の案(設計書4.4。親の案はここにだけ置く。P6)。"""

    name: str
    definition: str
    parent_suggestion: Optional[str] = None
    basis_quotes: list[str] = field(default_factory=list)
    basis_verified: list[bool] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "definition": self.definition,
            "parent_suggestion": self.parent_suggestion,
            "basis_quotes": list(self.basis_quotes),
            "basis_verified": list(self.basis_verified),
        }


@dataclass
class ActionNormalizationTrace:
    """レビューと評価のための記録(設計書4.4)。IR には入れない。"""

    runs: list[NormalizationRun]
    agreement: str
    candidates_considered: list[dict]
    new_action_proposal: Optional[NewActionProposal] = None
    duplicate_check: Optional[DuplicateCheck] = None
    elapsed_seconds: float = 0.0
    llm_calls: int = 0

    def to_json(self) -> dict:
        return {
            "runs": [r.to_json() for r in self.runs],
            "agreement": self.agreement,
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

    def to_json(self) -> dict:
        return {
            "action": {
                "surface": self.action.surface,
                "normalized": self.action.normalized,
                "parent": self.action.parent,
                "is_new": self.action.is_new,
                "approved_by": self.action.approved_by,
            },
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
    lines = [
        f"- {c['text']}（出典: {c.get('where', '不明')}）" for c in context
    ]
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
2. 規範の様相を表す語を含めない。含めてはいけない語の例: {modality_examples}
   例:「使用しないこと」→「会社の施設、物品等を使用」(「しないこと」を含めない)
   例:「口頭で伝達してはならない」→「口頭で伝達」
3. 使役の「させ」は様相ではないので残す。
   例:「労働に従事させない」→「労働に従事させ」(「ない」だけを外す)
4. 誰が・どこで・いつといった条件は、できるだけ含めない(それらは別の欄で扱う)
5. 1つだけ抜き出す。条文が2つ以上の行為を規制している場合は、主たる行為を1つ選ぶ

出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):
{{"surface": "条文から一言一句そのまま抜き出した行為の語句",
 "rationale": "なぜその語句を選んだかを日本語で1文"}}
"""

CHOICE_PROMPT = """あなたは業務規程の条文に出てくる行為を、既存の「Action 台帳」の
どれに当たるかを判断するアシスタントです。Action 台帳は、あるAIエージェントが
実行時に要求する行為の一覧(職務記述書)です。

条文(参照: {clause_ref}):
\"\"\"{clause_text}\"\"\"
{context_block}
この条文から抜き出した行為の語句: 「{surface}」

Action 台帳:
{ledger_block}

この行為が、台帳のどれに当たるかを1つだけ選んでください。台帳のどれにも当たらない
場合は「{no_match}」を選んでください。次の規則を守ってください。

1. 選べるのは、台帳にある名前そのままか、「{no_match}」のどちらかだけです。
   新しい名前を作ってはいけません
2. 行為の**種類**が同じかどうかで判断してください。誰が・どこで・いつ・どの区分かと
   いった条件が違っても、行為の種類が同じなら同じ Action です
   (例:「時間外労働に従事させる」と「休日労働に従事させる」は同じ行為)
3. 行為の**手段**や**向き**が違えば、別の Action です
   (例:「口頭で伝える」と「電子メールで送る」は手段が違う。
    「送る」と「取り出す」は向きが違う)
4. 行為の**主体**が違えば、別の Action です
   (例:「自ら働く」と「人を働かせる」は主体が違う)
5. 迷ったときは「{no_match}」を選んでください。誤って既存の Action に寄せると、
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
この条文から抜き出した行為の語句: 「{surface}」

既存の Action 台帳(この行為はどれにも当たらないと判断されました):
{ledger_block}

この行為に付ける新しい Action の名前と定義の案を出してください。次の規則を
守ってください。

1. 名前は体言にする(例: 〜操作、〜使用、〜提供、〜伝達)
2. 名前は行為の種類を表す。目的語や手段は、行為の種類を区別するのに必要なときだけ
   含める(例:「口頭で伝達」→ 口頭伝達。手段を含めないと電子メールでの送信と
   区別できないため)
3. 条件(誰が・どこで・いつ・どの区分か)は名前に含めない。それらは別の欄で扱う
   (例:「本番環境で特権操作」→ 特権操作)
4. 数字は半角にする
5. 定義は1文で、その行為が何を指すかを述べる。**条文に書かれていない説明を
   足さない**。定義の根拠にした条文中の語句を、一言一句そのまま basis_quotes に
   挙げること
6. 親の案は、既存の Action の名前か、上位の行為の種類を表す体言。思い当たらなければ
   null

出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):
{{"name": "新しい Action の名前(体言)",
 "definition": "その行為が何を指すかの1文の定義",
 "parent": "親の案、または null",
 "basis_quotes": ["定義の根拠にした条文中の語句を、一言一句そのまま抜き出したもの"]}}
"""


# --------------------------------------------------------------------------
# 正規化
# --------------------------------------------------------------------------


class ActionNormalizer:
    """Action 正規化の本体。LLM は差し替えられる(単体テスト用。5.2節)。"""

    def __init__(
        self,
        llm: JsonLlm,
        *,
        temperatures: tuple[float, ...] = TEMPERATURES,
    ) -> None:
        self.llm = llm
        self.temperatures = temperatures
        self.calls = 0

    # ---- 各手順 ----

    def _ask(self, prompt: str, temperature: float) -> dict:
        self.calls += 1
        return self.llm.complete_json(prompt, temperature=temperature)

    def _extract_surface(
        self,
        clause_text: str,
        clause_ref: str,
        context: Optional[list[dict]],
        temperature: float,
    ) -> tuple[str, str]:
        prompt = SURFACE_PROMPT.format(
            clause_ref=clause_ref,
            clause_text=clause_text,
            context_block=_context_block(context),
            modality_examples="、".join(
                ["してはならない", "しないこと", "禁止", "ない"]
            ),
        )
        parsed = self._ask(prompt, temperature)
        return str(parsed.get("surface", "")), str(parsed.get("rationale", ""))

    def _choose_action(
        self,
        clause_text: str,
        clause_ref: str,
        context: Optional[list[dict]],
        surface: str,
        ledger: ActionLedger,
        temperature: float,
    ) -> dict:
        prompt = CHOICE_PROMPT.format(
            clause_ref=clause_ref,
            clause_text=clause_text,
            context_block=_context_block(context),
            surface=surface,
            ledger_block=ledger.prompt_block(),
            no_match=NO_MATCH,
        )
        return self._ask(prompt, temperature)

    def _propose_action(
        self,
        clause_text: str,
        clause_ref: str,
        context: Optional[list[dict]],
        surface: str,
        ledger: ActionLedger,
    ) -> NewActionProposal:
        prompt = PROPOSAL_PROMPT.format(
            clause_ref=clause_ref,
            clause_text=clause_text,
            context_block=_context_block(context),
            surface=surface,
            ledger_block=ledger.prompt_block(),
        )
        parsed = self._ask(prompt, self.temperatures[0])
        quotes = [str(q) for q in (parsed.get("basis_quotes") or [])]
        parent = parsed.get("parent")
        return NewActionProposal(
            name=str(parsed.get("name", "")).strip(),
            definition=str(parsed.get("definition", "")).strip(),
            parent_suggestion=(str(parent) if parent else None),
            basis_quotes=quotes,
            # PU4: 定義の根拠の語句が条文に実在するかをプログラムで確かめる
            basis_verified=[q.strip().strip("「」") in clause_text for q in quotes],
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
        runs: list[NormalizationRun] = []
        for i, temperature in enumerate(self.temperatures, 1):
            raw_surface, _rationale = self._extract_surface(
                clause_text, clause_ref, context, temperature
            )
            surface_check = check_surface(raw_surface, clause_text)
            chosen = self._choose_action(
                clause_text,
                clause_ref,
                context,
                surface_check.surface or raw_surface,
                ledger,
                temperature,
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
            runs.append(
                NormalizationRun(
                    index=i,
                    temperature=temperature,
                    surface=surface_check.surface or raw_surface,
                    surface_check=surface_check,
                    choice=choice,
                    reason=reason,
                    definition_part=str(chosen.get("definition_part", "")),
                    considered=[
                        {
                            "name": str(c.get("name", "")),
                            "why_not": str(c.get("why_not", "")),
                        }
                        for c in (chosen.get("considered") or [])
                        if isinstance(c, dict)
                    ],
                )
            )

        counts = Counter(r.choice for r in runs)
        top = max(counts.values())
        if sum(1 for n in counts.values() if n == top) > 1:
            majority = runs[0].choice  # 同数なら1回目(temperature=0.0)を採る
        else:
            majority = counts.most_common(1)[0][0]
        agreement = f"{counts[majority]}/{len(runs)}"
        unstable = counts[majority] != len(runs)
        chosen_run = next(r for r in runs if r.choice == majority)

        considered: list[dict] = []
        seen = set()
        for run in runs:
            for item in run.considered:
                key = (item["name"], item["why_not"])
                if key in seen:
                    continue
                seen.add(key)
                considered.append(item)

        proposal: Optional[NewActionProposal] = None
        duplicate: Optional[DuplicateCheck] = None
        if majority == NO_MATCH:
            proposal = self._propose_action(
                clause_text, clause_ref, context, chosen_run.surface, ledger
            )
            duplicate = ledger.duplicate_check(proposal.name, proposal.definition)
            normalized = proposal.name
            is_new = True
        else:
            normalized = majority
            is_new = False

        action = ActionRef(
            surface=chosen_run.surface,
            normalized=normalized,
            parent=None,  # P6: 前段は親を書かない
            is_new=is_new,
            approved_by=None,  # D53: 承認は人
        )
        gates = ReviewGates(
            new_action_unapproved=is_new and action.approved_by is None,
            action_surface_unverified=not chosen_run.surface_check.ok,
            action_mapping_unstable=unstable,
            action_possible_duplicate=bool(duplicate and duplicate.flagged),
        )
        trace = ActionNormalizationTrace(
            runs=runs,
            agreement=agreement,
            candidates_considered=considered,
            new_action_proposal=proposal,
            duplicate_check=duplicate,
            elapsed_seconds=time.monotonic() - started,
            llm_calls=self.calls,
        )
        return ActionNormalizationResult(action=action, trace=trace, gates=gates)


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
    """プロンプトに出している様相の語(報告用)。"""
    return MODALITY_WORDS
