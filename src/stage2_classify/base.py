"""②2軸分類器のインターフェース定義。

設計方針（gijinka_extraction_pipeline_design_v1.docx 2.3節で決定済み）:
  - 軸1・軸2は独立した2問としてLLMに問う（一括6択にしない）
  - ストラテジーパターンで実装を差し替え可能にする
  - 実装はゼロベースで書き直す（既存コードは reference/ を参照）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AxisClassificationResult:
    label: str
    confidence: float  # 0.0-1.0
    rationale: str
    # 判定根拠として原文から逐語引用させたもの、およびそれが実際に
    # clause_text中に存在するかのプログラム側検証結果。confidenceとは独立した
    # もう一つの人間レビュー行きゲートとして使う(confidenceは常に0.7超を返す
    # 傾向があり単独では機能しないことが実測で判明したため)。
    evidence_quote: str = ""
    quote_verified: bool = False
    # 条文中に既知の語彙的盲点(「認められる」「申出」「許可を得る」「承認を受ける」等)を
    # 含むかのルールベース判定。quote_verifiedは「存在しない記述の幻視」(RTO型)は
    # 検出できるが、「実在する文言を正しく引用した上での解釈ミス」(第60条・5.6型、
    # 4回の反復調整でも解消しなかった既知の弱点)は検出できない。vocab_flags.pyの
    # check_vocab_flag()参照。confidence/quote_verifiedの結果に関わらずTrueなら
    # ④人間レビューへ強制的に回す運用上の補完フラグ。
    vocab_flagged: bool = False


class AxisClassifier(ABC):
    """AxisClassifier共通インターフェース。

    input: clause_text, clause_ref, context(optional)
    output: label, confidence, rationale
    """

    @abstractmethod
    def classify(
        self,
        clause_text: str,
        clause_ref: str | None = None,
        context: str | None = None,
    ) -> AxisClassificationResult:
        raise NotImplementedError


class Axis1Classifier(AxisClassifier):
    """軸1: ハード制約 / 判断ガイド / エスカレーション基準"""

    VALID_LABELS = ("ハード制約", "判断ガイド", "エスカレーション基準")


class Axis2Classifier(AxisClassifier):
    """軸2: 宣言的 / 手続き的"""

    VALID_LABELS = ("宣言的", "手続き的")


# 実装:
#   - LocalLlmAxis1Classifier / LocalLlmAxis2Classifier (local_llm.py, Ollama呼び出し)
#   - CONFIDENCE_THRESHOLD は eval/run_eval.py 側で定義(呼び出し側)
