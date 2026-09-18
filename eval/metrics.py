"""オラクルとの一致率を計算するための指標関数群。

schema.md「評価指標」節に対応。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Stage1Metrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def accuracy(self) -> float:
        total = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.tn) / total if total else 0.0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0


def compute_stage1_metrics(predictions: list[bool], oracles: list[bool]) -> Stage1Metrics:
    m = Stage1Metrics()
    for pred, gold in zip(predictions, oracles):
        if pred and gold:
            m.tp += 1
        elif pred and not gold:
            m.fp += 1
        elif not pred and gold:
            m.fn += 1
        else:
            m.tn += 1
    return m


@dataclass
class AxisMetrics:
    correct: int = 0
    correct_incl_alt: int = 0  # is_ambiguous=True の項目で alt_labels も許容した場合
    total: int = 0
    confusion: Counter = field(default_factory=Counter)  # (gold, pred) -> count

    @property
    def strict_accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def lenient_accuracy(self) -> float:
        return self.correct_incl_alt / self.total if self.total else 0.0


def compute_axis_metrics(
    predictions: list[str],
    golds: list[str],
    is_ambiguous_flags: list[bool],
    alt_labels: list[str | None],
) -> AxisMetrics:
    m = AxisMetrics()
    for pred, gold, ambiguous, alt in zip(predictions, golds, is_ambiguous_flags, alt_labels):
        m.total += 1
        m.confusion[(gold, pred)] += 1
        if pred == gold:
            m.correct += 1
            m.correct_incl_alt += 1
        elif ambiguous and alt is not None and pred == alt:
            m.correct_incl_alt += 1
    return m


def confidence_calibration(
    predictions: list[str],
    golds: list[str],
    confidences: list[float],
    threshold: float,
) -> dict:
    """confidence >= threshold の場合と未満の場合とで、それぞれの正解率を返す。"""
    above_correct = above_total = below_correct = below_total = 0
    for pred, gold, conf in zip(predictions, golds, confidences):
        correct = pred == gold
        if conf >= threshold:
            above_total += 1
            above_correct += int(correct)
        else:
            below_total += 1
            below_correct += int(correct)
    return {
        "threshold": threshold,
        "above_threshold_accuracy": above_correct / above_total if above_total else None,
        "above_threshold_count": above_total,
        "below_threshold_accuracy": below_correct / below_total if below_total else None,
        "below_threshold_count": below_total,
    }


def quote_verification_calibration(
    predictions: list[str],
    golds: list[str],
    quote_verified_flags: list[bool],
) -> dict:
    """evidence_quoteがclause_text中に実在した場合／しなかった場合の正解率を返す。

    confidence_calibration()と同じ形の集計だが、confidenceの代わりに
    quote_verified(プログラム側で検証した逐語引用の実在チェック)を軸にする。
    confidenceは常に0.7超を返す傾向があり単独では機能しないことが実測で
    判明したため、これを補う第二の較正指標として用いる。
    """
    verified_correct = verified_total = unverified_correct = unverified_total = 0
    for pred, gold, verified in zip(predictions, golds, quote_verified_flags):
        correct = pred == gold
        if verified:
            verified_total += 1
            verified_correct += int(correct)
        else:
            unverified_total += 1
            unverified_correct += int(correct)
    return {
        "verified_accuracy": verified_correct / verified_total if verified_total else None,
        "verified_count": verified_total,
        "unverified_accuracy": unverified_correct / unverified_total if unverified_total else None,
        "unverified_count": unverified_total,
    }
