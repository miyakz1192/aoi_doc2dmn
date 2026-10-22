"""ゴールデンパスの一気通貫実行: 条文 → ②分類 → IR → 4点セット → DMN評価。

実行方法:
    python3 -m src.stage3_generate.run_golden_path                 # ②分類器を実際に呼ぶ
    python3 -m src.stage3_generate.run_golden_path --use-oracle    # ②をスキップしオラクル値を使う

`--use-oracle`はOllamaを起動せずに配線部分だけを検証したい場合に使う。
②の分類結果はIRの`axis1_result`/`axis2_result`および`review_gates`に反映されるため、
本番相当の確認をするときはオプション無しで実行する。

v2 では配線ジェネレータが既存の決定表と fact 台帳(登録簿)を参照するため、
`registry/registry.json` を読み込んで渡す(`--registry` で差し替え可能)。更新後の登録簿は
`out/` に書き出すだけで、永続台帳への反映(promotion)は人間レビューを経た別操作とする。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common.clause_io import load_clauses  # noqa: E402
from src.stage2_classify.local_llm import (  # noqa: E402
    LocalLlmAxis1Classifier,
    LocalLlmAxis2Classifier,
)
from src.stage2_classify.vocab_flags import check_vocab_flag  # noqa: E402
from src.stage3_generate.dmn_engine import DmnModel  # noqa: E402
from src.stage3_generate.golden_path import (  # noqa: E402
    CLAUSE_ID,
    DECISION_ID,
    build_golden_path_ir,
)
from src.stage3_generate.ir_schema import AxisResult  # noqa: E402
from src.stage3_generate.registry import Registry  # noqa: E402
from src.stage3_generate.wiring_generator import generate_wiring_bundle  # noqa: E402

DEFAULT_TESTDATA = Path(__file__).resolve().parents[2] / "testdata" / "clauses" / "clauses_v1.json"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "out" / "golden_path"
DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "registry" / "registry.json"

# 生成したDMNが想定通りに動くかを確認するシナリオ。
#
# シナリオ2〜4の入力値(検証環境・一般・運用担当)は、D4により possible_values から
# 除かれた「条文に書かれていない値」である。DMN評価はバインディング設定を通らず値を
# 直接エンジンへ渡すため4/4は成立するが、これは設計書7章 U2(既定のワイルドカード行が
# 「許可」なので、未登録の値もすべて許可に落ちる)が指摘する論点そのものである。
# possible_values を広げて再合法化するのはD4に反するため、シナリオ名に明記して残す。
SCENARIOS = [
    {
        "name": "開発担当者が本番環境で特権操作 → 禁止",
        "facts": {
            "operator_role": "システム開発担当",
            "target_environment": "本番環境",
            "requested_privilege": "特権",
        },
        "expected": {"decision": "禁止", "reason_code": "dev_privileged_op_on_production"},
    },
    {
        "name": "開発担当者だが検証環境 → 許可(possible_values外の値が既定行に落ちる・U2)",
        "facts": {
            "operator_role": "システム開発担当",
            "target_environment": "検証環境",
            "requested_privilege": "特権",
        },
        "expected": {"decision": "許可", "reason_code": "not_prohibited_combination"},
    },
    {
        "name": "開発担当者が本番環境だが一般権限 → 許可(possible_values外の値が既定行に落ちる・U2)",
        "facts": {
            "operator_role": "システム開発担当",
            "target_environment": "本番環境",
            "requested_privilege": "一般",
        },
        "expected": {"decision": "許可", "reason_code": "not_prohibited_combination"},
    },
    {
        "name": "運用担当者が本番環境で特権操作 → 許可(possible_values外の値が既定行に落ちる・U2)",
        "facts": {
            "operator_role": "運用担当",
            "target_environment": "本番環境",
            "requested_privilege": "特権",
        },
        "expected": {"decision": "許可", "reason_code": "not_prohibited_combination"},
    },
]


def _classify(clause_text: str, clause_ref: str, use_oracle: bool, oracle: dict) -> tuple[AxisResult, AxisResult]:
    if use_oracle:
        flagged, _ = check_vocab_flag(clause_text)
        return (
            AxisResult(
                label=oracle["axis1"],
                confidence=1.0,
                rationale="(--use-oracle: ②分類をスキップしオラクル値を使用)",
                evidence_quote=clause_text,
                quote_verified=True,
                vocab_flagged=flagged,
            ),
            AxisResult(
                label=oracle["axis2"],
                confidence=1.0,
                rationale="(--use-oracle: ②分類をスキップしオラクル値を使用)",
                evidence_quote=clause_text,
                quote_verified=True,
                vocab_flagged=flagged,
            ),
        )

    r1 = LocalLlmAxis1Classifier().classify(clause_text, clause_ref)
    r2 = LocalLlmAxis2Classifier().classify(clause_text, clause_ref)
    to_axis = lambda r: AxisResult(  # noqa: E731
        label=r.label,
        confidence=r.confidence,
        rationale=r.rationale,
        evidence_quote=r.evidence_quote,
        quote_verified=r.quote_verified,
        vocab_flagged=r.vocab_flagged,
    )
    return to_axis(r1), to_axis(r2)


def run(
    use_oracle: bool = False,
    out_dir: Path = DEFAULT_OUT_DIR,
    registry_path: Path = DEFAULT_REGISTRY,
) -> int:
    clauses = {c.clause_id: c for c in load_clauses(DEFAULT_TESTDATA)}
    clause = clauses[CLAUSE_ID]
    print(f"[対象条文] {CLAUSE_ID} ({clause.article_no} {clause.article_title})")
    print(f"  {clause.text_raw}")
    print()

    # --- ② 2軸分類 ---
    axis1, axis2 = _classify(
        clause.text_raw,
        clause.clause_id,
        use_oracle,
        {"axis1": clause.axis1, "axis2": clause.axis2},
    )
    print("[② 2軸分類]")
    for name, result, gold in (("軸1", axis1, clause.axis1), ("軸2", axis2, clause.axis2)):
        mark = "OK" if result.label == gold else "MISS"
        print(f"  {name}: pred={result.label} gold={gold} conf={result.confidence:.2f} "
              f"quote_verified={result.quote_verified} vocab_flagged={result.vocab_flagged} {mark}")
    print()

    # --- ③a IR構築 ---
    ir = build_golden_path_ir(axis1, axis2, clause_text=clause.text_raw)
    print("[③a IR]")
    print(f"  actor={ir.actor} action={ir.action.normalized}(parent={ir.action.parent}, "
          f"is_new={ir.action.is_new}) scope={ir.scope}")
    print(f"  level_outcome={ir.level_determination.outcome}")
    print(f"  compound_fact={ir.compound_fact_application.option_taken}")
    print(f"  human_review_status={ir.human_review_status} should_review={ir.review_gates.should_review}")
    for reason in ir.review_gates.reasons():
        print(f"    - gate: {reason}")
    print()

    # --- 4点セット生成(クロス検証 C1〜C10 を含む) ---
    registry = Registry.load(registry_path)
    print(f"[登録簿] {registry_path}")
    print(f"  層1={len(registry.decision_tables)}件 層2={len(registry.rule_rows)}件 "
          f"層3={len(registry.fact_registry)}件")
    print()

    bundle = generate_wiring_bundle(ir, registry=registry)
    paths = bundle.write_all(out_dir, "privileged_operation")
    print("[4点セット生成] クロス検証 C1〜C10 通過")
    for key, path in paths.items():
        print(f"  {key}: {path}")
    print(f"  required_facts: {bundle.routing_table['routes'][0]['required_facts']}")
    print()

    # --- 生成したDMNの評価 ---
    print("[DMN評価(自作エンジン)]")
    model = DmnModel(str(paths["dmn"]))
    failures = 0
    for scenario in SCENARIOS:
        result = model.evaluate(DECISION_ID, scenario["facts"])
        actual = {k: v for k, v in result.items() if not k.startswith("_")}
        ok = all(actual.get(k) == v for k, v in scenario["expected"].items())
        if not ok:
            failures += 1
        print(f"  [{'OK' if ok else 'FAIL'}] {scenario['name']}")
        print(f"        -> {actual} (matched_rule={result.get('_matched_rule')})")
        if not ok:
            print(f"        expected: {scenario['expected']}")
    print()
    print(f"シナリオ: {len(SCENARIOS) - failures}/{len(SCENARIOS)} 一致")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--use-oracle",
        action="store_true",
        help="②分類器を呼ばずオラクル値を使う(Ollama不要。配線部分のみ検証したい場合)",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="入力となる登録簿のパス(既存の決定表とfact台帳。既定: registry/registry.json)",
    )
    args = parser.parse_args()
    raise SystemExit(
        run(use_oracle=args.use_oracle, out_dir=args.out_dir, registry_path=args.registry)
    )
