"""①関連フィルタのルールベース一次判定。

testdata/clauses/clauses_v1.json のノイズ条文(手当額・休暇日数・賞与・退職金等の
条件分岐を伴わない金額/日数算定規定)と、業務行動を規律する条文(禁止・承認・
報告・期限等の行為規定。行動主体が「あおい」自身か会社かは問わない。
testdata/schema.md「①関連判定の基準」節参照)を分ける観点でキーワードを選定
している。ここで判定できない場合はNoneを返し、LlmFallbackFilterに委ねる
(常にPASSを返すだけの実装にはしない)。
"""
import re

from .base import ClauseFilter, FilterResult

# 「しないこと」は「支給しないことがある」(賞与・退職金の任意不支給規定)にも
# 部分一致してしまうため、否定先読みで除外する。
RELEVANT_PATTERNS = (
    r"してはならない", r"しないこと(?!がある)", r"従事させない", r"禁止", r"義務を負う",
    r"承認を受け", r"承認を得なければ", r"届出", r"報告し", r"予告をする",
    r"面接指導", r"指示に従う",
)

IRRELEVANT_KEYWORDS = (
    "有給休暇", "家族手当", "扶養手当", "賞与", "退職金",
)


def rule_based_prefilter(clause_text: str) -> tuple[bool | None, str]:
    """明確なケースだけをルールベースで即断する。判定できない場合は(None, reason)を返す。"""
    has_relevant = any(re.search(p, clause_text) for p in RELEVANT_PATTERNS)
    has_irrelevant = any(k in clause_text for k in IRRELEVANT_KEYWORDS)

    if has_irrelevant and not has_relevant:
        return False, "手当・休暇日数等の算定に関するキーワードのみを含み、業務行動制御に関わるキーワードを含まない"
    if has_relevant:
        return True, "AIエージェントの業務行動制御に関わるキーワード(禁止・承認・報告・期限等)を含む"
    return None, "ルールベースでは判定不能"


class RuleBasedFilter(ClauseFilter):
    """ルールベース一次判定のみを行うフィルタ。

    try_filter()はルールベースで確定できた場合のみFilterResultを返しNoneにはならない
    ようCompositeFilter側でLLMフォールバックと組み合わせる想定。単体で使う場合は
    filter()を呼ぶと、判定不能時は保守的にrelevant=Falseを返す。
    """

    def try_filter(self, clause_text: str) -> FilterResult | None:
        relevant, reason = rule_based_prefilter(clause_text)
        if relevant is None:
            return None
        return FilterResult(relevant=relevant, reason=reason, decided_by="rule")

    def filter(self, clause_text: str, article_no: str | None = None) -> FilterResult:
        result = self.try_filter(clause_text)
        if result is not None:
            return result
        return FilterResult(
            relevant=False,
            reason="ルールベースでは判定不能(LLMフォールバック未使用のため非通過扱い)",
            decided_by="rule",
        )
