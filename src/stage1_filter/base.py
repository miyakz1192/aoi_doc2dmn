"""①条文抽出・関連フィルタのインターフェース定義。

実装（ルールベース／LLMフォールバック等）はゼロベースでこの下に追加する。
既存メモ（llm_vs_hardcoded_in_tests.md 2.2節）にある通り、
「ルールベースで確定できない場合のみLLMにフォールバックする」設計が前提。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class FilterResult:
    relevant: bool
    reason: str
    # ルールベースで確定したか、LLMフォールバックを使ったかを記録する。
    # 評価時に「ルールベースだけでどこまでカバーできているか」を測るため。
    decided_by: str  # "rule" | "llm"


class ClauseFilter(ABC):
    """①条文抽出・関連フィルタの共通インターフェース。"""

    @abstractmethod
    def filter(self, clause_text: str, article_no: str | None = None) -> FilterResult:
        """条文が、決定表（DMN）に構造化する価値がある業務ルールかを判定する。

        行動主体が「あおい」自身か、会社・労働者等の他の主体かは問わない
        (testdata/schema.md「①関連判定の基準」節参照)。条件（期限・属性・状態等）
        に応じて許可/禁止/分岐が変わる業務ルールであれば関係ありとする。
        """
        raise NotImplementedError


# 実装:
#   - RuleBasedFilter (rule_based.py): RELEVANT_KEYWORDS等によるルールベース一次判定
#   - LlmFallbackFilter (llm_fallback.py): ルールベースで確定しない場合のLLM判定
#   - CompositeFilter (composite.py): 上記2つを組み合わせ、decided_byを記録する
