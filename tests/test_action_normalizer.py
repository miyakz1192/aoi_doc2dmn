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
GOLD_PATH = REPO_ROOT / "testdata" / "action_gold" / "action_gold_v2.json"
GOLD_V1_PATH = REPO_ROOT / "testdata" / "action_gold" / "action_gold_v1.json"
REGISTRY_PATH = REPO_ROOT / "registry" / "registry.json"

CLAUSE = "許可なく職務以外の目的で会社の施設、物品等を使用しないこと。"
SURFACE = "会社の施設、物品等を使用"
SEED_DEFINITION = "あおいが、患者に関する情報を、患者や代理人に電子的に送ること"


@pytest.fixture
def action_gold() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def seeded_ledger() -> ActionLedger:
    """出発点(あおいの外部送信)だけの台帳。"""
    return ActionLedger(
        entries=[
            ActionEntry(
                name="外部送信",
                definition=SEED_DEFINITION,
                kind="concrete",
                actors=["あおい"],
            )
        ]
    )


def _responses(
    candidates: list[dict],
    choice: str,
    *,
    proposal: dict | None = None,
    considered: list[dict] | None = None,
) -> list[dict]:
    """k=1 の呼び出し(抜き出し → 選択 →(該当なしなら)候補)の応答を組み立てる。"""
    out: list[dict] = [{"candidates": candidates, "rationale": "(固定)"}]
    out.append(
        {
            "choice": choice,
            "reason": f"{choice} を選んだ(固定)",
            "definition_part": "",
            "considered": considered
            if considered is not None
            else [{"name": "外部送信", "why_not": "手段が違う(固定)"}],
        }
    )
    if proposal is not None:
        out.append(proposal)
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
    「しては」のような残りが出る。行為が1つも入っていないので、評価の3条件のうち
    「正解の中心の動詞を含む」で落ちる。**この形はゲートでは捕まらない**
    (条文の部分文字列ではあるため)。限界として記録する。
    """
    assert strip_modality("ない") == ("ない", [])
    assert strip_modality("してはならない") == ("しては", ["ならない"])


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
    """P7 v0.4: k=1。呼び出しは抜き出しと選択の2回で、温度は 0.0。"""
    result, llm = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], "外部送信"
    )
    assert llm.temperatures == [TEMPERATURE, TEMPERATURE]
    assert result.trace.llm_calls == 2
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
        proposal={"name": "特権操作", "definition": "特権/管理者権限で情報システムを操作すること",
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
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], "外部送信"
    )
    assert "action_surface_ambiguous" not in result.gates.gate_names()


# --------------------------------------------------------------------------
# 動詞の一致の検査(手順4・P15)
# --------------------------------------------------------------------------


def test_verb_mismatch_raises_the_gate(seeded_ledger):
    """P15: 既存に寄せたが、中心の動詞が過去の語句の動詞にない。"""
    seeded_ledger.record_surface("外部送信", "情報を送信", "あおいの既存DMN", "送信")
    result, _ = _normalize(
        seeded_ledger,
        "医療情報を口頭で伝達してはならない。",
        [{"surface": "口頭で伝達してはならない", "core_verb": "伝達"}],
        "外部送信",
    )
    assert "action_verb_mismatch" in result.gates.gate_names()
    check = result.trace.verb_check
    assert check is not None and check.matched is False
    assert check.core_verb == "伝達" and check.compared == ["送信"]


def test_verb_match_allows_an_okurigana_difference():
    """判断: 完全一致か、どちらかがもう片方を含むことを一致とする。"""
    assert core_verbs_match("従事させ", ["従事"])[0] is True
    assert core_verbs_match("従事", ["労働に従事させ"])[0] is True
    assert core_verbs_match("伝達", ["提供"])[0] is False
    # 過去の語句が無い Action に寄せた場合も立てる(P15)
    assert core_verbs_match("提供", [])[0] is False


def test_verb_check_is_not_run_for_a_new_action(seeded_ledger):
    """新規候補のときは比べる相手が無いので、動詞の検査はしない。"""
    result, _ = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], NO_MATCH,
        proposal={"name": "施設物品使用", "definition": "会社の施設や物品を使うこと",
                  "kind": "concrete", "parent": None, "basis_quotes": [SURFACE]},
    )
    assert result.trace.verb_check is None
    assert "action_verb_mismatch" not in result.gates.gate_names()


def test_abstract_selected_raises_the_gate(seeded_ledger):
    """P15: 抽象的な段を選んだら、禁止が子すべてに及ぶので人が段を確かめる。"""
    seeded_ledger.add(
        ActionEntry(
            name="情報提供",
            definition="手段を問わず、情報を他の組織や人に渡すこと",
            kind="abstract",
        )
    )
    seeded_ledger.record_surface("情報提供", "組織、人に提供", "人的対策-2", "提供")
    result, _ = _normalize(
        seeded_ledger,
        "機密を漏洩しないこと。",
        [{"surface": "漏洩しないこと", "core_verb": "漏洩"}],
        "情報提供",
    )
    assert "action_abstract_selected" in result.gates.gate_names()
    assert result.chosen_kind == "abstract"


# --------------------------------------------------------------------------
# 新規候補(手順5・P16)
# --------------------------------------------------------------------------


def test_no_match_proposes_a_new_action_with_a_kind_and_never_writes_the_parent(
    seeded_ledger,
):
    """P6: 親の案は Trace にだけ置く。段(kind)の案も出させる(P14)。"""
    proposal = {
        "name": "口頭伝達",
        "definition": "情報を、声で相手に伝えること",
        "kind": "concrete",
        "parent": "情報提供",
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
    assert result.action.normalized == "口頭伝達"
    assert result.action.parent is None  # IR には書かない(P6)
    assert result.action.approved_by is None  # 承認は人(D53)
    assert result.trace.new_action_proposal.parent_suggestion == "情報提供"
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
    assert "台帳にない名前" in result.trace.run.reason


# --------------------------------------------------------------------------
# プロンプトに渡すもの(P11・P14)
# --------------------------------------------------------------------------


def test_the_ledger_block_shows_the_kind_the_parent_and_the_core_verbs(seeded_ledger):
    seeded_ledger.add(
        ActionEntry(
            name="情報提供",
            definition="手段を問わず、情報を他の組織や人に渡すこと",
            kind="abstract",
        )
    )
    seeded_ledger.find("外部送信").parent = "情報提供"
    seeded_ledger.find("外部送信").excludes = ["口頭伝達"]
    seeded_ledger.record_surface("情報提供", "組織、人に提供", "人的対策-2", "提供")
    block = seeded_ledger.prompt_block()
    assert "抽象的な行為(手段を問わない)" in block
    assert "親(より抽象的な行為): 情報提供" in block
    assert "この Action に含まないもの: 口頭伝達" in block
    assert "中心の動詞: 提供" in block


def test_the_choice_prompt_tells_the_model_to_match_the_tier(seeded_ledger):
    """手順3: 段を合わせること・excludes にある行為は選ばないことを示す(P14)。"""
    _result, llm = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], "外部送信"
    )
    choice_prompt = llm.prompts[1]
    assert "段を合わせてください" in choice_prompt
    assert "この Action に含まないもの" in choice_prompt
    assert "中心の動詞" in choice_prompt


def test_the_surface_prompt_warns_about_the_sentence_final_predicate(seeded_ledger):
    """P17: 「〜は」「〜が」の直後の体言が行為であることを示す。"""
    _result, llm = _normalize(
        seeded_ledger, CLAUSE, [{"surface": SURFACE, "core_verb": "使用"}], "外部送信"
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
    assert "条件(誰が・どこで・いつ・どの区分か)を名前と定義に入れない" in prompt
    assert "一般的な言い換えや" in prompt


def test_context_is_offered_but_not_as_the_source_of_the_surface(seeded_ledger):
    """IR v2 1.4節: context は補足で、逐語で抜き出す対象は条文本文だけ。"""
    context = [{"text": "特権アカウント", "where": "6.10 の表の列の見出し"}]
    llm = ScriptedJsonLlm(
        _responses([{"surface": SURFACE, "core_verb": "使用"}], "外部送信")
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
    assert check.closest == "外部送信"
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
    assert ledger.names() == ["外部送信"]
    entry = ledger.entries[0]
    # P18: 承認の状態は名前と定義で分かれている
    assert entry.name_status == "provisional"
    assert entry.definition_status == "provisional"
    # タスクA2: 初期登録では親を付けない(評価の中で付く)
    assert entry.kind == "concrete"
    assert entry.parent is None
    registry = Registry.load(REGISTRY_PATH)
    assert registry.find_action("外部送信") is not None
    assert registry.action_kind("外部送信") == "concrete"


def test_teaching_adds_the_expected_action_with_its_kind_and_parent(
    action_gold, seeded_ledger
):
    """P13・4.2節: 台帳に足すのは正解の Action で、段と親も正解どおり。"""
    item = action_gold["items"][4]  # 口頭伝達(親は情報提供)
    message = teach(seeded_ledger, item)
    assert "台帳に追加" in message
    entry = seeded_ledger.find("口頭伝達")
    assert entry is not None
    assert entry.kind == "concrete"
    assert entry.parent == "情報提供"
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
    """4.1節: 項目3で情報提供が台帳に入った時点で、外部送信に親が付く。"""
    item = action_gold["items"][2]  # 人的対策-2(情報提供・abstract)
    assert seeded_ledger.find("外部送信").parent is None
    message = teach(seeded_ledger, item)
    assert seeded_ledger.find("情報提供").kind == "abstract"
    assert seeded_ledger.find("外部送信").parent == "情報提供"
    assert "親を付けた" in message
    assert seeded_ledger.relation("情報提供", "外部送信") == "descendant"


# --------------------------------------------------------------------------
# 誤りの4分類(5.1節・P9)
# --------------------------------------------------------------------------


@pytest.fixture
def taught_ledger(action_gold, seeded_ledger) -> ActionLedger:
    """項目5まで教えた台帳(情報提供・その子・特権操作などが入っている)。"""
    for item in action_gold["items"][:5]:
        teach(seeded_ledger, item)
    return seeded_ledger


def test_mis_merge_is_an_unrelated_existing_action(action_gold, taught_ledger):
    """誤統合: 親子関係にない既存の Action に寄せた(重大)。"""
    item = action_gold["items"][14]  # 物理3(正解は新規の「放置」)
    result, _ = _normalize(
        taught_ledger, item["text"],
        [{"surface": "原稿、印刷物を放置しない", "core_verb": "放置"}], "施設物品使用",
        clause_ref=item["clause_ref"],
    )
    assert classify(item, result, taught_ledger) == "mis_merge"
    verdict = judge(item, result, taught_ledger)
    assert verdict.severe is True
    # 動詞の一致の検査が捕まえるので、すり抜けない(P15)
    assert "action_verb_mismatch" in verdict.gates
    assert verdict.slipped is False


def test_under_applied_is_choosing_a_descendant_of_the_right_abstract_action(
    action_gold, seeded_ledger
):
    """適用漏れ: 正解は抽象的な Action なのに、その子孫を選んだ(重大)。"""
    item = action_gold["items"][2]  # 人的対策-2(正解は抽象の「情報提供」)
    result, _ = _normalize(
        seeded_ledger, item["text"],
        [{"surface": "組織、人に提供してはならない", "core_verb": "提供"}], "外部送信",
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
    item = action_gold["items"][4]  # 第14条(正解は具体の「口頭伝達」)
    result, _ = _normalize(
        seeded_ledger, item["text"],
        [{"surface": "口頭で伝達してはならない", "core_verb": "伝達"}], "情報提供",
        clause_ref=item["clause_ref"],
    )
    teach(seeded_ledger, item)
    assert classify(item, result, seeded_ledger) == "over_applied"
    verdict = judge(item, result, seeded_ledger)
    assert verdict.severe is False  # 安全側に倒れるので重大ではない
    assert "action_abstract_selected" in verdict.gates


def test_missing_an_existing_action_is_counted_as_a_miss(action_gold, taught_ledger):
    """取りこぼし: 正解は既存なのに新規にした(軽。ゲートで人が拾える)。"""
    item = action_gold["items"][5]  # 6.1⑤(正解は既存の「特権操作」)
    proposal = {
        "name": "私有機器使用",
        "definition": "私有機器を使うこと",
        "kind": "concrete",
        "parent": None,
        "basis_quotes": [],
    }
    result, _ = _normalize(
        taught_ledger, item["text"],
        [{"surface": "私有機器を使用しないこと", "core_verb": "使用"}], NO_MATCH,
        proposal=proposal, clause_ref=item["clause_ref"],
    )
    verdict = judge(item, result, taught_ledger)
    assert verdict.verdict == "missed"
    assert verdict.severe is False
    assert "new_action_unapproved" in verdict.gates


def test_choosing_the_right_existing_action_is_correct(action_gold, taught_ledger):
    """既存に正しく寄せた場合は、どの誤りにもならない。"""
    item = action_gold["items"][5]
    result, _ = _normalize(
        taught_ledger, item["text"], [{"surface": "特権操作", "core_verb": "操作"}],
        "特権操作", clause_ref=item["clause_ref"],
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
        "name": "特権操作",
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


def test_action_gold_v2_has_18_items_in_evaluation_order(action_gold):
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


def test_action_gold_v2_hierarchy_matches_the_design(action_gold):
    """4.1節の表どおりの段と親になっていること。"""
    by_name = {}
    for item in action_gold["items"]:
        expected = item["expected"]
        by_name.setdefault(expected["normalized"], expected)
    assert by_name["情報提供"]["kind"] == "abstract"
    assert by_name["口頭伝達"]["parent"] == "情報提供"
    assert by_name["データ送信"]["parent"] == "情報提供"
    # データ取り出しは向きが違うので情報提供の子にしない(3.4節)
    assert by_name["データ取り出し"]["parent"] is None
    # 項目3で外部送信に親が付く(4.1節)
    updates = action_gold["items"][2]["ledger_updates"]
    assert updates == [
        {
            "name": "外部送信",
            "parent": "情報提供",
            "reason": updates[0]["reason"],
        }
    ]


def test_action_gold_v2_definitions_have_no_modality(action_gold):
    """P16: Action の名前と定義に規範の様相を入れない。"""
    for item in action_gold["items"]:
        assert find_normative_modality(item["expected"]["normalized"]) == []
        definition = item.get("definition")
        if definition:
            assert find_normative_modality(definition["text"]) == [], item["clause_id"]
    seed = action_gold["ledger_seed"][0]
    assert find_normative_modality(seed["definition"]) == []


def test_the_v1_gold_is_kept(action_gold):
    """第31回 HANDOFF 4章: v1 は残す(比較のため)。"""
    v1 = json.loads(GOLD_V1_PATH.read_text(encoding="utf-8"))
    assert len(v1["items"]) == 18
    # v1 には段が無い。v2 で足した
    assert "kind" not in v1["items"][0]["expected"]
    assert action_gold["_meta"]["based_on"].endswith("action_gold_v1.json(v1 は残す)")


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
