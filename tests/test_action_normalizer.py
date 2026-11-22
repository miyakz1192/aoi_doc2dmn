"""前段(条文 → IR)の第1要素: Action 正規化のテスト。

第31回 HANDOFF パートB・C。前段設計書 v0.4 の3〜5章が仕様である。
**LLM は固定の応答を返す偽物に差し替えて行う**(5.3節「単体テストは LLM を差し替えて
行い、Ollama なしで通るようにする」)。対象は次の7つ。

| 確かめること | 根拠 |
| --- | --- |
| 様相の削り落としと逐語の確認(プログラムの仕事) | 前段設計書4.2節 手順2・P4 |
| 行為の語句の候補が2つのとき | P17 |
| 動詞の一致の検査 | P15・4.2節 手順4 |
| ゲート(7つ) | 4.5節 |
| 名前・定義の規則(様相を入れない) | P16・4.6節 |
| 教師ありの台帳の更新(段・親も足す) | P13・4.2節 |
| 誤りの4分類(誤統合・適用漏れ・過剰適用・取りこぼし) | 5.1節・P9 |

Ollama を使う実際の評価は `src/stage3_frontstage/run_action_eval.py` で行う。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_frontstage.action_ledger import (  # noqa: E402
    NAME_RATIO_THRESHOLD,
    NO_MATCH,
    SHARED_SUFFIX_THRESHOLD,
    ActionEntry,
    ActionLedger,
    definition_overlap,
    name_ratio,
    shared_suffix_length,
)
from src.stage3_frontstage.action_normalizer import (  # noqa: E402
    TEMPERATURE,
    ActionNormalizer,
    ScriptedJsonLlm,
    core_verbs_match,
)
from src.stage3_frontstage.modality import (  # noqa: E402
    MODALITY_WORDS,
    NORMATIVE_MODALITY_WORDS,
    check_surface,
    find_modality,
    strip_parentheses,
    find_normative_modality,
    strip_modality,
)
from src.stage3_frontstage.run_action_eval import (  # noqa: E402
    classify,
    judge,
    load_ledger,
    teach,
)
from src.stage3_generate.registry import Registry  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = REPO_ROOT / "testdata" / "action_gold" / "action_gold_v3.json"
GOLD_V2_PATH = REPO_ROOT / "testdata" / "action_gold" / "action_gold_v2.json"
GOLD_V1_PATH = REPO_ROOT / "testdata" / "action_gold" / "action_gold_v1.json"
REGISTRY_PATH = REPO_ROOT / "registry" / "registry.json"

CLAUSE = "許可なく職務以外の目的で会社の施設、物品等を使用しないこと。"
SURFACE = "会社の施設、物品等を使用"
# 初期登録の送信(旧: 外部送信)の定義(前段設計書 v0.6 の図3。D82)
SEED_DEFINITION = "電子的に宛先へ送ること"


@pytest.fixture
def action_gold() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def seeded_ledger() -> ActionLedger:
    """出発点(あおいの送信)だけの台帳。"""
    return ActionLedger(
        entries=[
            ActionEntry(
                name="送信",
                definition=SEED_DEFINITION,
                kind="concrete",
                actors=["あおい"],
            )
        ]
    )


# v0.9: 3a で寄らなければ必ず手順4 を通るので、名前を見ないテストでも新規候補の
# 応答が必要になる(P33)
DEFAULT_PROPOSAL = {
    "name": "（既定の新規候補）",
    "definition": "テスト用の定義",
    "kind": "concrete",
    "parent": None,
    "basis_quotes": [],
}


def _responses(
    candidates: list[dict],
    choice: str,
    *,
    proposal: dict | None = None,
    considered: list[dict] | None = None,
) -> list[dict]:
    """k=1 の呼び出しの応答を組み立てる(v0.9 の手順。P27・P33)。

    1回目は抜き出し(手順1)。2回目は**同義語の判定**(手順3c)で、`choice` に台帳の
    名前を渡すと「同義語である」と答え、`NO_MATCH` を渡すと同義語なしになる。
    手順3a(動詞の一致)で寄った場合、2回目は呼ばれないので使われない。

    v0.9 では 3c の答えで寄せないので(P33)、3a で寄らなければ必ず3回目(新規候補)が
    呼ばれる。`proposal` を渡さないときは、名前を見ない既定の候補を返す。
    """
    out: list[dict] = [{"candidates": candidates, "rationale": "(固定)"}]
    out.append({"synonym_of": choice, "reason": f"{choice} と同義(固定)"})
    out.append(proposal if proposal is not None else DEFAULT_PROPOSAL)
    return out


def _normalize(ledger, clause, candidates, choice, *, proposal=None, clause_ref="テスト 第1条"):
    llm = ScriptedJsonLlm(_responses(candidates, choice, proposal=proposal))
    normalizer = ActionNormalizer(llm)
    return normalizer.normalize(clause, clause_ref, ledger), llm


# --------------------------------------------------------------------------
# 様相の削り落としと逐語の確認(手順2・P4 v0.4)
# --------------------------------------------------------------------------


def test_modality_words_are_reported_longest_first():
    """「しないこと」を見つけたら、その内側の「ない」は重ねて報告しない。"""
    assert find_modality("使用しないこと") == ["しないこと"]
    assert find_modality("提供してはならない") == ["してはならない"]
    assert find_modality("放置禁止") == ["禁止"]
    assert find_modality("会社の施設、物品等を使用") == []


def test_causative_sase_is_not_modality():
    """P4・前段設計書6.1節: 使役の「させ」は様相ではないので残す。"""
    assert find_modality("労働に従事させ") == []
    assert find_modality("就業させ") == []
    assert find_modality("労働に従事させない") == ["ない"]


@pytest.mark.parametrize(
    "raw,expected,removed",
    [
        ("立ち入らないこと", "立ち入ら", ["ないこと"]),
        ("喫煙可能な場所には立ち入らないこと", "喫煙可能な場所には立ち入ら", ["ないこと"]),
        ("提供してはならない", "提供", ["してはならない"]),
        # タスクB3: 動詞が「し」で終わらない形。足す前は「伝えては」が残っていた
        ("口頭で伝えてはならない", "口頭で伝え", ["てはならない"]),
        ("立ち入ってはいけない", "立ち入っ", ["てはいけない"]),
        ("労働に従事させない", "労働に従事させ", ["ない"]),
        ("私有機器を使用しないこと", "私有機器を使用", ["しないこと"]),
        ("原稿、印刷物を放置しない", "原稿、印刷物を放置", ["しない"]),
        ("会社の施設、物品等を使用", "会社の施設、物品等を使用", []),
    ],
)
def test_strip_modality_removes_the_tail(raw, expected, removed):
    """P4 v0.4: 末尾の様相はプログラムが削る(LLM に語の途中で切らせない)。"""
    assert strip_modality(raw) == (expected, removed)


def test_strip_modality_never_returns_an_empty_surface():
    """語を1つ削ると空になる場合は削らない(空の語句は何も報告できないため)。

    様相の語だけを抜き出してきた場合(「してはならない」)は、途中まで削れて
    「し」のような残りが出る。行為が1つも入っていないので、評価の3条件のうち
    「正解の中心の動詞を含む」で落ちる。**この形はゲートでは捕まらない**
    (条文の部分文字列ではあるため)。限界として記録する。
    """
    assert strip_modality("ない") == ("ない", [])
    assert strip_modality("してはならない") == ("し", ["てはならない"])


def test_parentheses_are_removed_before_the_substring_check():
    """P24: 条文と語句の両方から丸括弧とその中身を取り除いてから照合する。

    第32回の `action_surface_unverified` の1件は、LLM が
    「深夜（午後１０時から午前５時まで）労働」の括弧の中を飛ばしたためだった。
    """
    clause = (
        "第２項による時間外労働又は休日若しくは深夜（午後１０時から午前５時まで）"
        "労働に従事させない。"
    )
    # 括弧の中を飛ばして引いた語句が通る
    check = check_surface("時間外労働又は休日若しくは深夜労働に従事させない", clause)
    assert check.surface == "時間外労働又は休日若しくは深夜労働に従事させ"
    assert check.in_clause is True
    # 括弧を含んだまま正しく引いた語句も通る。IR に入る語句は括弧を消さない
    check = check_surface("深夜（午後１０時から午前５時まで）労働に従事させ", clause)
    assert check.surface == "深夜（午後１０時から午前５時まで）労働に従事させ"
    assert check.in_clause is True
    # 条文に無い語句は、括弧を除いても通らない
    assert check_surface("休日に出勤させ", clause).in_clause is False


def test_strip_parentheses_removes_nested_pairs_and_both_widths():
    """P24: 入れ子は外側ごと除く。全角と半角の両方を対象にする。"""
    assert strip_parentheses("深夜（午後１０時から午前５時まで）労働") == "深夜労働"
    assert strip_parentheses("特権(管理者)権限") == "特権権限"
    assert strip_parentheses("（甲（乙）丙）労働") == "労働"
    # 閉じ括弧が無ければ、開き括弧から末尾まで除く
    assert strip_parentheses("労働（午後１０時から") == "労働"
    assert strip_parentheses("括弧のない語句") == "括弧のない語句"


def test_the_surface_gate_is_only_about_the_substring():
    """4.5節 v0.4: ゲートは「削った語句が条文にない」だけで立つ。

    語句の途中に残った様相(条件の側の否定を含む場合)は報告するが、ゲートにはしない。
    「ない」を様相ではない否定と区別できないためである(modality.py)。
    """
    clause = "営業秘密を許可されていない組織、人に提供してはならない。"
    check = check_surface("許可されていない組織、人に提供してはならない", clause)
    assert check.surface == "許可されていない組織、人に提供"
    assert check.removed_modality == ["してはならない"]
    assert check.in_clause is True
    assert check.ok is True  # ゲートは立たない
    assert check.modality_found == ["ない"]  # 途中に残っていることは報告する


def test_surface_not_in_the_clause_raises_the_surface_gate(seeded_ledger):
    """手順2: 削った語句が条文に無ければ action_surface_unverified。"""
    result, _ = _normalize(
        seeded_ledger, CLAUSE, [{"surface": "会社の設備を利用", "core_verb": "利用"}], NO_MATCH,
        proposal={"name": "設備利用", "definition": "設備を使うこと", "kind": "concrete",
                  "parent": None, "basis_quotes": []},
    )
    assert "action_surface_unverified" in result.gates.gate_names()


def test_the_gold_surfaces_all_pass_the_program_checks(action_gold):
    """正解18件の surface は、条文の部分文字列で様相を含まない(D71・P4)。"""
    for item in action_gold["items"]:
        check = check_surface(item["expected"]["surface"], item["text"])
        assert check.ok, (item["clause_id"], check.reasons())
        assert not check.modality_found, (item["clause_id"], check.modality_found)
        assert item["expected"]["core_verb"] in item["expected"]["surface"]


def test_modality_word_list_covers_the_examples_in_the_design():
    """設計書 D71 が挙げる4つの例が、一覧に入っている。"""
    for word in ("してはならない", "しないこと", "禁止", "ない"):
        assert word in MODALITY_WORDS


def test_the_name_rule_list_excludes_the_plain_negation():
    """P16 の検査は、打ち消しだけの「ない」を見ない(判断。modality.py)。

    「情報や物を、管理されない状態で置いたままにすること」のような定義を止めない
    ため。規範であることが語形に出ているもの(「ないこと」など)は見る。
    """
    assert "ない" not in NORMATIVE_MODALITY_WORDS
    assert "禁止" in NORMATIVE_MODALITY_WORDS
    assert find_normative_modality("情報や物を、管理されない状態で置いたままにすること") == []
    assert find_normative_modality("区域立ち入り禁止") == ["禁止"]
    assert find_normative_modality("立ち入らないこと") == ["ないこと"]


# --------------------------------------------------------------------------
# k=1 と候補(P7・P17)
# --------------------------------------------------------------------------


def test_normalization_runs_once_at_temperature_zero(seeded_ledger):
    """P7 v0.4・P33: k=1。1件あたりの呼び出しは、3a で寄れば1回、そうでなければ
    3回(抜き出し・同義語の提案・新規候補)で、温度はどれも 0.0。"""
    result, llm = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], "送信"
    )
    assert llm.temperatures == [TEMPERATURE, TEMPERATURE, TEMPERATURE]
    assert result.trace.llm_calls == 3
    # 廃止したゲートは名前ごと無くなっている(P7)
    assert "action_mapping_unstable" not in result.gates.gate_names()


def test_two_candidates_raise_the_ambiguous_gate(seeded_ledger):
    """P17: 候補が2つなら action_surface_ambiguous。出力は1つ目を使う。"""
    clause = "特権操作は多要素認証を用い、社有機器からのみ実施し、私有機器を使用しないこと。"
    result, _ = _normalize(
        seeded_ledger,
        clause,
        [
            {"surface": "特権操作", "core_verb": "操作"},
            {"surface": "私有機器を使用しないこと", "core_verb": "使用"},
        ],
        NO_MATCH,
        proposal={"name": "操作", "definition": "特権/管理者権限で情報システムを操作すること",
                  "kind": "concrete", "parent": None, "basis_quotes": ["特権操作"]},
    )
    assert "action_surface_ambiguous" in result.gates.gate_names()
    assert result.action.surface == "特権操作"
    # 2つとも Trace に残す(どちらが規制の対象かは人が見る)
    assert [c["surface"] for c in result.trace.run.to_json()["candidates"]] == [
        "特権操作",
        "私有機器を使用",
    ]


def test_a_single_candidate_does_not_raise_the_ambiguous_gate(seeded_ledger):
    result, _ = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], "送信"
    )
    assert "action_surface_ambiguous" not in result.gates.gate_names()


# --------------------------------------------------------------------------
# 寄せ方を動詞の規則にする(手順3a・3b・3c。P27〜P30。第38回パートA)
# --------------------------------------------------------------------------


def test_a_single_verb_match_merges_without_asking_the_llm(seeded_ledger):
    """手順3a(P27): 動詞が1つの Action と一致したら、LLM に聞かずに寄せる。

    LLM の呼び出しは抜き出しの1回だけになる(4.7節)。
    """
    seeded_ledger.record_surface("送信", "情報を送信", "あおいの既存DMN", "送信")
    result, llm = _normalize(
        seeded_ledger,
        "極秘の情報を社外に送信してはならない。",
        [{"surface": "社外に送信", "core_verb": "送信"}],
        NO_MATCH,  # 同義語の判定は呼ばれないので使われない
    )
    assert result.action.normalized == "送信"
    assert result.action.is_new is False
    assert result.trace.llm_calls == 1
    assert len(llm.prompts) == 1
    assert result.trace.run.route == "3a"
    assert result.trace.verb_routing.matched_names == ["送信"]
    # 動詞で寄せたので、同義語のゲートは立たない
    assert "action_synonym_merge" not in result.gates.gate_names()


def test_two_verb_matches_go_to_a_human(seeded_ledger):
    """手順3a(P27): 一致が2つ以上なら人に回す(どちらの行為かは人の判断)。"""
    seeded_ledger.record_surface("送信", "情報を送信", "あおいの既存DMN", "送信")
    seeded_ledger.add(
        ActionEntry(name="転送", definition="受け取った情報を別の宛先へ送ること")
    )
    seeded_ledger.record_surface("転送", "メールを転送", "テスト 第2条", "送信")
    result, _ = _normalize(
        seeded_ledger,
        "極秘の情報を社外に送信してはならない。",
        [{"surface": "社外に送信", "core_verb": "送信"}],
        NO_MATCH,
    )
    assert result.trace.verb_routing.matched_names == ["送信", "転送"]
    assert result.trace.verb_routing.ambiguous is True
    assert "action_surface_ambiguous" in result.gates.gate_names()
    # 1つ目(宣言順)に寄せたうえで人に回す
    assert result.action.normalized == "送信"


def test_a_synonym_answer_is_only_a_suggestion_and_never_merges(seeded_ledger):
    """P33: 3c が既存の名前を返しても寄せず、新規候補にして提案として残す。"""
    seeded_ledger.record_surface("送信", "情報を送信", "あおいの既存DMN", "送信")
    proposal = {"name": "転送", "definition": "情報を別の宛先に送ること",
                "kind": "concrete", "parent": None, "basis_quotes": []}
    result, llm = _normalize(
        seeded_ledger,
        "極秘の情報を社外に転送してはならない。",
        [{"surface": "社外に転送", "core_verb": "転送"}],
        "送信",
        proposal=proposal,
    )
    # 出力は新規候補。既存の「送信」には寄せない
    assert result.action.normalized == "転送"
    assert result.action.is_new is True
    assert result.trace.run.route == "4"
    # 提案は Trace に残り、設計上の確認のゲートが立つ
    assert result.trace.synonym_judgment.answer == "送信"
    assert "action_synonym_suggested" in result.gates.gate_names()
    assert "action_synonym_merge" not in result.gates.gate_names()
    assert "P33" in result.trace.run.reason
    assert result.trace.llm_calls == 3
    assert len(llm.prompts) == 3


def test_no_synonym_means_no_suggestion_gate(seeded_ledger):
    """P33: 3c が「該当なし」なら、提案のゲートは立たない。"""
    seeded_ledger.record_surface("送信", "情報を送信", "あおいの既存DMN", "送信")
    proposal = {"name": "転送", "definition": "情報を別の宛先に送ること",
                "kind": "concrete", "parent": None, "basis_quotes": []}
    result, _ = _normalize(
        seeded_ledger,
        "極秘の情報を社外に転送してはならない。",
        [{"surface": "社外に転送", "core_verb": "転送"}],
        NO_MATCH,
        proposal=proposal,
    )
    assert result.action.is_new is True
    assert result.trace.synonym_judgment.answer is None
    assert "action_synonym_suggested" not in result.gates.gate_names()


def test_the_retired_synonym_merge_gate_is_counted_as_a_design_check(seeded_ledger):
    """P33: `action_synonym_merge` は廃止したが、過去の生データを数え直すときは
    当時の区分(設計上の確認)で数える。"""
    from src.stage3_generate.ir_schema import (
        DESIGN_CHECK_GATES,
        RETIRED_GATES,
        gate_kind,
    )

    assert "action_synonym_merge" not in DESIGN_CHECK_GATES
    assert "action_synonym_merge" in RETIRED_GATES
    assert gate_kind("action_synonym_merge") == "design_check"
    assert "action_synonym_suggested" in DESIGN_CHECK_GATES
    assert gate_kind("action_synonym_suggested") == "design_check"
    # 立つことはない
    seeded_ledger.record_surface("送信", "情報を送信", "あおいの既存DMN", "送信")
    result, _ = _normalize(
        seeded_ledger,
        "極秘の情報を社外に転送してはならない。",
        [{"surface": "社外に転送", "core_verb": "転送"}],
        "送信",
    )
    assert "action_synonym_merge" not in result.gates.gate_names()


def test_the_retired_verb_mismatch_gate_is_gone(seeded_ledger):
    """P30: `action_verb_mismatch` は廃止した(ゲートの一覧に無い)。"""
    from src.stage3_generate.ir_schema import (
        RETIRED_GATES,
        SUSPICION_GATES,
        gate_kind,
    )

    assert "action_verb_mismatch" not in SUSPICION_GATES
    assert "action_verb_mismatch" in RETIRED_GATES
    # 過去の記録を読み直すときは、当時の種類(疑い)で数える
    assert gate_kind("action_verb_mismatch") == "suspicion"
    seeded_ledger.record_surface("送信", "情報を送信", "あおいの既存DMN", "送信")
    result, _ = _normalize(
        seeded_ledger,
        "極秘の情報を社外に転送してはならない。",
        [{"surface": "社外に転送", "core_verb": "転送"}],
        "送信",
    )
    assert "action_verb_mismatch" not in result.gates.gate_names()


def test_verb_match_allows_an_okurigana_difference():
    """判断: 完全一致か、どちらかがもう片方を含むことを一致とする。"""
    assert core_verbs_match("立ち入る", ["立ち入"])[0] is True
    assert core_verbs_match("使用", ["私有機器を使用"])[0] is True
    assert core_verbs_match("伝達", ["提供"])[0] is False
    # 比べる相手の動詞が無い Action には寄せられない
    assert core_verbs_match("提供", [])[0] is False


def test_a_causative_difference_is_not_a_match():
    """P28: 使役(「させ」)の有無だけが違う組は一致としない。"""
    assert core_verbs_match("就業させる", ["就業"])[0] is False
    assert core_verbs_match("従事させ", ["従事"])[0] is False
    assert core_verbs_match("従事", ["労働に従事させ"])[0] is False
    # 使役どうし・使役でないどうしは、送り仮名の差を一致とする
    assert core_verbs_match("就業させ", ["就業させる"])[0] is True
    assert core_verbs_match("就業", ["酒気を帯びて就業"])[0] is True


def test_a_causative_clause_is_not_merged_into_the_plain_verb(seeded_ledger):
    """P28: 「就業させる」の条文は、台帳の「就業」に動詞では寄らない。"""
    seeded_ledger.add(ActionEntry(name="就業", definition="業務に就くこと"))
    seeded_ledger.record_surface("就業", "酒気を帯びて就業", "厚労 第11条", "就業")
    result, _ = _normalize(
        seeded_ledger,
        "酒気を帯びた者を就業させてはならない。",
        [{"surface": "就業させ", "core_verb": "就業させ"}],
        NO_MATCH,
        proposal={"name": "就業指示", "definition": "人を業務に就かせること",
                  "kind": "concrete", "parent": None, "basis_quotes": ["就業させ"]},
    )
    assert result.trace.verb_routing.matched_names == []
    assert result.action.is_new is True


def test_the_extraction_check_flags_another_ledger_verb_in_the_clause(seeded_ledger):
    """手順3b(P29): 抜き出した動詞のほかに台帳の動詞が条文に現れたら人に回す。

    開発用の項目6で「使用」を抜き出したとき、条文に台帳の動詞「操作」がある。
    """
    seeded_ledger.add(ActionEntry(name="操作", definition="情報システムや機器を動かすこと"))
    seeded_ledger.record_surface("操作", "特権操作", "ipa 6.7第3項", "操作")
    clause = (
        "特権操作は多要素認証を用い、社有機器及び指定経路（VPN等）からのみ実施し、"
        "私有機器を使用しないこと。"
    )
    result, _ = _normalize(
        seeded_ledger,
        clause,
        [{"surface": "私有機器を使用", "core_verb": "使用"}],
        NO_MATCH,
        proposal={"name": "使用", "definition": "物・設備・サービスを使うこと",
                  "kind": "concrete", "parent": None, "basis_quotes": ["私有機器を使用"]},
    )
    hits = result.trace.extraction_check.hits
    assert [h["core_verb"] for h in hits] == ["操作"]
    assert "action_surface_ambiguous" in result.gates.gate_names()


def test_the_extraction_check_ignores_the_extracted_verb_itself(seeded_ledger):
    """手順3b: 抜き出した動詞そのもの(送り仮名の差を含む)は数えない。"""
    seeded_ledger.record_surface("送信", "情報を送信", "あおいの既存DMN", "送信")
    result, _ = _normalize(
        seeded_ledger,
        "極秘の情報を社外に送信してはならない。",
        [{"surface": "社外に送信", "core_verb": "送信"}],
        NO_MATCH,
    )
    assert result.trace.extraction_check.hits == []


def test_the_verb_routing_is_recorded_even_for_a_new_action(seeded_ledger):
    """新規候補のときも、動詞を比べた記録は残す(どの動詞と比べたか)。"""
    result, _ = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], NO_MATCH,
        proposal={"name": "使用", "definition": "会社の施設や物品を使うこと",
                  "kind": "concrete", "parent": None, "basis_quotes": [SURFACE]},
    )
    assert result.trace.verb_routing.matched_names == []
    assert result.trace.run.route == "4"
    assert "action_synonym_merge" not in result.gates.gate_names()


def test_abstract_selected_raises_the_gate(seeded_ledger):
    """P15・P31: 抽象の段に寄るのは 3a で動詞が一致したときだけ。ゲートは立つ。

    v0.8 で 3c の候補から抽象の段を外したので(P31)、抽象に寄る道は手順3a だけに
    なった。人が同義語を承認して台帳の過去の語句に足した動詞は、次から 3a で寄る
    (P30)。ここでは「漏洩」が承認済みとして入っている状態を作る。
    """
    seeded_ledger.add(
        ActionEntry(
            name="提供",
            definition="手段を問わず、情報を他の組織や人に渡すこと",
            kind="abstract",
        )
    )
    seeded_ledger.record_surface("提供", "機密を漏洩", "第11条⑤", "漏洩")
    result, llm = _normalize(
        seeded_ledger,
        "機密を漏洩しないこと。",
        [{"surface": "漏洩しないこと", "core_verb": "漏洩"}],
        NO_MATCH,
    )
    assert result.trace.run.route == "3a"
    assert len(llm.prompts) == 1  # 3a で寄ったので LLM は抜き出しの1回だけ
    assert "action_abstract_selected" in result.gates.gate_names()
    assert result.chosen_kind == "abstract"


def test_the_synonym_candidates_include_the_abstract_actions_again(seeded_ledger):
    """P33(P31 の廃止): 3c に渡す候補は台帳の全段に戻した。寄せはしないので、
    提案は広い候補から選べたほうがよい。"""
    seeded_ledger.add(
        ActionEntry(
            name="提供",
            definition="手段を問わず、情報を他の組織や人に渡すこと",
            kind="abstract",
        )
    )
    seeded_ledger.record_surface("提供", "組織、人に提供", "人的対策-2", "提供")
    proposal = {"name": "漏洩", "definition": "機密が外に出ること",
                "kind": "concrete", "parent": None, "basis_quotes": []}
    result, llm = _normalize(
        seeded_ledger,
        "機密を漏洩しないこと。",
        [{"surface": "漏洩しないこと", "core_verb": "漏洩"}],
        NO_MATCH,
        proposal=proposal,
    )
    synonym_prompt = llm.prompts[1]
    assert "提供（これまでの動詞" in synonym_prompt  # 抽象の段も渡す(P31 の廃止)
    assert "送信（これまでの動詞" in synonym_prompt
    assert result.trace.synonym_judgment is not None
    assert "提供" in result.trace.synonym_judgment.offered
    assert result.action.is_new is True


def test_an_abstract_suggestion_is_kept_as_a_suggestion_and_never_merged(
    seeded_ledger,
):
    """P33: 抽象の段の名前が返っても、寄せずに提案として残す(第37回・第39回の
    「提供」への誤統合は、ここで前段の出力から外れる)。"""
    seeded_ledger.add(
        ActionEntry(
            name="提供",
            definition="手段を問わず、情報を他の組織や人に渡すこと",
            kind="abstract",
        )
    )
    seeded_ledger.record_surface("提供", "組織、人に提供", "人的対策-2", "提供")
    proposal = {"name": "漏洩", "definition": "機密が外に出ること",
                "kind": "concrete", "parent": None, "basis_quotes": []}
    result, _ = _normalize(
        seeded_ledger,
        "機密を漏洩しないこと。",
        [{"surface": "漏洩しないこと", "core_verb": "漏洩"}],
        "提供",  # 渡していない抽象の段の名前を返してきた
        proposal=proposal,
    )
    # 候補に入っているので「渡していない名前」ではない(P31 を廃止した)
    assert result.trace.synonym_judgment.off_list is False
    assert result.trace.synonym_judgment.answer == "提供"
    assert result.trace.run.route == "4"
    assert result.action.is_new is True
    assert result.action.normalized == "漏洩"
    assert "action_synonym_suggested" in result.gates.gate_names()
    # 抽象に寄せていないので、抽象の段のゲートは立たない
    assert "action_abstract_selected" not in result.gates.gate_names()


# --------------------------------------------------------------------------
# 新規候補(手順5・P16)
# --------------------------------------------------------------------------


def test_no_match_proposes_a_new_action_with_a_kind_and_never_writes_the_parent(
    seeded_ledger,
):
    """P6: 親の案は Trace にだけ置く。段(kind)の案も出させる(P14)。"""
    proposal = {
        "name": "伝達",
        "definition": "情報を、声で相手に伝えること",
        "kind": "concrete",
        "parent": "提供",
        "basis_quotes": ["口頭で伝達"],
    }
    result, _ = _normalize(
        seeded_ledger,
        "医療情報を口頭で伝達してはならない。",
        [{"surface": "口頭で伝達してはならない", "core_verb": "伝達"}],
        NO_MATCH,
        proposal=proposal,
    )
    assert result.action.is_new is True
    assert result.action.normalized == "伝達"
    # IR に parent の欄そのものが無い(P6・D78。v1.10 で廃止)
    assert not hasattr(result.action, "parent")
    assert result.action.approved_by is None  # 承認は人(D53)
    assert result.trace.new_action_proposal.parent_suggestion == "提供"
    assert result.trace.new_action_proposal.kind == "concrete"
    assert result.trace.new_action_proposal.basis_verified == [True]
    assert "new_action_unapproved" in result.gates.gate_names()


def test_a_prohibition_in_the_name_raises_the_name_rule_gate(seeded_ledger):
    """P16: 名前・定義に規範の様相があれば action_name_rule。"""
    proposal = {
        "name": "区域立ち入り禁止",
        "definition": "立入禁止区域には立ち入らないこと",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": [],
    }
    result, _ = _normalize(
        seeded_ledger,
        "立入禁止区域には立ち入らないこと。",
        [{"surface": "立ち入らないこと", "core_verb": "立ち入ら"}],
        NO_MATCH,
        proposal=proposal,
    )
    assert "action_name_rule" in result.gates.gate_names()
    assert result.trace.new_action_proposal.name_modality == ["禁止"]
    assert result.trace.new_action_proposal.definition_modality == ["ないこと", "禁止"]


def test_a_definition_that_uses_a_negation_does_not_raise_the_name_rule_gate(
    seeded_ledger,
):
    """判断: 打ち消しだけの否定で止めない(「管理されない状態で」は正しい定義)。"""
    proposal = {
        "name": "放置",
        "definition": "情報や物を、管理されない状態で置いたままにすること",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": ["原稿、印刷物を放置"],
    }
    result, _ = _normalize(
        seeded_ledger,
        "複合機、プリンタに原稿、印刷物を放置しない。",
        [{"surface": "原稿、印刷物を放置しない", "core_verb": "放置"}],
        NO_MATCH,
        proposal=proposal,
    )
    assert "action_name_rule" not in result.gates.gate_names()


def test_a_name_outside_the_ledger_is_treated_as_no_match(seeded_ledger):
    """P5: 閉じた選択。台帳にない名前が返ってきたら「該当なし」として扱う。"""
    proposal = {
        "name": "情報開示",
        "definition": "情報を開示すること",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": [],
    }
    result, _ = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}],
        "台帳に無い名前", proposal=proposal,
    )
    assert result.action.is_new is True
    assert "候補に渡していない名前" in result.trace.run.reason
    assert "台帳の外" in result.trace.run.reason


# --------------------------------------------------------------------------
# プロンプトに渡すもの(P11・P14)
# --------------------------------------------------------------------------


def test_the_ledger_block_shows_the_kind_the_parent_and_the_core_verbs(seeded_ledger):
    seeded_ledger.add(
        ActionEntry(
            name="提供",
            definition="手段を問わず、情報を他の組織や人に渡すこと",
            kind="abstract",
        )
    )
    seeded_ledger.find("送信").parent = "提供"
    seeded_ledger.find("送信").excludes = ["伝達"]
    seeded_ledger.record_surface("提供", "組織、人に提供", "人的対策-2", "提供")
    block = seeded_ledger.prompt_block()
    assert "抽象的な行為(手段を問わない)" in block
    assert "親(より抽象的な行為): 提供" in block
    assert "この Action に含まないもの: 伝達" in block
    assert "中心の動詞: 提供" in block


def test_the_synonym_prompt_shows_the_verbs_but_never_the_definitions(seeded_ledger):
    """手順3c(W1): 名前と過去の動詞だけを渡し、**定義は渡さない**。

    第37回の誤統合8件は、定義を読んで「一番近い既存」を選んだ結果だった(7.4節)。
    """
    seeded_ledger.record_surface("送信", "情報を送信", "あおいの既存DMN", "送信")
    _result, llm = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], NO_MATCH,
        proposal={"name": "使用", "definition": "物・設備・サービスを使うこと",
                  "kind": "concrete", "parent": None, "basis_quotes": [SURFACE]},
    )
    synonym_prompt = llm.prompts[1]
    assert "同じ行為を表す同義語" in synonym_prompt
    assert "送信（これまでの動詞: 送信）" in synonym_prompt
    # 定義も条文本文も渡さない
    assert SEED_DEFINITION not in synonym_prompt
    assert CLAUSE not in synonym_prompt
    # 向き・使役・上位下位は同義語ではないことを示す(P27)
    assert "向きが違う動詞は同義語ではありません" in synonym_prompt
    assert "使役(させる)の有無が違えば別の行為です" in synonym_prompt
    assert "上位・下位の関係は同義語ではありません" in synonym_prompt


def test_the_surface_prompt_warns_about_the_sentence_final_predicate(seeded_ledger):
    """P17: 「〜は」「〜が」の直後の体言が行為であることを示す。"""
    _result, llm = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], "送信"
    )
    surface_prompt = llm.prompts[0]
    assert "文末の述語だけを見ないこと" in surface_prompt
    assert "候補を**2つまで**挙げてよい" in surface_prompt
    assert "そのまま付けたままでよい" in surface_prompt  # 様相はプログラムが削る


def test_the_proposal_prompt_forbids_modality_and_conditions(seeded_ledger):
    """P16・4.6節: 名前と定義に様相・条件を入れないことをプロンプトで示す。"""
    proposal = {"name": "立入り", "definition": "区域や場所に入ること", "kind": "concrete",
                "parent": None, "basis_quotes": []}
    _result, llm = _normalize(
        seeded_ledger, "立入禁止区域には立ち入らないこと。",
        [{"surface": "立ち入らないこと", "core_verb": "立ち入ら"}], NO_MATCH,
        proposal=proposal,
    )
    prompt = llm.prompts[2]
    assert "規範の様相(禁止・不可・不許可など)を名前と定義に入れない" in prompt
    assert "条件(誰が・どこで・いつ・どの区分か)と対象・手段を名前と定義に入れない" in prompt
    assert "一般的な言い換えや" in prompt
    # P26: 名前は行為の種類(動詞)だけ。対象・権限・場所・宛先・副詞の手段は列
    assert "名前は行為の種類(動詞)だけにする" in prompt
    assert "対象(何を)・権限・場所・宛先・副詞で" in prompt
    assert "使役(させる)は動詞の" in prompt


def test_the_synonym_prompt_gives_examples_and_keeps_the_object_out(seeded_ledger):
    """P27: 同義語の例を示し、対象・権限・場所が違っても動詞が同じなら同義語とする。"""
    _result, llm = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], NO_MATCH,
        proposal={"name": "使用", "definition": "使うこと", "kind": "concrete",
                  "parent": None, "basis_quotes": []},
    )
    synonym_prompt = llm.prompts[1]
    assert "「利用」と「使用」" in synonym_prompt
    assert "「ダウンロード」と「取り出し」" in synonym_prompt
    assert "行為の種類\n   (動詞)が同じなら同義語です" in synonym_prompt
    # 迷ったら寄せない(閉じた選択の「該当なし」)
    assert f"迷ったときは「{NO_MATCH}」を選んでください" in synonym_prompt


def test_context_is_offered_but_not_as_the_source_of_the_surface(seeded_ledger):
    """IR v2 1.4節: context は補足で、逐語で抜き出す対象は条文本文だけ。"""
    context = [{"text": "特権アカウント", "where": "6.10 の表の列の見出し"}]
    llm = ScriptedJsonLlm(
        _responses([{"surface": SURFACE, "core_verb": "使用"}], "送信")
    )
    ActionNormalizer(llm).normalize(CLAUSE, "テスト 第1条", seeded_ledger, context=context)
    assert "特権アカウント" in llm.prompts[0]
    assert "行為の語句はここからではなく、条文本文から抜き出すこと" in llm.prompts[0]


# --------------------------------------------------------------------------
# 近さの判定(P8)
# --------------------------------------------------------------------------


def test_duplicate_check_flags_a_shared_head_noun(seeded_ledger):
    """P8: 末尾の体言が同じ新規候補は、既存に寄せるべきかを人に見せる。"""
    check = seeded_ledger.duplicate_check(
        "データ送信", "データを、電子メールなどで宛先に電子的に送ること"
    )
    assert check.flagged
    assert check.closest == "送信"
    assert name_ratio("データ送信", "外部送信") < NAME_RATIO_THRESHOLD
    assert shared_suffix_length("データ送信", "外部送信") >= SHARED_SUFFIX_THRESHOLD


def test_duplicate_check_does_not_flag_an_unrelated_action(seeded_ledger):
    """別の行為は拾わない(拾いすぎるとゲートが意味を失う)。"""
    check = seeded_ledger.duplicate_check(
        "アカウント共用", "1つのアカウントを、2人以上で使うこと"
    )
    assert not check.flagged
    assert definition_overlap("1つのアカウントを、2人以上で使うこと", SEED_DEFINITION) < 0.3


def test_duplicate_gate_is_raised_for_a_close_new_candidate(seeded_ledger):
    """P8: 近い新規候補には action_possible_duplicate も立つ。"""
    proposal = {
        "name": "データ送信",
        "definition": "データを、電子メールなどで宛先に電子的に送ること(転送を含む)",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": [],
    }
    clause = "社用メールアドレスで受信したメールを従業員個人のアドレスに転送することを禁止する。"
    result, _ = _normalize(
        seeded_ledger, clause,
        [{"surface": "従業員個人のアドレスに転送", "core_verb": "転送"}], NO_MATCH,
        proposal=proposal,
    )
    gates = result.gates.gate_names()
    assert "action_possible_duplicate" in gates
    assert "new_action_unapproved" in gates


# --------------------------------------------------------------------------
# 教師ありの台帳の更新(P13)
# --------------------------------------------------------------------------


def test_the_ledger_start_comes_from_the_initial_registry(action_gold):
    """P10: 台帳の出発点は初期登録の登録簿にある Action(外部送信)だけ。"""
    ledger = load_ledger(action_gold, REGISTRY_PATH)
    assert ledger.names() == ["送信"]
    entry = ledger.entries[0]
    # P18: 承認の状態は名前と定義で分かれている
    assert entry.name_status == "provisional"
    assert entry.definition_status == "provisional"
    # タスクA2: 初期登録では親を付けない(評価の中で付く)
    assert entry.kind == "concrete"
    assert entry.parent is None
    registry = Registry.load(REGISTRY_PATH)
    assert registry.find_action("送信") is not None
    assert registry.action_kind("送信") == "concrete"


def test_teaching_adds_the_expected_action_with_its_kind_and_parent(
    action_gold, seeded_ledger
):
    """P13・4.2節: 台帳に足すのは正解の Action で、段と親も正解どおり。"""
    item = action_gold["items"][4]  # 伝達(親は提供)
    message = teach(seeded_ledger, item)
    assert "台帳に追加" in message
    entry = seeded_ledger.find("伝達")
    assert entry is not None
    assert entry.kind == "concrete"
    assert entry.parent == "提供"
    assert entry.name_status == "approved"
    assert entry.definition_status == "provisional"
    assert entry.approved_by == "Kazuhiro"
    # 過去の語句には中心の動詞も入れる(動詞の一致の検査に使う。P15)
    assert entry.surfaces == [
        {
            "text": item["expected"]["surface"],
            "clause_ref": item["clause_ref"],
            "core_verb": item["expected"]["core_verb"],
        }
    ]


def test_teaching_applies_the_ledger_updates(action_gold, seeded_ledger):
    """4.1節: 項目3で提供が台帳に入った時点で、外部送信に親が付く。"""
    item = action_gold["items"][2]  # 人的対策-2(提供・abstract)
    assert seeded_ledger.find("送信").parent is None
    message = teach(seeded_ledger, item)
    assert seeded_ledger.find("提供").kind == "abstract"
    assert seeded_ledger.find("送信").parent == "提供"
    assert "親を付けた" in message
    assert seeded_ledger.relation("提供", "送信") == "descendant"


# --------------------------------------------------------------------------
# 誤りの4分類(5.1節・P9)
# --------------------------------------------------------------------------


@pytest.fixture
def taught_ledger(action_gold, seeded_ledger) -> ActionLedger:
    """項目5まで教えた台帳(提供・その子・操作などが入っている)。"""
    for item in action_gold["items"][:5]:
        teach(seeded_ledger, item)
    return seeded_ledger


def test_mis_merge_is_an_unrelated_existing_action(action_gold, taught_ledger):
    """誤統合: 親子関係にない既存の Action に寄せた(重大)。"""
    item = action_gold["items"][14]  # 物理3(正解は新規の「放置」)
    # v0.9: 3c の答えでは寄せない(P33)ので、既存に寄るのは手順3a だけ。人が
    # 「放置」を「使用」の同義語として承認し、過去の語句に足した状態を作る(P30)
    taught_ledger.record_surface("使用", "原稿、印刷物を放置", item["clause_ref"], "放置")
    result, _ = _normalize(
        taught_ledger, item["text"],
        [{"surface": "原稿、印刷物を放置しない", "core_verb": "放置"}], "使用",
        clause_ref=item["clause_ref"],
    )
    assert classify(item, result, taught_ledger) == "mis_merge"
    verdict = judge(item, result, taught_ledger)
    assert verdict.severe is True
    assert verdict.route == "3a"
    assert "action_synonym_merge" not in verdict.gates
    # v0.9 で誤統合が起きる道は手順3a だけになった。3a の寄せにはゲートが立たない
    # ので、この1件はすり抜ける。ただしそこへ至るには、人が誤った同義語を承認して
    # 動詞を過去の語句に足している必要がある(P30)
    assert verdict.slipped is True


def test_under_applied_is_choosing_a_descendant_of_the_right_abstract_action(
    action_gold, seeded_ledger
):
    """適用漏れ: 正解は抽象的な Action なのに、その子孫を選んだ(重大)。"""
    item = action_gold["items"][2]  # 人的対策-2(正解は抽象の「提供」)
    # v0.9: 既存に寄るのは手順3a だけ(P33)。人が「提供」を「送信」の同義語として
    # 誤って承認した状態を作る(P30)
    seeded_ledger.record_surface("送信", "組織、人に提供", item["clause_ref"], "提供")
    result, _ = _normalize(
        seeded_ledger, item["text"],
        [{"surface": "組織、人に提供してはならない", "core_verb": "提供"}], "送信",
        clause_ref=item["clause_ref"],
    )
    teach(seeded_ledger, item)  # 正解を足してから分類する(4.2節)
    assert classify(item, result, seeded_ledger) == "under_applied"
    assert judge(item, result, seeded_ledger).severe is True


def test_over_applied_is_choosing_the_parent_of_the_right_concrete_action(
    action_gold, seeded_ledger
):
    """過剰適用: 正解は具体的な Action なのに、その祖先を選んだ(中)。"""
    for item in action_gold["items"][:3]:
        teach(seeded_ledger, item)
    item = action_gold["items"][4]  # 第14条(正解は具体の「伝達」)
    # v0.8: 3c の候補から抽象は外れたので(P31)、抽象に寄るのは 3a で動詞が
    # 一致したときだけ。人が「伝達」を同義語として承認し、提供の過去の語句に
    # 足した状態を作る(P30)
    seeded_ledger.record_surface("提供", "口頭で伝達", item["clause_ref"], "伝達")
    result, _ = _normalize(
        seeded_ledger, item["text"],
        [{"surface": "口頭で伝達してはならない", "core_verb": "伝達"}], NO_MATCH,
        clause_ref=item["clause_ref"],
    )
    teach(seeded_ledger, item)
    assert classify(item, result, seeded_ledger) == "over_applied"
    verdict = judge(item, result, seeded_ledger)
    assert verdict.severe is False  # 安全側に倒れるので重大ではない
    assert "action_abstract_selected" in verdict.gates


def test_missing_an_existing_action_is_counted_as_a_miss(action_gold, taught_ledger):
    """取りこぼし: 正解は既存なのに新規にした(軽。ゲートで人が拾える)。"""
    item = action_gold["items"][5]  # 6.1⑤(正解は既存の「操作」。旧: 特権操作)
    proposal = {
        "name": "私有機器使用",
        "definition": "私有機器を使うこと",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": [],
    }
    # v0.7: 台帳にある動詞(操作・使用)を抜き出すと手順3a で寄ってしまうので、
    # 台帳に無い動詞(「実施」)を抜き出した場合で確かめる
    result, _ = _normalize(
        taught_ledger, item["text"],
        [{"surface": "社有機器及び指定経路（VPN等）からのみ実施", "core_verb": "実施"}],
        NO_MATCH,
        proposal=proposal, clause_ref=item["clause_ref"],
    )
    verdict = judge(item, result, taught_ledger)
    assert verdict.verdict == "missed"
    assert verdict.severe is False
    assert verdict.route == "4"
    assert "new_action_unapproved" in verdict.gates


def test_a_missed_item_with_a_right_suggestion_can_be_rescued(
    action_gold, taught_ledger
):
    """P33 の指標: 取りこぼしのうち、提案が正解と一致していた件は人が救える。"""
    item = action_gold["items"][5]  # 6.1⑤(正解は既存の「操作」)
    proposal = {
        "name": "私有機器使用",
        "definition": "私有機器を使うこと",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": [],
    }
    result, _ = _normalize(
        taught_ledger, item["text"],
        [{"surface": "社有機器及び指定経路（VPN等）からのみ実施", "core_verb": "実施"}],
        "操作",  # 3c は正解の既存を提案した
        proposal=proposal, clause_ref=item["clause_ref"],
    )
    verdict = judge(item, result, taught_ledger)
    # 寄せはしないので、出力は取りこぼしのまま
    assert verdict.verdict == "missed"
    assert verdict.is_new is True
    assert verdict.synonym_suggestion == "操作"
    assert verdict.suggestion_in_scope is True
    assert verdict.suggestion_hit is True
    assert verdict.suggestion_rescues_missed is True
    assert verdict.suggestion_false_alarm is False
    assert "action_synonym_suggested" in verdict.gates


def test_a_suggestion_on_a_new_action_is_a_false_alarm(action_gold, taught_ledger):
    """P33 の指標: 正解が新規なのに提案が出たら、提案の誤報として数える。"""
    item = action_gold["items"][14]  # 物理3(正解は新規の「放置」)
    proposal = {"name": "放置", "definition": "置いたままにすること",
                "kind": "concrete", "parent": None, "basis_quotes": []}
    result, _ = _normalize(
        taught_ledger, item["text"],
        [{"surface": "原稿、印刷物を放置しない", "core_verb": "放置"}],
        "使用",  # 3c が誤って既存を提案した
        proposal=proposal, clause_ref=item["clause_ref"],
    )
    verdict = judge(item, result, taught_ledger)
    assert verdict.verdict == "correct"  # 出力そのものは正しい(新規の「放置」)
    assert verdict.suggestion_false_alarm is True
    assert verdict.suggestion_in_scope is False  # 正解が新規なので分母に入らない
    assert verdict.suggestion_hit is False
    # 設計上の確認なので、正しい結果でもゲートの誤報には数えない(P21・W2)
    assert "action_synonym_suggested" in verdict.gates
    assert "action_synonym_suggested" not in verdict.false_alarm_gates


def test_choosing_the_right_existing_action_is_correct(action_gold, taught_ledger):
    """既存に正しく寄せた場合は、どの誤りにもならない。"""
    item = action_gold["items"][5]
    result, _ = _normalize(
        taught_ledger, item["text"], [{"surface": "特権操作", "core_verb": "操作"}],
        "操作", clause_ref=item["clause_ref"],
    )
    verdict = judge(item, result, taught_ledger)
    assert verdict.verdict == "correct"
    assert verdict.surface_ok is True
    # 正しい結果なのに立ったゲートは「誤報」として数える(new_action_unapproved は除く)
    assert verdict.false_alarm_gates == verdict.gates


def test_the_new_action_gate_is_not_counted_as_a_false_alarm(action_gold, seeded_ledger):
    """5.1節: 新しい Action の承認は設計どおりの動きなので、誤報に数えない。"""
    item = action_gold["items"][0]
    proposal = {
        "name": "操作",
        "definition": "特権/管理者権限で情報システムを操作すること",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": ["特権/管理者権限で操作を行う"],
    }
    result, _ = _normalize(
        seeded_ledger, item["text"],
        [{"surface": "特権/管理者権限で操作を行う", "core_verb": "操作"}], NO_MATCH,
        proposal=proposal, clause_ref=item["clause_ref"],
    )
    teach(seeded_ledger, item)
    verdict = judge(item, result, seeded_ledger)
    assert verdict.verdict == "correct"
    assert verdict.gates == ["new_action_unapproved"]
    assert verdict.false_alarm_gates == []


# --------------------------------------------------------------------------
# 正解ファイルの形(パートC)
# --------------------------------------------------------------------------


def test_action_gold_v3_has_18_items_in_evaluation_order(action_gold):
    """P13: 正解IR 5件 ＋ 候補13件を、評価の順に並べる。"""
    items = action_gold["items"]
    assert len(items) == 18
    assert [i["order"] for i in items] == list(range(1, 19))
    assert sum(1 for i in items if i["group"] == "正解IR") == 5
    assert len(action_gold["ledger_seed"]) == 1
    for item in items:
        assert item["review"]["approved_by"] == "Kazuhiro"
        assert item["expected"]["kind"] in ("concrete", "abstract")
        if item["expected"]["is_new"]:
            assert item["definition"]["text"], item["clause_id"]
            assert item["definition"]["basis"], item["clause_id"]


def test_action_gold_v3_hierarchy_matches_the_design(action_gold):
    """前段設計書 v0.6 の3.4節の表どおりの名前・段・親になっていること(D82)。"""
    by_name = {}
    for item in action_gold["items"]:
        expected = item["expected"]
        by_name.setdefault(expected["normalized"], expected)
    assert by_name["提供"]["kind"] == "abstract"
    for child in ("伝達", "送信", "共有"):
        assert by_name[child]["parent"] == "提供", child
    # 取り出しは向きが違うので提供の子にしない(3.4節)
    assert by_name["取り出し"]["parent"] is None
    # 旧い名前は1つも残っていない(D82 の対応表)
    old = {"特権操作", "施設物品使用", "情報提供", "口頭伝達", "アカウント共用",
           "データ取り出し", "外部送信", "データ送信"}
    assert not old & set(by_name)
    # 項目3で、初期登録の送信に親が付く
    updates = action_gold["items"][2]["ledger_updates"]
    assert [(u["name"], u["parent"]) for u in updates] == [("送信", "提供")]


def test_action_gold_v3_merges_the_two_sendings(action_gold):
    """D80・D82: 外部送信とデータ送信を「送信」にまとめたので、項目9は既存になる。"""
    item9 = action_gold["items"][8]
    assert item9["clause_id"] == "ipa_secguide_v4_2_3_3_forward"
    assert item9["expected"]["normalized"] == "送信"
    assert item9["expected"]["is_new"] is False
    assert item9["expected"]["renamed_from"] == "データ送信"
    assert "definition" not in item9
    assert action_gold["ledger_seed"][0]["name"] == "送信"
    # 18件を足し終えた台帳は11件(v2 は12件)
    names = {i["expected"]["normalized"] for i in action_gold["items"]} | {"送信"}
    assert len(names) == 11


def test_action_gold_v3_definitions_have_no_modality(action_gold):
    """P16: Action の名前と定義に規範の様相を入れない。"""
    for item in action_gold["items"]:
        assert find_normative_modality(item["expected"]["normalized"]) == []
        definition = item.get("definition")
        if definition:
            assert find_normative_modality(definition["text"]) == [], item["clause_id"]
    seed = action_gold["ledger_seed"][0]
    assert find_normative_modality(seed["definition"]) == []


def test_the_v1_and_v2_golds_are_kept(action_gold):
    """v1・v2 は残す(過去の評価の記録。第30回は v1、第32回は v2 で流した)。"""
    v1 = json.loads(GOLD_V1_PATH.read_text(encoding="utf-8"))
    v2 = json.loads(GOLD_V2_PATH.read_text(encoding="utf-8"))
    assert len(v1["items"]) == len(v2["items"]) == 18
    # v1 には段が無い。v2 で足した
    assert "kind" not in v1["items"][0]["expected"]
    assert v2["_meta"]["based_on"].endswith("action_gold_v1.json(v1 は残す)")
    # v2 は旧い名前のまま(第32回の生データと突き合わせるため)
    assert v2["items"][0]["expected"]["normalized"] == "特権操作"
    assert action_gold["_meta"]["based_on"].startswith("testdata/action_gold/action_gold_v2.json")
    # 条文と語句は v2 から変えていない(名前・段・親・定義だけを改めた)
    for a, b in zip(v2["items"], action_gold["items"]):
        assert a["clause_id"] == b["clause_id"]
        assert a["text"] == b["text"]
        assert a["expected"]["surface"] == b["expected"]["surface"]


def test_frontstage_clauses_are_all_hard_declarative():
    """第29回 HANDOFF 4.1節: 13件はすべて軸1＝ハード制約・軸2＝宣言的。"""
    path = REPO_ROOT / "testdata" / "clauses" / "clauses_frontstage_v1.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert len(doc["clauses"]) == 13
    for clause in doc["clauses"]:
        assert clause["oracle"]["axis1"] == "ハード制約"
        assert clause["oracle"]["axis2"] == "宣言的"
        assert clause["oracle"]["cell_id"] == "hard_declarative"
        assert clause["text_raw"] == clause["text_filled"]
    v1 = json.loads(
        (REPO_ROOT / "testdata" / "clauses" / "clauses_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(v1["clauses"]) == 28
    assert not {c["clause_id"] for c in doc["clauses"]} & {
        c["clause_id"] for c in v1["clauses"]
    }


# --------------------------------------------------------------------------
# ゲートの2種類と v0.5 の指標(P21・P22・P23・P25。タスクB1・B4)
# --------------------------------------------------------------------------


def test_the_design_check_gates_are_not_counted_as_false_alarms(action_gold, seeded_ledger):
    """P21・P22: action_abstract_selected は設計上の確認で、誤報に数えない。

    正解が抽象の項目(人的対策-2)で、正しく抽象を選んでも必ず立つ。第32回はこれを
    誤報に数えていたので、完璧な答えでも誤報が出る状態になっていた(6.2節)。
    """
    for item in action_gold["items"][:3]:
        teach(seeded_ledger, item)
    item = action_gold["items"][7]  # 第11条⑤(正解は既存の抽象「提供」)
    # v0.8: 抽象に寄るのは 3a だけ(P31)。人が「漏洩」を同義語として承認し、
    # 提供の過去の語句に足した状態を作る(P30)
    seeded_ledger.record_surface("提供", "機密を漏洩", item["clause_ref"], "漏洩")
    result, _ = _normalize(
        seeded_ledger, item["text"],
        [{"surface": "漏洩しないこと", "core_verb": "漏洩"}], NO_MATCH,
        clause_ref=item["clause_ref"],
    )
    verdict = judge(item, result, seeded_ledger)
    assert verdict.verdict == "correct"
    assert "action_abstract_selected" in verdict.gates
    # 3a で寄ったので action_synonym_merge は立たない。設計上の確認だけなので、
    # 正しい答えでも誤報は0件になる(W2・P30・P31)
    assert verdict.design_check_gates == ["action_abstract_selected"]
    assert verdict.suspicion_gates == []
    assert verdict.false_alarm_gates == []
    # 人に回ったが、立ったのは設計上の確認だけ
    assert verdict.routed_to_human is True
    assert verdict.design_check_only is True


def test_an_item_with_only_a_design_check_gate_is_counted_separately(
    action_gold, seeded_ledger
):
    """P21: 人に回る割合は「設計上の確認だけ」と「疑いのゲートあり」に分ける。"""
    item = action_gold["items"][0]
    proposal = {
        "name": "操作",
        "definition": "特権/管理者権限で情報システムを操作すること",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": ["特権/管理者権限で操作を行う"],
    }
    result, _ = _normalize(
        seeded_ledger, item["text"],
        [{"surface": "特権/管理者権限で操作を行う", "core_verb": "操作"}], NO_MATCH,
        proposal=proposal, clause_ref=item["clause_ref"],
    )
    teach(seeded_ledger, item)
    verdict = judge(item, result, seeded_ledger)
    assert verdict.gates == ["new_action_unapproved"]
    assert verdict.routed_to_human is True
    assert verdict.design_check_only is True
    assert verdict.false_alarm_gates == []
    # P23: 新規候補の段が正解と一致したか
    assert verdict.new_action_kind_matches is True


def test_a_new_candidate_with_the_wrong_tier_is_counted(action_gold, seeded_ledger):
    """P23: 段が正解と逆の新規候補を数える(4分類では「正しい」のままである)。"""
    for item in action_gold["items"][:2]:
        teach(seeded_ledger, item)
    item = action_gold["items"][2]  # 人的対策-2(正解は abstract)
    proposal = {
        "name": "提供",
        "definition": "情報を他の組織や人に渡すこと",
        "kind": "concrete",  # 正解は abstract
        "parent": None,
        "basis_quotes": ["組織、人に提供"],
    }
    result, _ = _normalize(
        seeded_ledger, item["text"],
        [{"surface": "提供してはならない", "core_verb": "提供"}], NO_MATCH,
        proposal=proposal, clause_ref=item["clause_ref"],
    )
    teach(seeded_ledger, item)
    verdict = judge(item, result, seeded_ledger)
    # 名前と既存／新規は合っているので4分類では正しい(第32回 6.1節の穴)
    assert verdict.verdict == "correct"
    assert verdict.new_action_kind_matches is False


def test_an_existing_choice_has_no_tier_metric(action_gold, taught_ledger):
    """P23: 既存を選んだ項目に段の一致は問えない(分母から外す)。"""
    item = action_gold["items"][5]
    result, _ = _normalize(
        taught_ledger, item["text"], [{"surface": "特権操作", "core_verb": "操作"}],
        "操作", clause_ref=item["clause_ref"],
    )
    assert judge(item, result, taught_ledger).new_action_kind_matches is None


# P25 の仕組みは、保留用の正解の値ではなく合成の項目で確かめる(保留用の正解の値は
# 追補で受け取る。前段設計書 PU3 の例「複写し、又は持ち出してはならない」を使う)
ALT_CLAUSE = "書類を複写し、又は持ち出してはならない。"


def _alt_item() -> dict:
    return {
        "order": 1,
        "clause_id": "synthetic_copy_or_carry_out",
        "clause_ref": "テスト規程 第1条",
        "group": "合成",
        "text": ALT_CLAUSE,
        "expected": {"surface": "書類を複写", "normalized": "複写", "is_new": False,
                     "core_verb": "複写", "kind": "concrete", "parent": None},
        "expected_alternatives": [
            {"surface": "持ち出", "normalized": "持出し", "is_new": False,
             "core_verb": "持ち出", "kind": "concrete", "parent": None},
        ],
        "review": {"approved_by": "pytest"},
    }


def _alt_ledger() -> ActionLedger:
    ledger = ActionLedger.from_json([])
    for name, definition in (("複写", "写しを作ること"),
                             ("持出し", "物を、置かれている場所の外へ持って出ること"),
                             ("放置", "置いたままにすること")):
        ledger.add(ActionEntry(name=name, definition=definition))
    ledger.record_surface("複写", "原本を複写", "テスト規程 第0条", "複写")
    ledger.record_surface("持出し", "持ち出", "テスト規程 第0条", "持ち出")
    return ledger


def test_either_action_of_a_line_of_expectations_is_correct():
    """P25: 並べた正解のどれか1つと一致すれば正しい。"""
    item, ledger = _alt_item(), _alt_ledger()
    for surface, choice, verb in (("書類を複写", "複写", "複写"), ("持ち出", "持出し", "持ち出")):
        result, _ = _normalize(
            ledger, item["text"], [{"surface": surface, "core_verb": verb}], choice,
            clause_ref=item["clause_ref"],
        )
        verdict = judge(item, result, ledger)
        assert verdict.verdict == "correct", choice
        assert verdict.is_alternatives is True
        assert verdict.matched["normalized"] == choice


def test_an_unrelated_action_on_a_line_of_expectations_is_a_mis_merge():
    """P25: 並びのどれとも親子関係にない既存を選べば誤統合(重大)。"""
    item, ledger = _alt_item(), _alt_ledger()
    # v0.9: 既存に寄るのは手順3a だけ(P33)。「書類」が「放置」の過去の語句に
    # 誤って足された状態を作る
    ledger.record_surface("放置", "書類", item["clause_ref"], "書類")
    result, _ = _normalize(
        ledger, item["text"], [{"surface": "書類", "core_verb": "書類"}], "放置",
        clause_ref=item["clause_ref"],
    )
    verdict = judge(item, result, ledger)
    assert verdict.verdict == "mis_merge"
    assert verdict.severe is True
    assert verdict.route == "3a"
    assert "action_synonym_merge" not in verdict.gates


def test_the_ambiguous_gate_is_by_design_on_a_line_of_expectations():
    """P25: 正解が並びの項目で action_surface_ambiguous が立つのは設計どおり。

    正解が1つの項目で立った場合は誤報として数える。
    """
    item, ledger = _alt_item(), _alt_ledger()
    candidates = [
        {"surface": "書類を複写", "core_verb": "複写"},
        {"surface": "持ち出", "core_verb": "持ち出"},
    ]
    result, _ = _normalize(
        ledger, item["text"], candidates, "複写", clause_ref=item["clause_ref"]
    )
    verdict = judge(item, result, ledger)
    assert verdict.surface_ambiguous is True
    assert verdict.verdict == "correct"
    assert verdict.false_alarm_gates == []

    # 正解が1つの項目では誤報になる
    single = _alt_item()
    del single["expected_alternatives"]
    result, _ = _normalize(
        ledger, single["text"], candidates, "複写", clause_ref=single["clause_ref"]
    )
    verdict = judge(single, result, ledger)
    assert verdict.verdict == "correct"
    assert verdict.false_alarm_gates == ["action_surface_ambiguous"]


def test_choosing_a_descendant_of_a_new_abstract_action_is_under_applied():
    """P25・5.1節: 新しい抽象の正解に、ledger_updates でその子になる既存を選ぶと適用漏れ。

    教師ありで正解(抽象)を足し、ledger_updates で既存の具体を子にしてから分類する。
    """
    ledger = _alt_ledger()
    item = {
        "order": 1,
        "clause_id": "synthetic_abstract",
        "clause_ref": "テスト規程 第2条",
        "group": "合成",
        "text": "書類を移動してはならない。",
        "expected": {"surface": "書類を移動", "normalized": "移動", "is_new": True,
                     "core_verb": "移動", "kind": "abstract", "parent": None},
        "ledger_updates": [{"name": "持出し", "parent": "移動"}],
        "definition": {"text": "物の場所を変えること", "status": "provisional",
                       "basis": [], "general_terms": []},
        "review": {"approved_by": "pytest"},
    }
    # v0.9: 既存に寄るのは手順3a だけ(P33)
    ledger.record_surface("持出し", "書類を移動", item["clause_ref"], "移動")
    result, _ = _normalize(
        ledger, item["text"], [{"surface": "書類を移動", "core_verb": "移動"}], "持出し",
        clause_ref=item["clause_ref"],
    )
    teach(ledger, item)
    assert ledger.find("持出し").parent == "移動"
    verdict = judge(item, result, ledger)
    assert verdict.verdict == "under_applied"
    assert verdict.severe is True
