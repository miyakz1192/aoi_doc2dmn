"""①関連フィルタのLLMフォールバック判定。

RuleBasedFilterで確定できなかった条文について、Ollama経由で意味判定を行う。
"""
from __future__ import annotations

from src.common.ollama_client import DEFAULT_MODEL, DEFAULT_OLLAMA_BASE_URL, call_ollama_json

from .base import ClauseFilter, FilterResult


class LlmFallbackFilter(ClauseFilter):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        timeout: int = 300,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def filter(self, clause_text: str, article_no: str | None = None) -> FilterResult:
        prompt = self._build_prompt(clause_text, article_no)
        parsed = call_ollama_json(prompt, model=self.model, base_url=self.base_url, timeout=self.timeout)
        relevant = bool(parsed.get("relevant", False))
        reason = str(parsed.get("rationale", ""))
        return FilterResult(relevant=relevant, reason=reason, decided_by="llm")

    def _build_prompt(self, clause_text: str, article_no: str | None) -> str:
        return (
            "あなたは就業規則・情報管理規程等の業務規程の条文が、業務行動を規律する"
            "ルール(決定表・ガイドライン・エスカレーション基準のいずれかとして"
            "構造化する候補)かどうかを判定するアシスタントです。将来的にはAIエージェント"
            "(例:「あおい」)がこのルールをハーネスとして参照し、自身が実行する行動に"
            "制約をかける想定ですが、条文中の行動主体が「あおい」自身か、会社・労働者等の"
            "他の主体かは問いません。\n\n"
            "関係する = 以下のいずれかに該当する規定である。\n"
            "  (a) 禁止・許可・義務(期限管理や条件分岐を伴う手続き的な規定を含む。"
            "例:支払期限が休日に当たる場合の繰り上げ、期限内に対応がなければ処理が"
            "切り替わる、等)\n"
            "  (b) 抽象的な努力義務・規範で、状況判断を要するもの(ガイドライン向け)\n"
            "  (c) 承認・報告等、人間の確認を求めるエスカレーション要求\n"
            "関係しない = 手当額・休暇日数・賞与・退職金等、条件分岐や期限管理を"
            "伴わない固定的な金額・日数の算定ルールにとどまる規定。\n\n"
            f"条文(参照: {article_no or '不明'}):\n\"\"\"{clause_text}\"\"\"\n\n"
            "出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):\n"
            '{"relevant": true または false, "rationale": "判定根拠を日本語で1文"}\n'
        )
