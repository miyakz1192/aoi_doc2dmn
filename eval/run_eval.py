"""testdata/clauses/*.json のオラクルに対して、①フィルタ・②分類器の
一致率を計測するハーネス。

build_filter() は CompositeFilter(ルールベース＋Ollamaフォールバック)、
build_axis1_classifier() / build_axis2_classifier() は LocalLlmAxis1Classifier /
LocalLlmAxis2Classifier(Ollama, qwen2.5:14b-instruct)を使う。実行にはOllamaが
localhost:11434で起動している必要がある。

実行方法:
    python -m eval.run_eval                    # text_raw（空欄あり）で評価
    python -m eval.run_eval --variant filled    # text_filled（空欄埋め済み）で評価
    python -m eval.run_eval --variant both      # 両方を評価し差分を出力

variantの選択について:
    testdata/schema.md「空欄の扱い方針」節を参照。空欄あり／埋め済みの両方が
    本番トラフィックに実在するという想定に基づき、両方を独立したテストケース
    として扱う。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.metrics import (  # noqa: E402
    compute_axis_metrics,
    compute_stage1_metrics,
    confidence_calibration,
    quote_verification_calibration,
)
from src.common.clause_io import ClauseOracle, load_clauses  # noqa: E402
from src.stage1_filter.base import ClauseFilter  # noqa: E402
from src.stage1_filter.composite import CompositeFilter  # noqa: E402
from src.stage2_classify.base import AxisClassifier  # noqa: E402
from src.stage2_classify.local_llm import (  # noqa: E402
    LocalLlmAxis1Classifier,
    LocalLlmAxis2Classifier,
)

DEFAULT_TESTDATA = Path(__file__).resolve().parents[1] / "testdata" / "clauses" / "clauses_v1.json"
CONFIDENCE_THRESHOLD = 0.7  # llm_vs_hardcoded_in_tests.md記載の既存しきい値と合わせる


def build_filter() -> ClauseFilter:
    return CompositeFilter()


def build_axis1_classifier() -> AxisClassifier:
    return LocalLlmAxis1Classifier()


def build_axis2_classifier() -> AxisClassifier:
    return LocalLlmAxis2Classifier()


def run_variant(clauses: list[ClauseOracle], variant: str) -> dict:
    """指定variant('raw'|'filled')で①②を評価し、予測結果と指標をまとめて返す。"""
    clause_filter = build_filter()
    axis1_classifier = build_axis1_classifier()
    axis2_classifier = build_axis2_classifier()

    # --- Stage 1: 関連フィルタ ---
    stage1_preds, stage1_golds = [], []
    for c in clauses:
        result = clause_filter.filter(c.text(variant), c.article_no)
        stage1_preds.append(result.relevant)
        stage1_golds.append(c.stage1_relevant)
    stage1_metrics = compute_stage1_metrics(stage1_preds, stage1_golds)

    # --- Stage 2: 軸1・軸2分類（①でrelevant=Trueと判定された条文のみ対象） ---
    relevant_clauses = [c for c in clauses if c.stage1_relevant]
    axis1_preds, axis1_confs, axis1_verified, axis1_vocab_flagged = [], [], [], []
    axis2_preds, axis2_confs, axis2_verified = [], [], []
    for c in relevant_clauses:
        r1 = axis1_classifier.classify(c.text(variant), c.clause_id)
        r2 = axis2_classifier.classify(c.text(variant), c.clause_id)
        axis1_preds.append(r1.label)
        axis1_confs.append(r1.confidence)
        axis1_verified.append(r1.quote_verified)
        axis1_vocab_flagged.append(r1.vocab_flagged)
        axis2_preds.append(r2.label)
        axis2_confs.append(r2.confidence)
        axis2_verified.append(r2.quote_verified)

    axis1_golds = [c.axis1 for c in relevant_clauses]
    axis2_golds = [c.axis2 for c in relevant_clauses]
    is_ambiguous = [c.is_ambiguous for c in relevant_clauses]
    alt_axis1 = [c.alt_labels.get("axis1") if c.alt_labels else None for c in relevant_clauses]
    alt_axis2 = [c.alt_labels.get("axis2") if c.alt_labels else None for c in relevant_clauses]

    axis1_metrics = compute_axis_metrics(axis1_preds, axis1_golds, is_ambiguous, alt_axis1)
    axis2_metrics = compute_axis_metrics(axis2_preds, axis2_golds, is_ambiguous, alt_axis2)

    axis1_calibration = confidence_calibration(
        axis1_preds, axis1_golds, axis1_confs, CONFIDENCE_THRESHOLD
    )
    axis2_calibration = confidence_calibration(
        axis2_preds, axis2_golds, axis2_confs, CONFIDENCE_THRESHOLD
    )
    axis1_quote_calibration = quote_verification_calibration(axis1_preds, axis1_golds, axis1_verified)
    axis2_quote_calibration = quote_verification_calibration(axis2_preds, axis2_golds, axis2_verified)

    # vocab_flagged集計: フラグが立った件数のうち、実際に不正解だった件数
    # (=フラグが拾えている不正解)と、フラグが立ったが正解だった件数
    # (=過剰検知。④人間レビューの負荷として計上されるが誤りではない)。
    vocab_flag_total = sum(axis1_vocab_flagged)
    vocab_flag_catches = sum(
        1 for flagged, pred, gold in zip(axis1_vocab_flagged, axis1_preds, axis1_golds)
        if flagged and pred != gold
    )
    axis1_missed_ids = [
        cid for cid, pred, gold in zip(
            [c.clause_id for c in relevant_clauses], axis1_preds, axis1_golds
        )
        if pred != gold
    ]
    axis1_missed_flagged = [
        cid for cid, flagged, pred, gold in zip(
            [c.clause_id for c in relevant_clauses], axis1_vocab_flagged, axis1_preds, axis1_golds
        )
        if pred != gold and not flagged
    ]
    vocab_flag_stats = {
        "flagged_count": vocab_flag_total,
        "flagged_total_relevant": len(relevant_clauses),
        "flagged_and_incorrect": vocab_flag_catches,
        "incorrect_total": len(axis1_missed_ids),
        "incorrect_but_not_flagged": axis1_missed_flagged,
    }

    return {
        "variant": variant,
        "stage1_metrics": stage1_metrics,
        "relevant_clause_ids": [c.clause_id for c in relevant_clauses],
        "axis1_preds": axis1_preds,
        "axis2_preds": axis2_preds,
        "axis1_metrics": axis1_metrics,
        "axis2_metrics": axis2_metrics,
        "axis1_calibration": axis1_calibration,
        "axis2_calibration": axis2_calibration,
        "axis1_quote_calibration": axis1_quote_calibration,
        "axis2_quote_calibration": axis2_quote_calibration,
        "axis1_vocab_flag_stats": vocab_flag_stats,
    }


def print_report(result: dict) -> None:
    stage1_metrics = result["stage1_metrics"]
    axis1_metrics = result["axis1_metrics"]
    axis2_metrics = result["axis2_metrics"]
    print(f"[variant={result['variant']}]")
    print("[Stage 1: 関連フィルタ]")
    print(f"  accuracy={stage1_metrics.accuracy:.2f} "
          f"precision={stage1_metrics.precision:.2f} recall={stage1_metrics.recall:.2f}")
    print(f"  tp={stage1_metrics.tp} fp={stage1_metrics.fp} "
          f"fn={stage1_metrics.fn} tn={stage1_metrics.tn}")
    print()
    print("[Stage 2: 軸1 (ハード制約/判断ガイド/エスカレーション基準)]")
    print(f"  strict_accuracy={axis1_metrics.strict_accuracy:.2f} "
          f"lenient_accuracy={axis1_metrics.lenient_accuracy:.2f} "
          f"(n={axis1_metrics.total})")
    print(f"  confusion (gold, pred) -> count: {dict(axis1_metrics.confusion)}")
    print(f"  confidence calibration: {result['axis1_calibration']}")
    print(f"  quote calibration: {result['axis1_quote_calibration']}")
    vfs = result["axis1_vocab_flag_stats"]
    print(f"  vocab flag: {vfs['flagged_count']}/{vfs['flagged_total_relevant']}件に既知の語彙的盲点フラグ、"
          f"うち不正解{vfs['flagged_and_incorrect']}件を捕捉"
          f"(不正解は全{vfs['incorrect_total']}件、フラグ未検知の不正解: {vfs['incorrect_but_not_flagged']})")
    print()
    print("[Stage 2: 軸2 (宣言的/手続き的)]")
    print(f"  strict_accuracy={axis2_metrics.strict_accuracy:.2f} "
          f"lenient_accuracy={axis2_metrics.lenient_accuracy:.2f} "
          f"(n={axis2_metrics.total})")
    print(f"  confusion (gold, pred) -> count: {dict(axis2_metrics.confusion)}")
    print(f"  confidence calibration: {result['axis2_calibration']}")
    print(f"  quote calibration: {result['axis2_quote_calibration']}")


def print_diff(result_raw: dict, result_filled: dict) -> None:
    """raw/filled間で予測が変化した条文を一覧表示する。schema.md
    「空欄の扱い方針」節の判定基準（差分件数に応じた解釈）に対応。"""
    print()
    print("[raw vs filled 差分]")
    ids = result_raw["relevant_clause_ids"]
    assert ids == result_filled["relevant_clause_ids"], "対象条文の集合が一致していません"

    changed = 0
    for i, cid in enumerate(ids):
        r_a1, f_a1 = result_raw["axis1_preds"][i], result_filled["axis1_preds"][i]
        r_a2, f_a2 = result_raw["axis2_preds"][i], result_filled["axis2_preds"][i]
        if r_a1 != f_a1 or r_a2 != f_a2:
            changed += 1
            print(f"  - {cid}: axis1 {r_a1!r}->{f_a1!r}  axis2 {r_a2!r}->{f_a2!r}")

    print(f"  変化した条文数: {changed} / {len(ids)}")
    if changed == 0:
        print("  判定基準: 差分なし → 空欄は①②に影響しないと結論。text_rawを正本として運用。")
    elif changed <= 2:
        print("  判定基準: 1〜2件変化 → n=15では有意性を判定できない。"
              "テストデータを30〜50件に増やして再測定が必要。")
    else:
        print("  判定基準: 3件以上変化 → 空欄が実質的な影響を持つと判断。"
              "プロンプト側で「空欄は事業場ごとの設定値であり判定に影響しない」旨を"
              "明示する対策を検討。")


def run(testdata_path: Path = DEFAULT_TESTDATA, variant: str = "raw") -> None:
    clauses: list[ClauseOracle] = load_clauses(testdata_path)
    print(f"loaded {len(clauses)} clauses from {testdata_path.name}")
    print()

    if variant == "both":
        result_raw = run_variant(clauses, "raw")
        result_filled = run_variant(clauses, "filled")
        print_report(result_raw)
        print()
        print_report(result_filled)
        print_diff(result_raw, result_filled)
    else:
        print_report(run_variant(clauses, variant))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant", choices=["raw", "filled", "both"], default="raw",
        help="raw=空欄あり原文, filled=空欄埋め済み, both=両方評価して差分表示（デフォルト: raw）",
    )
    args = parser.parse_args()
    run(variant=args.variant)
