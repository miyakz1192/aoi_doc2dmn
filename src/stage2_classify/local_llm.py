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
            "- ハード制約: 具体的な禁止・許可・義務行為が明記され、限られた事実(属性・条件の"
            "有無等)から機械的に白黒判定できる。強行規定的で裁量の余地が小さい。\n"
            "- 判断ガイド: 抽象的な規範・努力義務・キャッチオール条項で、該当性の判断に"
            "事例参照や状況判断を要し、機械的な二値判定ができない。\n"
            "- エスカレーション基準: 不確実性の検知・確信度・期限切れ等をトリガーに、"
            "行動前または異常発生時に人間の承認・報告・確認を求める規定。\n\n"
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
            "- 宣言的: 一時点の状態評価で結論が出る(期限・待機・時間経過の記述がない)。\n"
            "- 手続き的: トリガー→期限→分岐のような時間経過の監視を伴う"
            "(例:「30日前に予告」「2か月以内に対応がなされなかった場合」)。\n\n"
            f"条文(参照: {clause_ref or '不明'}):\n\"\"\"{clause_text}\"\"\"\n\n"
            "出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):\n"
            '{"label": "宣言的 または 手続き的 のいずれか1つ", '
            '"confidence": "0から1の数値。判定にどれだけ自信があるかを自己申告", '
            '"rationale": "判定根拠を日本語で1〜2文で簡潔に"}\n'
        )
