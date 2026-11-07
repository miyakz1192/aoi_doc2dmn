"""前段(条文 → IR)の第1要素: Action 正規化のテスト。

第29回 HANDOFF パートD。前段設計書 v0.3 の3〜5章が仕様である。
**LLM は固定の応答を返す偽物に差し替えて行う**(5.3節「単体テストは LLM を差し替えて
行い、Ollama なしで通るようにする」)。対象は次の6つ。

| 確かめること | 根拠 |
| --- | --- |
| 逐語の確認(抜き出した語句が条文にあるか) | 前段設計書4.2節 手順2・P4 |
| 様相の検出 | P4・IR v2 D71 |
| 多数決(k 回の選択の一致) | P7・4.2節 手順4 |
| ゲート(4つ) | 4.5節 |
| 教師ありの台帳の更新 | P13 |
| 集計(誤統合・取りこぼし) | 5.1節・P9 |

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
    TEMPERATURES,
    ActionNormalizer,
    ScriptedJsonLlm,
)
from src.stage3_frontstage.modality import (  # noqa: E402
    MODALITY_WORDS,
    check_surface,
    find_modality,
)
from src.stage3_frontstage.run_action_eval import judge, load_ledger, teach  # noqa: E402
from src.stage3_generate.registry import Registry  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = REPO_ROOT / "testdata" / "action_gold" / "action_gold_v1.json"
REGISTRY_PATH = REPO_ROOT / "registry" / "registry.json"

CLAUSE = "許可なく職務以外の目的で会社の施設、物品等を使用しないこと。"
SURFACE = "会社の施設、物品等を使用"


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
                definition="患者に関する情報を、患者・代理人などの外部に送ること",
                actors=["あおい"],
            )
        ]
    )


def _runs(*pairs: tuple[str, str], proposal: dict | None = None) -> list[dict]:
    """(抜き出す語句, 選ぶ Action) の組から、偽の LLM の応答を組み立てる。"""
    responses: list[dict] = []
    for surface, choice in pairs:
        responses.append({"surface": surface, "rationale": "(固定)"})
        responses.append(
            {
                "choice": choice,
                "reason": f"{choice} を選んだ(固定)",
                "definition_part": "",
                "considered": [{"name": "外部送信", "why_not": "手段が違う(固定)"}],
            }
        )
    if proposal is not None:
        responses.append(proposal)
    return responses


# --------------------------------------------------------------------------
# 様相の語と逐語の確認(手順2・P4)
# --------------------------------------------------------------------------


def test_modality_words_are_reported_longest_first():
    """「しないこと」を見つけたら、その内側の「ない」は重ねて報告しない。"""
    assert find_modality("使用しないこと") == ["しないこと"]
    assert find_modality("提供してはならない") == ["してはならない"]
    assert find_modality("放置禁止") == ["禁止"]
    assert find_modality("会社の施設、物品等を使用") == []


def test_causative_sase_is_not_modality():
    """P4・前段設計書6.1節: 使役の「させ」は様相ではないので残す。

    「就業する(労働者)」と「就業させる(使用者)」は主体が違う別の行為なので、
    使役を落とすと2つの Action を区別できなくなる。
    """
    assert find_modality("労働に従事させ") == []
    assert find_modality("就業させ") == []
    # 打ち消しの「ない」だけが様相
    assert find_modality("労働に従事させない") == ["ない"]


def test_surface_must_be_a_substring_of_the_clause():
    """手順2: 抜き出した語句が条文に逐語で実在することをプログラムで確かめる。"""
    assert check_surface(SURFACE, CLAUSE).ok
    bad = check_surface("会社の設備を利用", CLAUSE)
    assert not bad.ok
    assert not bad.in_clause
    assert "実在しない" in " ".join(bad.reasons())


def test_the_gold_surfaces_all_pass_the_two_program_checks(action_gold):
    """D71: 正解18件の surface は、条文の部分文字列で様相を含まない。"""
    for item in action_gold["items"]:
        check = check_surface(item["expected"]["surface"], item["text"])
        assert check.ok, (item["clause_id"], check.reasons())
        assert item["expected"]["core_verb"] in item["expected"]["surface"]


def test_modality_word_list_covers_the_examples_in_the_design():
    """設計書 D71 が挙げる4つの例が、一覧に入っている。"""
    for word in ("してはならない", "しないこと", "禁止", "ない"):
        assert word in MODALITY_WORDS


# --------------------------------------------------------------------------
# 多数決とゲート(手順4・4.5節)
# --------------------------------------------------------------------------


def test_unanimous_choice_of_an_existing_action(seeded_ledger):
    """3回とも同じ既存の Action を選んだら、is_new=False でゲートは立たない。"""
    llm = ScriptedJsonLlm(
        _runs(
            ("会社の施設、物品等を使用", "外部送信"),
            ("会社の施設、物品等を使用", "外部送信"),
            ("会社の施設、物品等を使用", "外部送信"),
        )
    )
    result = ActionNormalizer(llm).normalize(CLAUSE, "就業規則 第11条", seeded_ledger)
    assert result.action.normalized == "外部送信"
    assert result.action.is_new is False
    assert result.trace.agreement == "3/3"
    assert result.gates.gate_names() == []
    # P6・D53: 前段は親を書かず、承認者も入れない
    assert result.action.parent is None
    assert result.action.approved_by is None


def test_split_choice_raises_the_unstable_gate(seeded_ledger):
    """P7: k 回の選択が一致しなければ action_mapping_unstable が立つ。

    確信度の自己申告は使えない(1.1節)ので、選択が揺れること自体を証拠にする。
    """
    llm = ScriptedJsonLlm(
        _runs(
            (SURFACE, "外部送信"),
            (SURFACE, NO_MATCH),
            (SURFACE, "外部送信"),
        )
    )
    result = ActionNormalizer(llm).normalize(CLAUSE, "就業規則 第11条", seeded_ledger)
    assert result.trace.agreement == "2/3"
    assert "action_mapping_unstable" in result.gates.gate_names()
    assert result.action.normalized == "外部送信"


def test_a_tie_falls_back_to_the_first_run(seeded_ledger):
    """同数のときは1回目(temperature=0.0)の選択を採る。どちらでも不安定は立つ。"""
    llm = ScriptedJsonLlm(_runs((SURFACE, "外部送信"), (SURFACE, NO_MATCH)))
    normalizer = ActionNormalizer(llm, temperatures=(0.0, 0.3))
    result = normalizer.normalize(CLAUSE, "就業規則 第11条", seeded_ledger)
    assert result.action.normalized == "外部送信"
    assert result.trace.agreement == "1/2"
    assert "action_mapping_unstable" in result.gates.gate_names()


def test_the_k_runs_do_not_all_use_temperature_zero():
    """temperature=0.0 は決定論的デコードなので、k 回とも 0.0 では揺れが観測できない。

    1回目は 0.0(再現性)、2回目以降は 0.3 で呼ぶ。これを 0.0 に戻すと、自己一貫性の
    ゲートが「常に k/k」になって機能しなくなる(確信度の自己申告と同じ失敗)。
    """
    assert TEMPERATURES[0] == 0.0
    assert all(t > 0 for t in TEMPERATURES[1:])
    llm = ScriptedJsonLlm(_runs(*[(SURFACE, "外部送信")] * 3))
    ActionNormalizer(llm).normalize(
        CLAUSE,
        "就業規則 第11条",
        ActionLedger(entries=[ActionEntry(name="外部送信", definition="(略)")]),
    )
    assert llm.temperatures == [0.0, 0.0, 0.3, 0.3, 0.3, 0.3]


def test_surface_with_modality_raises_the_surface_gate(seeded_ledger):
    """P4: 抜き出した語句が様相の語を含めば action_surface_unverified が立つ。"""
    llm = ScriptedJsonLlm(_runs(*[("使用しないこと", "外部送信")] * 3))
    result = ActionNormalizer(llm).normalize(CLAUSE, "就業規則 第11条", seeded_ledger)
    assert "action_surface_unverified" in result.gates.gate_names()
    assert result.trace.runs[0].surface_check.modality_found == ["しないこと"]


def test_surface_not_in_the_clause_raises_the_surface_gate(seeded_ledger):
    """手順2: 条文に無い語句(ハルシネーション)でもゲートが立つ。"""
    llm = ScriptedJsonLlm(_runs(*[("会社の設備を利用", "外部送信")] * 3))
    result = ActionNormalizer(llm).normalize(CLAUSE, "就業規則 第11条", seeded_ledger)
    assert "action_surface_unverified" in result.gates.gate_names()


def test_no_match_proposes_a_new_action_and_never_writes_the_parent(seeded_ledger):
    """手順5・P6: 「該当なし」なら新規候補を出す。親の案は Trace にだけ置く。"""
    proposal = {
        "name": "施設物品使用",
        "definition": "会社の施設や物品等を使用すること",
        "parent": "会社資源利用",
        "basis_quotes": ["会社の施設、物品等を使用", "この語は条文に無い"],
    }
    llm = ScriptedJsonLlm(_runs(*[(SURFACE, NO_MATCH)] * 3, proposal=proposal))
    result = ActionNormalizer(llm).normalize(CLAUSE, "就業規則 第11条", seeded_ledger)
    assert result.action.is_new is True
    assert result.action.normalized == "施設物品使用"
    assert result.action.parent is None  # IR には書かない(P6)
    assert result.trace.new_action_proposal is not None
    assert result.trace.new_action_proposal.parent_suggestion == "会社資源利用"
    # PU4: 定義の根拠の語句が条文に実在するかをプログラムで確かめる
    assert result.trace.new_action_proposal.basis_verified == [True, False]
    assert "new_action_unapproved" in result.gates.gate_names()


def test_a_name_outside_the_ledger_is_treated_as_no_match(seeded_ledger):
    """P5: 閉じた選択。台帳にない名前を返してきたら「該当なし」として扱う。

    自由に名前を作らせると、同じ行為に別の名前が付いて語彙が増え続ける(U1)。
    """
    proposal = {
        "name": "施設物品使用",
        "definition": "会社の施設や物品等を使用すること",
        "parent": None,
        "basis_quotes": [],
    }
    llm = ScriptedJsonLlm(
        _runs(*[(SURFACE, "施設の私的利用")] * 3, proposal=proposal)
    )
    result = ActionNormalizer(llm).normalize(CLAUSE, "就業規則 第11条", seeded_ledger)
    assert result.action.is_new is True
    assert result.action.normalized == "施設物品使用"
    assert "台帳にない名前" in result.trace.runs[0].reason


def test_the_ledger_block_shows_definitions_and_examples(seeded_ledger):
    """P11: プロンプトには台帳の名前・定義・これまでの語句を渡す。"""
    seeded_ledger.record_surface("外部送信", "情報を送信", "あおいの DMN")
    block = seeded_ledger.prompt_block()
    assert "外部送信" in block
    assert "患者に関する情報" in block
    assert "情報を送信" in block
    llm = ScriptedJsonLlm(_runs(*[(SURFACE, "外部送信")] * 3))
    ActionNormalizer(llm).normalize(CLAUSE, "就業規則 第11条", seeded_ledger)
    choice_prompt = llm.prompts[1]
    assert "患者に関する情報" in choice_prompt
    assert NO_MATCH in choice_prompt


def test_context_is_offered_but_not_as_the_source_of_the_surface(seeded_ledger):
    """IR v2 1.4節: context は出典付きの補足。逐語の抜き出し元は条文本文だけ。"""
    llm = ScriptedJsonLlm(_runs(*[(SURFACE, "外部送信")] * 3))
    ActionNormalizer(llm).normalize(
        CLAUSE,
        "就業規則 第11条",
        seeded_ledger,
        context=[{"text": "レベル1領域", "where": "表の行の見出し"}],
    )
    assert "レベル1領域" in llm.prompts[0]
    assert "条文本文から抜き出すこと" in llm.prompts[0]


# --------------------------------------------------------------------------
# 近さの判定(P8)
# --------------------------------------------------------------------------


def test_duplicate_check_flags_a_shared_head_noun(seeded_ledger):
    """P8: 末尾の体言が同じ新規候補は、既存に寄せるべきかを人に見せる。

    「データ送信」と「外部送信」は文字列の比では 0.44 でしきい値に届かないが、
    行為の種類を担う末尾の体言(送信)が同じなので、取りこぼしの候補として拾う。
    """
    check = seeded_ledger.duplicate_check(
        "データ送信", "電子メールなどで、データを宛先に送ること（転送を含む）"
    )
    assert check.flagged
    assert check.closest == "外部送信"
    assert name_ratio("データ送信", "外部送信") < NAME_RATIO_THRESHOLD
    assert shared_suffix_length("データ送信", "外部送信") >= SHARED_SUFFIX_THRESHOLD


def test_duplicate_check_does_not_flag_an_unrelated_action(seeded_ledger):
    """別の行為は拾わない(拾いすぎるとゲートが意味を失う)。"""
    check = seeded_ledger.duplicate_check(
        "アカウント共用", "1つのアカウントを2人以上で使うこと"
    )
    assert not check.flagged
    assert definition_overlap(
        "1つのアカウントを2人以上で使うこと",
        "患者に関する情報を、患者・代理人などの外部に送ること",
    ) < 0.3


def test_duplicate_gate_is_raised_for_a_close_new_candidate(seeded_ledger):
    """P8: 近い新規候補には action_possible_duplicate も立つ。"""
    proposal = {
        "name": "データ送信",
        "definition": "電子メールなどで、データを宛先に送ること（転送を含む）",
        "parent": None,
        "basis_quotes": [],
    }
    clause = "社用メールアドレスで受信したメールを従業員個人のアドレスに転送することを禁止する。"
    llm = ScriptedJsonLlm(
        _runs(*[("従業員個人のアドレスに転送", NO_MATCH)] * 3, proposal=proposal)
    )
    result = ActionNormalizer(llm).normalize(clause, "IPA 2.3.3", seeded_ledger)
    assert "action_possible_duplicate" in result.gates.gate_names()
    assert "new_action_unapproved" in result.gates.gate_names()


# --------------------------------------------------------------------------
# 教師ありの台帳の更新(P13)と集計(5.1節)
# --------------------------------------------------------------------------


def test_the_ledger_start_comes_from_the_initial_registry(action_gold):
    """P10: 台帳の出発点は初期登録の登録簿にある Action(外部送信)だけ。"""
    ledger = load_ledger(action_gold, REGISTRY_PATH)
    assert ledger.names() == ["外部送信"]
    assert ledger.entries[0].status == "provisional"
    # 登録簿の中に層1〜3と並べて置いた(前段設計書3章)
    registry = Registry.load(REGISTRY_PATH)
    assert registry.find_action("外部送信") is not None
    assert "action_ledger" in registry.to_json()


def test_teaching_adds_the_expected_action_not_the_output(action_gold, seeded_ledger):
    """P13: 台帳に足すのは前段の出力ではなく正解の Action。

    前段が1件誤ったとき、その誤りが後の条文の選択肢に持ち越されると、後の誤りの
    原因が分からなくなる。
    """
    item = action_gold["items"][0]  # 特権操作
    message = teach(seeded_ledger, item)
    assert "台帳に追加" in message
    entry = seeded_ledger.find("特権操作")
    assert entry is not None
    assert entry.definition == item["definition"]["text"]
    assert entry.parent is None  # D60: 階層の位置づけは人の判断
    assert entry.approved_by == "Kazuhiro"
    assert entry.surfaces == [
        {"text": item["expected"]["surface"], "clause_ref": item["clause_ref"]}
    ]
    # 2件目が同じ Action に寄るときは、語句だけが足される
    second = {
        "order": 6,
        "clause_id": "ipa_secguide_v4_6_1_5",
        "clause_ref": "IPA 6.1",
        "expected": {"surface": "特権操作", "normalized": "特権操作", "is_new": False},
        "review": {"approved_by": "Kazuhiro", "reviewed_at": "2026-11-08"},
    }
    assert "語句を追加" in teach(seeded_ledger, second)
    assert len(seeded_ledger.find("特権操作").surfaces) == 2


def test_mis_merge_is_counted_when_an_existing_action_is_chosen_wrongly(
    action_gold, seeded_ledger
):
    """5.1節・P9: 正解が新規なのに既存を選んだら誤統合(最重要)。"""
    item = action_gold["items"][4]  # 口頭伝達(外部送信に寄せないことを確かめる項目)
    llm = ScriptedJsonLlm(_runs(*[("口頭で伝達", "外部送信")] * 3))
    result = ActionNormalizer(llm).normalize(
        item["text"], item["clause_ref"], seeded_ledger
    )
    verdict = judge(item, result)
    assert verdict.mis_merge is True
    assert verdict.missed is False
    assert verdict.is_new_correct is False
    assert verdict.surface_ok is True


def test_missing_an_existing_action_is_counted_as_a_miss(action_gold, seeded_ledger):
    """5.1節: 正解が既存なのに新規にしたら取りこぼし(ゲートで人が拾える)。"""
    item = action_gold["items"][5]  # 6.1⑤(正解は既存の「特権操作」)
    seeded_ledger.add(
        ActionEntry(name="特権操作", definition="特権/管理者権限で情報システムを操作すること")
    )
    proposal = {
        "name": "多要素認証実施",
        "definition": "特権操作を多要素認証を用いて実施すること",
        "parent": None,
        "basis_quotes": [],
    }
    llm = ScriptedJsonLlm(_runs(*[("特権操作", NO_MATCH)] * 3, proposal=proposal))
    result = ActionNormalizer(llm).normalize(
        item["text"], item["clause_ref"], seeded_ledger
    )
    verdict = judge(item, result)
    assert verdict.missed is True
    assert verdict.mis_merge is False
    assert verdict.choice_correct is False


def test_choosing_the_right_existing_action_is_not_a_mis_merge(
    action_gold, seeded_ledger
):
    """既存に正しく寄せた場合は、誤統合でも取りこぼしでもない。"""
    item = action_gold["items"][5]
    seeded_ledger.add(ActionEntry(name="特権操作", definition="(略)"))
    llm = ScriptedJsonLlm(_runs(*[("特権操作", "特権操作")] * 3))
    result = ActionNormalizer(llm).normalize(
        item["text"], item["clause_ref"], seeded_ledger
    )
    verdict = judge(item, result)
    assert (verdict.mis_merge, verdict.missed) == (False, False)
    assert verdict.choice_correct is True
    assert verdict.surface_ok is True


# --------------------------------------------------------------------------
# 正解ファイルの形(パートC)
# --------------------------------------------------------------------------


def test_action_gold_has_18_items_in_evaluation_order(action_gold):
    """P13: 正解IR 5件 ＋ 候補13件を、評価の順に並べる。"""
    items = action_gold["items"]
    assert len(items) == 18
    assert [i["order"] for i in items] == list(range(1, 19))
    assert sum(1 for i in items if i["group"] == "正解IR") == 5
    assert len(action_gold["ledger_seed"]) == 1
    for item in items:
        # D53: 正解にも承認者は入れるが、前段の出力の approved_by は常に None
        assert item["review"]["approved_by"] == "Kazuhiro"
        if item["expected"]["is_new"]:
            assert item["definition"]["text"], item["clause_id"]
            assert item["definition"]["basis"], item["clause_id"]


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
    # ②の評価用の28件には混ぜない(分布が変わるため)
    v1 = json.loads(
        (REPO_ROOT / "testdata" / "clauses" / "clauses_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(v1["clauses"]) == 28
    assert not {c["clause_id"] for c in doc["clauses"]} & {
        c["clause_id"] for c in v1["clauses"]
    }
