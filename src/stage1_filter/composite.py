"""①関連フィルタの合成実装。

RuleBasedFilterで確定できる条文はルールベースで即断し、確定できない条文のみ
LlmFallbackFilterに委ねる。decided_by("rule" | "llm")で、実際にどちらが
判定したかを評価側から観測できるようにする。
"""
from __future__ import annotations

from .base import ClauseFilter, FilterResult
from .llm_fallback import LlmFallbackFilter
from .rule_based import RuleBasedFilter


class CompositeFilter(ClauseFilter):
    def __init__(
        self,
        rule_filter: RuleBasedFilter | None = None,
        llm_filter: LlmFallbackFilter | None = None,
    ):
        self.rule_filter = rule_filter or RuleBasedFilter()
        self.llm_filter = llm_filter or LlmFallbackFilter()

    def filter(self, clause_text: str, article_no: str | None = None) -> FilterResult:
        rule_result = self.rule_filter.try_filter(clause_text)
        if rule_result is not None:
            return rule_result
        return self.llm_filter.filter(clause_text, article_no)
