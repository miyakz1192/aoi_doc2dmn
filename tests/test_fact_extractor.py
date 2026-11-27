"""事実の抽出(第2要素)の単体テスト。前段設計書10章 v0.13(P34〜P42)。

| 確かめること | 根拠 |
| --- | --- |
| 値の正規化は全角→半角と括弧の除去だけ | FU1・D56・P24 |
| 根拠の語句が条文にあるかをプログラムが確かめる | P36・手順2 |
| 既存の fact を使うのは**値が完全一致したときだけ** | P37・手順3a |
| 手順3b の提案では寄せない | **P37(この要素の要点)** |
| 一致が2つ以上なら1つ目に寄せて人に回す | 手順3a |
| 比較型は条文に比べる語があるときだけ | P38・D3・D23 |
| sources は機械的に作る | P39・手順5 |
| ゲートは5つ、疑いと設計上の確認に分かれる | P41 |
| 寄せる相手は**同じ行為の決定表が使う** fact に限る | **P37 改訂【v0.13】** |
| 手順3b の答えは含む関係でも当てる | Q5(第50回) |
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_frontstage.fact_extractor import (  # noqa: E402
    CONDITION_KINDS,
    FactExtractor,
    ScriptedJsonLlm,
    build_sources,
    clause_has_comparison_word,
    match_answer,
    normalize_value,
    quote_in_clause,
)
from src.stage3_frontstage.fact_ledger import (  # noqa: E402
    NO_MATCH,
    FactEntry,
    FactLedger,
    FactWiring,
)
from src.stage3_generate.ir_schema import (  # noqa: E402
    DESIGN_CHECK_GATES,
    OTHER_VALUE,
    SUSPICION_GATES,
    ComparisonFact,
    PassthroughFact,
    gate_kind,
)
from src.stage3_generate.registry import Registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

GOLDEN_TEXT = (
    "システム開発を担当する者が本番環境において特権/管理者権限で操作を行うことを禁止する。"
)


def passthrough(variable: str, values: list[str], note: str = "その他の状態") -> FactEntry:
    return FactEntry(
        variable=variable,
        primitive="passthrough",
        possible_values=[*values, OTHER_VALUE],
        other_value_note=note,
    )


@pytest.fixture
def empty_ledger() -> FactLedger:
    return FactLedger()


@pytest.fixture
def bootstrap_ledger() -> FactLedger:
    """初期登録の登録簿の層3(あおいの7件)。"""
    registry = Registry.load(ROOT / "registry" / "registry.json")
    return FactLedger.from_json(registry.fact_registry)


# --------------------------------------------------------------------------
# 手順2: 語句の確かめと値の正規化(FU1)
# --------------------------------------------------------------------------


def test_the_value_normalization_only_does_width_and_parentheses():
    """FU1: プログラムが当てるのは全角→半角と括弧の除去だけ。"""
    assert normalize_value("産後１年を経過しない女性労働者")[0] == (
        "産後1年を経過しない女性労働者"
    )
    assert normalize_value("１８歳未満の者")[0] == "18歳未満の者"
    value, applied = normalize_value("深夜（午後１０時から午前５時まで）労働")
    assert value == "深夜労働"
    assert applied == ["括弧の除去"]


def test_the_value_normalization_does_not_turn_a_phrase_into_a_noun():
    """FU1: 体言化・並列の展開は手順1 の LLM の仕事(プログラムはしない)。"""
    assert normalize_value("システム開発を担当する者")[0] == "システム開発を担当する者"


def test_the_basis_quote_is_checked_against_the_clause_ignoring_parentheses():
    """手順2・P24: 照合は条文と語句の両方から丸括弧を除いて行う。"""
    assert quote_in_clause("本番環境", GOLDEN_TEXT)
    assert not quote_in_clause("検証環境", GOLDEN_TEXT)
    clause = "深夜（午後１０時から午前５時まで）労働に従事させない。"
    assert quote_in_clause("深夜労働", clause)


def test_a_condition_whose_quote_is_not_in_the_clause_raises_the_gate():
    """P41: 値の根拠の語句が条文にないと fact_value_unverified が立つ。"""
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "検証環境", "value": "検証環境", "kind": "状況"}
                ],
                "rationale": "",
            },
            {
                "facts": [
                    {
                        "variable": "target_environment",
                        "primitive": "passthrough",
                        "values": ["検証環境"],
                        "other_value_note": "他の環境",
                    }
                ]
            },
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT, "ref", FactLedger(), action_name="操作"
    )
    assert result.trace.conditions[0].in_clause is False
    assert "fact_value_unverified" in result.gates.gate_names()


# --------------------------------------------------------------------------
# 手順3a: 値の完全一致(P37)
# --------------------------------------------------------------------------


def test_an_exact_value_match_reuses_the_existing_fact_with_one_llm_call():
    """手順3a・10.3節: すべての値が既存に一致すれば LLM の呼び出しは1回。"""
    ledger = FactLedger(entries=[passthrough("target_environment", ["本番環境"])])
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"}
                ],
                "rationale": "",
            }
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT, "ref", ledger, action_name="操作"
    )
    assert result.facts.referenced_facts == ["target_environment"]
    assert result.facts.new_facts == []
    assert result.trace.llm_calls == 1
    assert result.gates.gate_names() == []


def test_two_exact_matches_take_the_first_and_route_to_a_human():
    """手順3a: 一致が2つ以上なら1つ目に寄せ、fact_possible_duplicate を立てる。"""
    ledger = FactLedger(
        entries=[
            passthrough("target_environment", ["本番環境"]),
            passthrough("deploy_environment", ["本番環境"]),
        ]
    )
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"}
                ],
                "rationale": "",
            }
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT, "ref", ledger, action_name="操作"
    )
    assert result.facts.referenced_facts == ["target_environment"]
    assert result.trace.matches[0].ambiguous is True
    assert "fact_possible_duplicate" in result.gates.gate_names()


def test_the_reserved_other_value_is_never_matched():
    """D45: 予約語「その他」は条文の値ではないので一致の相手にしない。"""
    ledger = FactLedger(entries=[passthrough("target_environment", ["本番環境"])])
    assert ledger.match_value(OTHER_VALUE).matches == []


# --------------------------------------------------------------------------
# 手順3b: 提案では寄せない(P37。この要素の要点)
# --------------------------------------------------------------------------


def test_a_suggestion_from_step_3b_never_reuses_the_existing_fact():
    """**P37**: 3b が既存の fact を提案しても、出力は新規候補のままである。"""
    ledger = FactLedger(entries=[passthrough("operator_role", ["システム開発担当"])])
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"}
                ],
                "rationale": "",
            },
            # LLM は「operator_role と同じ列」と答えるが、値は一致していない
            {
                "assignments": [
                    {"value": "本番環境", "fact": "operator_role", "reason": "近い"}
                ]
            },
            {
                "facts": [
                    {
                        "variable": "target_environment",
                        "primitive": "passthrough",
                        "values": ["本番環境"],
                        "other_value_note": "他の環境",
                    }
                ]
            },
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT, "ref", ledger, action_name="操作"
    )
    assert result.facts.referenced_facts == []
    assert [f.variable for f in result.facts.new_facts] == ["target_environment"]
    assert result.trace.suggestions[0].answer == "operator_role"
    assert "fact_reuse_suggested" in result.gates.gate_names()
    # 設計上の確認なので、誤りの疑いのゲートではない(P21・P41)
    assert gate_kind("fact_reuse_suggested") == "design_check"


def test_step_3b_is_skipped_when_layer3_is_empty():
    """提案する相手が無ければ手順3b は呼ばない(LLM の呼び出しは2回)。"""
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"}
                ],
                "rationale": "",
            },
            {
                "facts": [
                    {
                        "variable": "target_environment",
                        "primitive": "passthrough",
                        "values": ["本番環境"],
                        "other_value_note": "他の環境",
                    }
                ]
            },
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT, "ref", FactLedger(), action_name="操作"
    )
    assert result.trace.suggestions == []
    assert result.trace.llm_calls == 2


def test_a_suggestion_outside_the_ledger_is_treated_as_no_suggestion():
    """閉じた選択(P5 と同じ扱い): 層3に無い名前は提案なしにする。"""
    ledger = FactLedger(entries=[passthrough("operator_role", ["システム開発担当"])])
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"}
                ],
                "rationale": "",
            },
            {
                "assignments": [
                    {"value": "本番環境", "fact": "env_column", "reason": ""}
                ]
            },
            {"facts": []},
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT, "ref", ledger, action_name="操作"
    )
    suggestion = result.trace.suggestions[0]
    assert suggestion.answer is None
    assert suggestion.off_list is True
    assert "fact_reuse_suggested" not in result.gates.gate_names()


# --------------------------------------------------------------------------
# 手順4: 新規候補(P38)
# --------------------------------------------------------------------------


def _propose_with(primitive: str, clause: str, values: list[str], quote: str):
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": quote, "value": values[0], "kind": "状態"}
                ],
                "rationale": "",
            },
            {
                "facts": [
                    {
                        "variable": "identity_check",
                        "primitive": primitive,
                        "values": values,
                        "other_value_note": "確認できた",
                    }
                ]
            },
        ]
    )
    return FactExtractor(llm).extract(clause, "ref", FactLedger(), action_name="伝達")


def test_a_comparison_candidate_is_kept_when_the_clause_compares_two_values():
    """P38: 条文に「一致」などの語があるときだけ比較型の候補にする。"""
    clause = "生年月日及び連絡先の一致が確認できない場合、口頭で伝達してはならない。"
    assert clause_has_comparison_word(clause)
    result = _propose_with("exact_match", clause, ["一致"], "一致が確認できない")
    fact = result.facts.new_facts[0]
    assert isinstance(fact, ComparisonFact)
    assert fact.primitive == "exact_match"
    assert "comparison_fact_proposed" in result.gates.gate_names()
    assert gate_kind("comparison_fact_proposed") == "suspicion"


def test_a_comparison_candidate_is_demoted_when_the_clause_does_not_compare():
    """P38: 比べる語が無ければ passthrough に戻す(戻したことは Trace に残す)。"""
    result = _propose_with(
        "exact_match", GOLDEN_TEXT, ["本番環境"], "本番環境"
    )
    fact = result.facts.new_facts[0]
    assert isinstance(fact, PassthroughFact)
    assert result.trace.proposals[0].primitive_demoted_from == "exact_match"
    assert "comparison_fact_proposed" not in result.gates.gate_names()


def test_a_bootstrap_only_primitive_is_demoted():
    """D23: identifiability_classifier は新しい条文の new_facts では宣言できない。"""
    clause = "生年月日の一致が確認できない場合、伝達してはならない。"
    result = _propose_with(
        "identifiability_classifier", clause, ["一致"], "一致が確認できない"
    )
    assert isinstance(result.facts.new_facts[0], PassthroughFact)
    assert result.trace.proposals[0].primitive_demoted_from == (
        "identifiability_classifier"
    )


def test_a_value_the_clause_does_not_give_is_dropped_and_gated():
    """手順4 が条文に無い値を返したら、候補に入れずゲートを立てる。"""
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"}
                ],
                "rationale": "",
            },
            {
                "facts": [
                    {
                        "variable": "target_environment",
                        "primitive": "passthrough",
                        # 「検証環境」は条文に無い(LLM が足した値)
                        "values": ["本番環境", "検証環境"],
                        "other_value_note": "他の環境",
                    }
                ]
            },
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT, "ref", FactLedger(), action_name="操作"
    )
    fact = result.facts.new_facts[0]
    assert isinstance(fact, PassthroughFact)
    assert fact.possible_values == ["本番環境"]
    assert result.trace.proposals[0].dropped_values == ["検証環境"]
    assert "fact_value_unverified" in result.gates.gate_names()


def test_a_value_already_reused_at_3a_is_not_counted_as_an_unverified_value():
    """手順3a で寄せた値を手順4 が混ぜてきても、条文に無い値ではない。"""
    ledger = FactLedger(entries=[passthrough("usage_authorization", ["許可なし"])])
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "許可なく", "value": "許可なし", "kind": "状態"},
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"},
                ],
                "rationale": "",
            },
            {"assignments": [{"value": "本番環境", "fact": NO_MATCH, "reason": ""}]},
            {
                "facts": [
                    {
                        "variable": "target_environment",
                        "primitive": "passthrough",
                        "values": ["本番環境", "許可なし"],
                        "other_value_note": "他の環境",
                    }
                ]
            },
        ]
    )
    clause = "許可なく本番環境において操作を行うことを禁止する。"
    result = FactExtractor(llm).extract(clause, "ref", ledger, action_name="操作")
    proposal = result.trace.proposals[0]
    assert proposal.values == ["本番環境"]
    assert proposal.reused_values == ["許可なし"]
    assert proposal.dropped_values == []
    assert "fact_value_unverified" not in result.gates.gate_names()


def test_a_passthrough_candidate_with_no_value_left_is_not_put_in_the_ir():
    """値がすべて 3a で寄ったら、その候補は列にならない(D4)。記録には残る。"""
    ledger = FactLedger(entries=[passthrough("usage_authorization", ["許可なし"])])
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "許可なく", "value": "許可なし", "kind": "状態"}
                ],
                "rationale": "",
            },
            {
                "facts": [
                    {
                        "variable": "recipient_authorization",
                        "primitive": "passthrough",
                        "values": ["許可なし"],
                        "other_value_note": "許可されている",
                    }
                ]
            },
        ]
    )
    clause = "許可なく提供してはならない。"
    result = FactExtractor(llm).extract(clause, "ref", ledger, action_name="提供")
    assert result.facts.new_facts == []
    assert result.facts.referenced_facts == ["usage_authorization"]
    # 候補そのものは Trace に残り、名前の近さで人に回る(P41)
    assert result.trace.proposals == []  # 手順4 は呼ばれない(一致しない値が無い)


# --------------------------------------------------------------------------
# 手順5: sources と referenced_facts(P39・D25)
# --------------------------------------------------------------------------


def test_the_sources_are_built_mechanically():
    """P39: from は action_request、path は fact の名前(比較型は引数ごと)。"""
    sources = build_sources("operator_role", "passthrough")
    assert list(sources) == ["value"]
    assert sources["value"].from_ == "action_request"
    assert sources["value"].path == "operator_role"
    comparison = build_sources("birth_check", "date_match")
    assert sorted(comparison) == ["claimed", "registered"]
    assert comparison["claimed"].path == "birth_check.claimed"


def test_the_referenced_facts_are_the_facts_matched_at_3a_without_repeats():
    """D25 の向き: 3a で一致した fact を宣言順に、重ねずに並べる。"""
    ledger = FactLedger(
        entries=[passthrough("usage_target", ["会社の施設", "物品等"])]
    )
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "会社の施設", "value": "会社の施設", "kind": "対象"},
                    {"quote": "物品等", "value": "物品等", "kind": "対象"},
                ],
                "rationale": "",
            }
        ]
    )
    clause = "許可なく職務以外の目的で会社の施設、物品等を使用しないこと。"
    result = FactExtractor(llm).extract(clause, "ref", ledger, action_name="使用")
    assert result.facts.referenced_facts == ["usage_target"]


# --------------------------------------------------------------------------
# 層3(fact 台帳)
# --------------------------------------------------------------------------


def test_a_value_added_to_an_existing_fact_goes_before_the_reserved_word():
    """D45: 予約語「その他」は末尾のまま、新しい値はその手前に入る。"""
    ledger = FactLedger(entries=[passthrough("target_environment", ["本番環境"])])
    added = ledger.add_values("target_environment", ["検証環境", "本番環境"])
    assert added == ["検証環境"]
    entry = ledger.find("target_environment")
    assert entry is not None
    assert entry.possible_values == ["本番環境", "検証環境", OTHER_VALUE]


def test_a_value_shared_with_a_comparison_fact_is_flagged_but_not_reused(
    bootstrap_ledger,
):
    """P37・P41: 比較型は 3a の一致の相手ではないが、値が同じなら人に見せる。

    あおいの初期登録では「一致」を name_match・birth_match・phone_match の3つが
    共有しているので、値だけではどの fact かが決まらない。
    """
    assert bootstrap_ledger.match_value("一致").matches == []
    assert bootstrap_ledger.comparison_value_matches("一致") == [
        "name_match",
        "birth_match",
        "phone_match",
    ]
    check = bootstrap_ledger.duplicate_check("identity_match", ["一致"])
    assert check.flagged is True
    assert [s["fact"] for s in check.shared_values] == [
        "name_match",
        "birth_match",
        "phone_match",
    ]


def test_the_ledger_prompt_block_shows_names_and_values_only(bootstrap_ledger):
    """W1: 層3を LLM に見せるのは名前と値だけ(定義の文は渡さない)。"""
    block = bootstrap_ledger.prompt_block()
    assert "contact_consent" in block
    assert "同意" in block
    assert "あおいの既存DMN" not in block


# --------------------------------------------------------------------------
# ゲートの区分(P41)
# --------------------------------------------------------------------------


def test_the_five_fact_gates_are_split_into_suspicion_and_design_check():
    """P41: 疑い3つ・設計上の確認2つ。"""
    for name in (
        "fact_value_unverified",
        "fact_possible_duplicate",
        "comparison_fact_proposed",
    ):
        assert name in SUSPICION_GATES
        assert gate_kind(name) == "suspicion"
    for name in ("fact_reuse_suggested", "new_fact_unapproved"):
        assert name in DESIGN_CHECK_GATES
        assert gate_kind(name) == "design_check"


def test_the_four_condition_kinds_are_the_ones_in_p35():
    assert CONDITION_KINDS == ("対象", "主体の属性", "状況", "状態")


# --------------------------------------------------------------------------
# 通し(ゴールデンパスの条文)
# --------------------------------------------------------------------------


def test_the_golden_path_clause_reproduces_the_approved_gold_facts(bootstrap_ledger):
    """承認済みの正解 IR(6.7 特権アカウント)の facts 欄と同じ形になる。"""
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {
                        "quote": "システム開発を担当する者",
                        "value": "システム開発担当",
                        "kind": "主体の属性",
                    },
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"},
                    {"quote": "特権/管理者権限", "value": "特権", "kind": "状況"},
                ],
                "rationale": "",
            },
            {
                "assignments": [
                    {"value": "システム開発担当", "fact": NO_MATCH, "reason": ""},
                    {"value": "本番環境", "fact": NO_MATCH, "reason": ""},
                    {"value": "特権", "fact": NO_MATCH, "reason": ""},
                ]
            },
            {
                "facts": [
                    {
                        "variable": "operator_role",
                        "primitive": "passthrough",
                        "values": ["システム開発担当"],
                        "other_value_note": "システム開発担当以外の担当",
                    },
                    {
                        "variable": "target_environment",
                        "primitive": "passthrough",
                        "values": ["本番環境"],
                        "other_value_note": "本番環境以外の環境",
                    },
                    {
                        "variable": "requested_privilege",
                        "primitive": "passthrough",
                        "values": ["特権"],
                        "other_value_note": "特権以外の権限",
                    },
                ]
            },
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT,
        "IPA情報セキュリティ関連規程 6.7 特権アカウント(第3項)",
        bootstrap_ledger,
        action_name="操作",
        action_surface="特権/管理者権限で操作を行う",
    )
    gold = json.loads(
        (ROOT / "testdata" / "ir_gold" / "ipa_secguide_v4_6_7_1.json").read_text(
            encoding="utf-8"
        )
    )
    expected = gold["ir"]["facts"]["new_facts"]
    assert [f.variable for f in result.facts.new_facts] == [
        f["variable"] for f in expected
    ]
    for fact, want in zip(result.facts.new_facts, expected):
        assert isinstance(fact, PassthroughFact)
        assert fact.possible_values == want["possible_values"]
        assert fact.other_value_note == want["other_value_note"]
        assert fact.approved_by is None  # D53: 承認は人
    assert result.facts.referenced_facts == []
    assert result.trace.llm_calls == 3
    assert result.gates.gate_names() == ["new_fact_unapproved"]


# --------------------------------------------------------------------------
# P37 改訂【v0.13】: 寄せる相手は同じ行為の決定表が使う fact に限る(第50回パートA)
# --------------------------------------------------------------------------


def wired_ledger(
    entries: list[FactEntry], tables: list[dict], rows: list[dict] | None = None
) -> FactLedger:
    """層3と配線(層1・層2)を持った台帳(P37 改訂の寄せ先を試す用)。"""
    return FactLedger(
        entries=entries,
        wiring=FactWiring(tables=tables, rows=rows or [], action_ledger=[]),
    )


USE_TABLE = {
    "decision_table_id": "dt_facility_property_use",
    "decision_id": "decision_facility_property_use",
    "entry_points": [{"actor": "あおい", "action": "使用"}],
    "input_columns": ["usage_authorization", "usage_purpose", "usage_target"],
    "depends_on_decisions": [],
}


def test_a_generic_value_does_not_attach_to_another_actions_fact():
    """第11条(使用)の「許可なし」は、提供の条文では `usage_authorization` に寄らない。

    第49回の実機とオラクルで、どちらも**重大な誤り(誤った再利用)**になった件である。
    値だけでは「使用の許可」と「提供先の許可」を区別できないので、寄せ先を
    **同じ行為の決定表が使っている fact** に限る(P37 改訂)。
    """
    ledger = wired_ledger(
        [passthrough("usage_authorization", ["許可なし"])], [USE_TABLE]
    )
    match = ledger.match_value("許可なし", action="提供")
    assert match.chosen is None
    assert [o["fact"] for o in match.out_of_scope] == ["usage_authorization"]
    assert [o["actions"] for o in match.out_of_scope] == [["使用"]]
    assert ledger.action_scope("提供") == []


def test_the_same_action_still_attaches_on_an_exact_value_match():
    """同じ行為(使用)の条文なら、これまでどおり値の完全一致で寄せる(P37)。"""
    ledger = wired_ledger(
        [passthrough("usage_authorization", ["許可なし"])], [USE_TABLE]
    )
    match = ledger.match_value("許可なし", action="使用")
    assert match.chosen == "usage_authorization"
    assert match.out_of_scope == []
    assert ledger.action_scope("使用") == [
        "usage_authorization",
        "usage_purpose",
        "usage_target",
    ]


def test_a_match_in_another_action_goes_to_the_step_3b_suggestion():
    """寄せなかった一致は手順3b に回り、新規候補になる(P37 改訂・パートA3)。"""
    ledger = wired_ledger(
        [passthrough("usage_authorization", ["許可なし"])], [USE_TABLE]
    )
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "許可されていない", "value": "許可なし", "kind": "状態"}
                ],
                "rationale": "",
            },
            {
                "assignments": [
                    {
                        "value": "許可なし",
                        "fact": "usage_authorization",
                        "reason": "近い",
                    }
                ]
            },
            {
                "facts": [
                    {
                        "variable": "recipient_authorization",
                        "primitive": "passthrough",
                        "values": ["許可なし"],
                        "other_value_note": "許可のある提供先",
                    }
                ]
            },
        ]
    )
    result = FactExtractor(llm).extract(
        "許可されていない組織、人に提供してはならない。",
        "ref",
        ledger,
        action_name="提供",
    )
    # 寄せていない(referenced_facts は空)
    assert result.facts.referenced_facts == []
    assert [f.variable for f in result.facts.new_facts] == ["recipient_authorization"]
    # 手順3b に回り、提案として記録されている
    assert result.trace.suggestions[0].answer == "usage_authorization"
    assert "fact_reuse_suggested" in result.gates.gate_names()
    # 値が一致したことは記録に残り、疑いのゲートで人に見せる
    assert result.trace.out_of_scope_hits == [
        {
            "value": "許可なし",
            "facts": ["usage_authorization"],
            "actions": ["使用"],
            "ancestor_only": False,
        }
    ]
    assert "fact_possible_duplicate" in result.gates.gate_names()


def test_the_scope_includes_the_intermediate_decisions_of_the_same_action():
    """中間判定(entry_points が空)の fact も、その行為の寄せ先に入る(D16)。"""
    tables = [
        {
            "decision_table_id": "dt_transmission",
            "decision_id": "decision_transmission",
            "entry_points": [{"actor": "あおい", "action": "送信"}],
            "input_columns": ["情報分類判定.sensitivity", "contact_consent"],
            "depends_on_decisions": ["decision_classification"],
        },
        {
            "decision_table_id": "dt_classification",
            "decision_id": "decision_classification",
            "entry_points": [],
            "input_columns": ["has_medical", "has_pii"],
            "depends_on_decisions": [],
        },
    ]
    ledger = wired_ledger([passthrough("has_pii", ["含む"])], tables)
    assert ledger.action_scope("送信") == [
        "contact_consent",
        "has_medical",
        "has_pii",
    ]
    # ドット参照(別の判定の出力)は fact ではないので入らない
    assert "情報分類判定.sensitivity" not in (ledger.action_scope("送信") or [])
    assert ledger.match_value("含む", action="送信").chosen == "has_pii"


def test_an_abstract_action_is_not_a_reuse_target_but_is_counted():
    """抽象の行為(提供)の fact は寄せる相手にしない。祖先かどうかは記録する(パートA2)。"""
    tables = [
        {
            "decision_table_id": "dt_provision",
            "decision_id": "decision_provision",
            "entry_points": [{"actor": "あおい", "action": "提供"}],
            "input_columns": ["recipient_authorization"],
            "depends_on_decisions": [],
        }
    ]
    wiring = FactWiring(
        tables=tables,
        rows=[],
        action_ledger=[
            {"name": "提供", "kind": "abstract", "parent": None},
            {"name": "開示", "kind": "concrete", "parent": "提供"},
        ],
    )
    ledger = FactLedger(
        entries=[passthrough("recipient_authorization", ["許可なし"])], wiring=wiring
    )
    match = ledger.match_value("許可なし", action="開示")
    assert match.chosen is None
    assert match.ancestor_only is True
    assert wiring.ancestor_facts("開示") == ["recipient_authorization"]


def test_the_rows_of_layer2_also_name_the_facts_of_an_action():
    """層2の行の条件も、その行為の寄せ先になる(パートA1)。"""
    tables = [
        {
            "decision_table_id": "dt_x",
            "decision_id": "decision_x",
            "entry_points": [{"actor": "あおい", "action": "操作"}],
            "input_columns": [],
            "depends_on_decisions": [],
        }
    ]
    rows = [{"decision_table_id": "dt_x", "conditions": {"operator_role": "開発"}}]
    ledger = wired_ledger([passthrough("operator_role", ["開発"])], tables, rows)
    assert ledger.action_scope("操作") == ["operator_role"]
    assert ledger.match_value("開発", action="操作").chosen == "operator_role"


def test_without_wiring_the_ledger_keeps_the_v012_behaviour():
    """配線が無い台帳(素の単体テスト)は、これまでどおり層3の全体を相手にする。"""
    ledger = FactLedger(entries=[passthrough("usage_authorization", ["許可なし"])])
    assert ledger.action_scope("提供") is None
    assert ledger.match_value("許可なし", action="提供").chosen == "usage_authorization"


# --------------------------------------------------------------------------
# Q5: 手順3b の答えの照合(第50回パートC)
# --------------------------------------------------------------------------


def test_the_answer_is_matched_to_the_value_by_containment():
    """LLM が見出しを言い直しても、含む関係で当たれば提案として扱う(Q5)。"""
    entry, how = match_answer(
        "許可なし", {"許可なしの状態": {"fact": "usage_authorization"}}
    )
    assert how == "contains"
    assert entry["fact"] == "usage_authorization"


def test_an_answer_that_matches_two_values_is_dropped():
    """2つ以上に当たる見出しは、どれか決められないので提案なしにする(Q5)。"""
    entry, how = match_answer(
        "一致", {"一致": {"fact": "a"}, "不一致": {"fact": "b"}}
    )
    # 完全一致が先に当たる
    assert how == "exact" and entry["fact"] == "a"
    entry, how = match_answer("確認", {"確認なし": {"fact": "a"}, "確認済": {"fact": "b"}})
    assert how == "ambiguous" and entry == {}


def test_the_suggestion_records_how_the_answer_was_matched():
    """照合の仕方を Trace に残す(読めない指標にしないため。Q5)。"""
    ledger = FactLedger(entries=[passthrough("operator_role", ["システム開発担当"])])
    llm = ScriptedJsonLlm(
        [
            {
                "conditions": [
                    {"quote": "本番環境", "value": "本番環境", "kind": "状況"}
                ],
                "rationale": "",
            },
            # 見出しを「本番環境での作業」と言い直してきた
            {
                "assignments": [
                    {
                        "value": "本番環境での作業",
                        "fact": "operator_role",
                        "reason": "近い",
                    }
                ]
            },
            {"facts": []},
        ]
    )
    result = FactExtractor(llm).extract(
        GOLDEN_TEXT, "ref", ledger, action_name="操作"
    )
    suggestion = result.trace.suggestions[0]
    assert suggestion.answer == "operator_role"
    assert suggestion.matched_by == "contains"
    assert "含む関係" in suggestion.reason


def test_an_answer_that_says_no_match_in_the_heading_is_matched_by_position():
    """観測された型(K71): LLM が見出しにも「該当なし」と書く。

    第50回の実測では、手順3b に渡した値15件のうち10件がこの形だった（第49回の報告で
    「LLM が値を言い直す」と書いたのは誤りで、実際は見出しにも「該当なし」と書いて
    いた）。答えの数が渡した値の数と同じで、`fact` が「該当なし」のときだけ並び順で
    当てる。**実在の列名のときは当てない**ので、この当て方が提案を作ることはない。
    """
    answers = [
        {"value": NO_MATCH, "fact": NO_MATCH, "reason": "本番環境は無い"},
        {"value": NO_MATCH, "fact": NO_MATCH, "reason": "特権権限は無い"},
    ]
    by_value = {NO_MATCH: answers[0]}
    entry, how = match_answer(
        "本番環境", by_value, position=0, answers=answers, total=2
    )
    assert how == "no_match_answer"
    assert entry["reason"] == "本番環境は無い"
    # 答えが「該当なし」なので、提案にはならない
    assert entry["fact"] == NO_MATCH


def test_a_positional_answer_with_a_real_column_is_not_matched():
    """並び順で当てるのは `fact` が「該当なし」のときだけ(K71 の歯止め)。"""
    answers = [
        {"value": "許可の有無", "fact": "usage_authorization", "reason": "近い"},
        {"value": NO_MATCH, "fact": NO_MATCH, "reason": "無い"},
    ]
    by_value = {"許可の有無": answers[0], NO_MATCH: answers[1]}
    entry, how = match_answer("職務以外の目的", by_value, position=0, answers=answers, total=2)
    assert how == "none" and entry == {}
