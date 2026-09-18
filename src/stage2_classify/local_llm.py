"""②2軸分類器のローカルLLM(Ollama)実装。

reference/gijinka-extraction-pipeline/src/pipeline/classifiers/local_llm_strategy.py
のプロンプト骨子を、就業規則・情報管理規程を対象とする本リポジトリのスコープ
(医療事務限定ではない)向けに一般化して移植したもの。

軸1・軸2は独立した2問としてLLMに問う(gijinka_extraction_pipeline_design_v1.docx
2.3節の決定事項、base.pyのdocstring参照)。

## 軸1の2段階化を試して差し戻した経緯(2026年8月17〜18日)

n=27での詳細診断（第18条・5.6等の失敗パターン）を受け、軸1を「まずエスカレーション
基準に該当するかを単独で判定するgate→残り2択」という2段階構造に変更して実測した。
狙い撃ちした4件中3件(5.6・RTO・8.4)は個別には修正できたが、n=22全体で再測定すると
strict_accuracyが0.77→0.73へ悪化した。これまで全ラウンドで一貫して正解していた
第18条・第58条・創作条文2件が新たに「具体的で機械的に判定可能」という同一パターンの
理由で誤判定されるようになり、狙った箇所を直すたびに別の箇所が壊れる現象
(「もぐら叩き」)が、単一プロンプトへの微修正だけでなくアーキテクチャ変更でも
再現することが実測で確認された。net負という結果を受け、判定ロジックは本コミットの
単一プロンプト版(n=22でaxis1 strict=0.77/lenient=0.82, axis2 strict=lenient=0.95)
に差し戻した。軸2の「トリガー条件と応答の区別」も同時期に追加したが、これも
第18条を新たに壊す副作用が確認されたため同様に差し戻している。

## 逐語引用検証(2026年8月17日追加、差し戻し対象外)

confidenceは実測で常に0.7を超えており(below_threshold_countが全評価ランで0)、
「confidence<0.7で人間レビューに回す」という安全網が機能していないことが判明した。
そこで各判定にrationaleの根拠となる原文からの逐語引用(evidence_quote)を必須化し、
それが実際にclause_text中に存在するかをプログラム側で検証する(quote_verified)。
これは分類ロジック自体を変更しない純粋な追加計測であり、上記の2段階化差し戻しの
影響を受けず、単一プロンプト版にもそのまま組み込んでいる。実際にfilled版の評価で
「quote_verified=Falseかつ不正解」という事例を1件観測しており、confidenceでは
検出できないハルシネーションを検出できることを確認済み。

## context引数がプロンプトに反映されていなかったバグ修正(2026年8月19日)

base.pyのAxisClassifier.classify()は`context: str | None`引数を受け付ける設計だったが、
_build_prompt()の実装はこれを一切プロンプトに反映していなかった(渡しても無視される
だけの死んだ引数になっていた)。第60条・5.6の未解決ミスについて、同一出典文書内の
関連記述(第60条: 厚労省モデル就業規則解説の量的閾値、5.6: IPA文書内の承認権限定義)を
contextとして渡す実験(HANDOFF_20260818_round6_to_web.md後続のタスクA)を行う前提として
修正した。_context_block()参照。
"""
from __future__ import annotations

from src.common.ollama_client import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    OllamaCallError,
    call_ollama_json,
)

from .base import AxisClassificationResult, Axis1Classifier, Axis2Classifier
from .vocab_flags import check_vocab_flag

# 逐語引用として許容する前後の記号(全角/半角の鉤括弧・引用符)。これらを剥がしてから
# clause_text中の部分文字列として存在するかを照合する。
_QUOTE_STRIP_CHARS = "「」『』\"'“”"


def _verify_quote(evidence_quote: str, clause_text: str) -> bool:
    """evidence_quoteがclause_textの部分文字列として実在するかを検証する。"""
    q = evidence_quote.strip().strip(_QUOTE_STRIP_CHARS).strip()
    if not q:
        return False
    return q in clause_text


_EVIDENCE_QUOTE_SPEC = (
    '"evidence_quote": "判定根拠とした条文中の一節を、原文のまま一言一句'
    '正確に抜き出したもの(要約・言い換え禁止。存在しない内容を書かないこと)"'
)


def _context_block(context: str | None) -> str:
    """contextが与えられた場合、条文本文と判定指示の間に挿入する参考情報ブロックを返す。

    base.pyのAxisClassifier.classify()はcontext引数を受け付ける設計だが、
    従来の_build_prompt()実装はこれをプロンプトに一切反映していなかった
    (2026年8月19日修正)。あくまで判定の「参考」であり、条文本文そのものの
    根拠として求めるevidence_quoteはclause_text側から引用させる(contextからの
    引用は許容しない)。
    """
    if not context:
        return ""
    return (
        f"\n参考情報(判定の参考にしてよいが、この一節自体はevidence_quoteの"
        f"引用元にはしないこと。あくまで条文本文の解釈を助けるための補足):\n"
        f"\"\"\"{context}\"\"\"\n"
    )


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
        evidence_quote = str(parsed.get("evidence_quote", ""))
        quote_verified = _verify_quote(evidence_quote, clause_text)
        vocab_flagged, _matched = check_vocab_flag(clause_text)
        return AxisClassificationResult(
            label=label,
            confidence=confidence,
            rationale=rationale,
            evidence_quote=evidence_quote,
            quote_verified=quote_verified,
            vocab_flagged=vocab_flagged,
        )

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
            f"条文(参照: {clause_ref or '不明'}):\n\"\"\"{clause_text}\"\"\"\n"
            f"{_context_block(context)}\n"
            "出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):\n"
            '{"label": "ハード制約 または 判断ガイド または エスカレーション基準のいずれか1つ", '
            '"confidence": "0から1の数値。判定にどれだけ自信があるかを自己申告", '
            '"rationale": "判定根拠を日本語で1〜2文で簡潔に", '
            f"{_EVIDENCE_QUOTE_SPEC}}}\n"
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
            "- 宣言的: ある一時点の状態評価で結論が出る(期限・待機・時間経過の記述がない)。\n"
            "- 手続き的: 期限までの待機(例:「30日前に予告」)、継続的な状態監視"
            "(例:労働時間の状況を継続的に把握する)、複数の時点にまたがる周期処理"
            "(例:毎月の締め日→翌月の支払日)など、時間の経過そのものを伴う"
            "(例:「2か月以内に対応がなされなかった場合」)。\n\n"
            f"条文(参照: {clause_ref or '不明'}):\n\"\"\"{clause_text}\"\"\"\n"
            f"{_context_block(context)}\n"
            "出力は必ず次のJSON形式のみで返してください(前後に他のテキストを含めないこと):\n"
            '{"label": "宣言的 または 手続き的 のいずれか1つ", '
            '"confidence": "0から1の数値。判定にどれだけ自信があるかを自己申告", '
            '"rationale": "判定根拠を日本語で1〜2文で簡潔に", '
            f"{_EVIDENCE_QUOTE_SPEC}}}\n"
        )
