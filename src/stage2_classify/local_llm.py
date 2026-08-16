"""②2軸分類器のローカルLLM(Ollama)実装。

reference/gijinka-extraction-pipeline/src/pipeline/classifiers/local_llm_strategy.py
のプロンプト骨子を、就業規則・情報管理規程を対象とする本リポジトリのスコープ
(医療事務限定ではない)向けに一般化して移植したもの。

軸1・軸2は独立した2問としてLLMに問う(gijinka_extraction_pipeline_design_v1.docx
2.3節の決定事項、base.pyのdocstring参照)。
"""
from __future__ import annotations

from src.common.ollama_client import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    OllamaCallError,
    call_ollama_json,
)

from .base import AxisClassificationResult, Axis1Classifier, Axis2Classifier


class _OllamaAxisClassifierMixin:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        timeout: int = 300,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def classify(
        self,
        clause_text: str,
        clause_ref: str | None = None,
        context: str | None = None,
    ) -> AxisClassificationResult:
        prompt = self._build_prompt(clause_text, clause_ref, context)
        parsed = call_ollama_json(prompt, model=self.model, base_url=self.base_url, timeout=self.timeout)

        label = str(parsed.get("label", ""))
        if label not in self.VALID_LABELS:
            raise OllamaCallError(
                f"{type(self).__name__}: invalid label {label!r}, expected one of {self.VALID_LABELS} "
                f"(raw response: {parsed!r})"
            )
        try:
            confidence = float(parsed["confidence"])
        except (KeyError, TypeError, ValueError) as e:
            raise OllamaCallError(f"{type(self).__name__}: missing/invalid confidence in {parsed!r}") from e
        rationale = str(parsed.get("rationale", ""))
        return AxisClassificationResult(label=label, confidence=confidence, rationale=rationale)

    def _build_prompt(self, clause_text: str, clause_ref: str | None, context: str | None) -> str:
        raise NotImplementedError


class LocalLlmAxis1Classifier(_OllamaAxisClassifierMixin, Axis1Classifier):
    def _build_prompt(self, clause_text: str, clause_ref: str | None, context: str | None) -> str:
        return (
            "あなたは就業規則・情報管理規程等の業務規程の条文を分類するアシスタントです。\n"
            "この条文が、AIエージェント(業務代行システム。例:「あおい」)の業務行動を"
            "どう制御するかという観点で、以下の「軸1」のいずれか1つに分類してください。\n\n"
            "判定は次の順序で行ってください(「客観的に判定可能か」だけでは"
            "ハード制約とエスカレーション基準を区別できないため、まず1を確認すること)。\n"
            "1. 条文が『行動前または異常検知時に、人間の承認・報告・確認を必須の手続きとして"
            "要求しているか』を確認する。該当すれば、トリガー条件自体が客観的に判定可能"
            "(例:「2か月以内に対応がなければ」という期限超過の検知)であっても、義務として"
            "明確に書かれていても、必ず『エスカレーション基準』とする。\n"
            "2. 該当しない場合、条文が抽象的な規範・努力義務や、外延が条文からは確定できない"
            "キャッチオール条項(例:「あらゆるハラスメント」)で、該当性の判断に個別事例の"
            "参照を要するかを確認する。該当すれば『判断ガイド』とする。ただし、トリガー条件が"
            "法令上の客観的な基準・閾値や本人の申出など明確な事実に基づく場合は、"
            "「認められる」のように表現が柔らかくても判断ガイドにはしない。\n"
            "3. 上記いずれにも該当しない、具体的な禁止・許可・義務行為で、限られた事実"
            "(属性・条件の有無等)から機械的に白黒判定でき、人間の承認を経由せず行動が"
            "進む場合は『ハード制約』とする。\n\n"
            "- ハード制約: 具体的な禁止・許可・義務行為が明記され、機械的に白黒判定できる。"
            "行動は人間の承認を経由せず進む。\n"
            "- 判断ガイド: 抽象的な規範・努力義務・キャッチオール条項で、該当性の判断に"
            "個別事例の参照や状況判断を要し、機械的な二値判定ができない。\n"
            "- エスカレーション基準: 不確実性の検知・期限切れ等をトリガーに、行動前または"
            "異常発生時に人間の承認・報告・確認を必須の手続きとして要求する規定。\n\n"
            f"条文(参照: {clause_ref or '不明'}):\n\"\"\"{clause_text}\"\"\"\n\n"
            "出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):\n"
            '{"label": "ハード制約 または 判断ガイド または エスカレーション基準のいずれか1つ", '
            '"confidence": "0から1の数値。判定にどれだけ自信があるかを自己申告", '
            '"rationale": "判定根拠を日本語で1〜2文で簡潔に"}\n'
        )


class LocalLlmAxis2Classifier(_OllamaAxisClassifierMixin, Axis2Classifier):
    def _build_prompt(self, clause_text: str, clause_ref: str | None, context: str | None) -> str:
        return (
            "あなたは就業規則・情報管理規程等の業務規程の条文を分類するアシスタントです。\n"
            "この条文が、AIエージェントの業務行動をどう制御するかという観点で、"
            "以下の「軸2」のいずれか1つに分類してください。\n\n"
            "判定基準は「時間に関する語が条文中にあるか」ではなく、「期限までの待機・"
            "継続的な状態監視・複数の時点にまたがる周期処理という、時間の経過そのものを"
            "伴うか」です。「事前に」「速やかに」等の順序を示す語は、単に行動の"
            "前後関係(〜する前に、すぐに)を示すだけであり、時間の経過を伴わないなら"
            "宣言的と判定してください(例:「事前に承認を得る」は、承認が下りればその"
            "場で完結するため宣言的)。\n\n"
            "- 宣言的: ある一時点の事実確認・即時の可否判定で完結する。期限までの待機・"
            "継続監視・周期処理を伴わない。\n"
            "- 手続き的: 期限までの待機(例:「30日前に予告」)、継続的な状態監視"
            "(例:労働時間の状況を継続的に把握する)、複数の時点にまたがる周期処理"
            "(例:毎月の締め日→翌月の支払日)など、時間の経過そのものを伴う"
            "(例:「2か月以内に対応がなされなかった場合」)。\n\n"
            f"条文(参照: {clause_ref or '不明'}):\n\"\"\"{clause_text}\"\"\"\n\n"
            "出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):\n"
            '{"label": "宣言的 または 手続き的 のいずれか1つ", '
            '"confidence": "0から1の数値。判定にどれだけ自信があるかを自己申告", '
            '"rationale": "判定根拠を日本語で1〜2文で簡潔に"}\n'
        )
