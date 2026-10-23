"""初期登録した DMN の修正(D34)のテスト。

第19回 HANDOFF タスク4。元の DMN(`reference/` にあるあおいの送信可否判定)には、
参照しても当たらない値と、どの決定表も参照しない inputData があった。D34 により、
元ファイルは書き換えず `registry/` の側で直し、内容・理由・承認者を履歴に残す。

| 確かめること | 根拠 |
| --- | --- |
| 修正が1か所(`DMN_FIXES`)にまとまり、内容・理由・承認者を持つ | D34・HANDOFF タスク4 |
| 層1・層2の `history` に修正が残る | D34 |
| 修正を外すと、登録簿が C11 で止まる | D34「修正後の登録簿に C5・C11 を適用」 |
| 修正の前後で DMN の評価結果が変わらない | 「未」は identity_level に来ない値 |
| 初期登録を再実行すると同じ結果になる | HANDOFF タスク4 |
| `reference/` の元ファイルは書き換えない | D34 |

元の DMN は `tests/fixtures/aoi_transmission_baseline.dmn` にコミットしてある(D42)。
v1.3 までは `reference/`(.gitignore 対象)を読んでいたため、`reference/` のない環境では
このファイルのテストの大半が飛ばされていた。**今は skip が無い。** あおいの既存実装側の
ファイルとの照合だけは、`reference/` がある環境でのみ行う
(`test_fixture_matches_the_upstream_file_when_available`)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage3_generate.bootstrap_aoi import (  # noqa: E402
    DMN_FIXES,
    DMN_MODEL,
    FIX_APPROVER,
    SOURCE_DMN_DEFAULT,
    UPSTREAM_DMN,
    build_registry,
)
from src.stage3_generate.dmn_engine import DmnModel  # noqa: E402
from src.stage3_generate.registry import Registry  # noqa: E402
from src.stage3_generate.wiring_generator import SchemaWiringError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "registry" / "registry.json"
FIXED_DMN = REPO_ROOT / "registry" / "dmn" / f"{DMN_MODEL}.dmn"
# D42: 元の DMN はリポジトリにコミットしてある(tests/fixtures/)。
SOURCE_DMN = REPO_ROOT / SOURCE_DMN_DEFAULT
# あおいの既存実装側。コピーが古くなっていないかの照合にだけ使う(無い環境もある)。
UPSTREAM_DMN_PATH = REPO_ROOT / UPSTREAM_DMN

# 本人確認判定が出しうる確認レベルの全値と、その値になる入力(DMN から読み取れる)
IDENTITY_SCENARIOS = {
    "高": {"name_match": "一致", "birth_match": "一致", "phone_match": "一致"},
    "中": {"name_match": "一致", "birth_match": "一致", "phone_match": "未提供"},
    "不一致検出": {"name_match": "一致", "birth_match": "不一致", "phone_match": "一致"},
    "情報不足": {
        "name_match": "未提供",
        "birth_match": "未提供",
        "phone_match": "未提供",
    },
}
# 送信可否判定の残りの入力: 機微度レベル(情報分類判定の出力)と連絡同意
SENSITIVITY_SCENARIOS = {
    "最高": {"has_medical": "含む", "has_pii": "含まない", "identifiable": "可能"},
    "高": {"has_medical": "含まない", "has_pii": "含む", "identifiable": "可能"},
    "中": {
        "has_medical": "含まない",
        "has_pii": "含まない",
        "identifiable": "不可能_院内",
    },
    "低": {
        "has_medical": "含まない",
        "has_pii": "含まない",
        "identifiable": "不可能_公開",
    },
}
CONSENTS = ["同意", "不同意"]


@pytest.fixture
def source_dmn() -> Path:
    """初期登録の元にする DMN(D42 でリポジトリにコミットしたもの)。

    v1.3 までは `reference/` を読んでいたため、`reference/` のない環境では
    修正前後の一致などの肝心のテストが飛ばされていた。
    """
    assert SOURCE_DMN.exists(), f"元の DMN がありません: {SOURCE_DMN}"
    return SOURCE_DMN


@pytest.fixture
def committed_registry() -> Registry:
    """コミット済みの初期登録した登録簿。"""
    if not REGISTRY_PATH.exists():
        pytest.skip("registry/registry.json がありません(初期登録を実行してください)。")
    return Registry.load(REGISTRY_PATH)


# --------------------------------------------------------------------------
# 修正の宣言
# --------------------------------------------------------------------------


def test_fixes_are_declared_in_one_place_with_reason_and_approver():
    """D34: 修正は内容・理由・承認者を持ち、1か所(DMN_FIXES)にまとまっている。"""
    assert len(DMN_FIXES) == 2, "承認された修正は2件だけ"
    for fix in DMN_FIXES:
        assert fix["change"], fix["fix_id"]
        assert fix["reason"], fix["fix_id"]
        assert fix["approved_by"] == FIX_APPROVER == "Kazuhiro"
    kinds = {fix["kind"] for fix in DMN_FIXES}
    assert kinds == {"remove_cell_value", "remove_input_data"}


# --------------------------------------------------------------------------
# 修正の結果(コミット済みの登録簿・DMN から確かめる)
# --------------------------------------------------------------------------


def test_fixes_are_recorded_in_layer1_and_layer2_history(committed_registry):
    """D34: 層1・層2の history に、内容・理由・承認者が残る。"""
    entry = committed_registry.find_decision_table(decision_table_id="dt_transmission")
    fix_ids = [h["fix_id"] for h in entry["history"] if h["change"] == "dmn_fix"]
    assert fix_ids == [
        "drop_identity_result_value_from_identity_level_reference",
        "drop_unreferenced_input_data",
    ]
    for record in entry["history"]:
        assert record["approved_by"] == "Kazuhiro"
        assert record["reason"]

    # 値を直した行(tr_r3・tr_r5)には、その修正が層2にも残る
    rows = {r["row_id"]: r for r in committed_registry.rows_of("dt_transmission")}
    for row_id in ("tr_r3", "tr_r5"):
        history = [h for h in rows[row_id]["history"] if h["change"] == "dmn_fix"]
        assert len(history) == 1
        assert history[0]["row_ids"] == ["tr_r3", "tr_r5"]
    assert not [h for h in rows["tr_r4"]["history"] if h["change"] == "dmn_fix"]


def test_fixed_values_are_gone_from_the_registry_and_the_dmn(committed_registry):
    """「未」が確認レベルの参照から消え、参照されない inputData も消えている。"""
    rows = {r["row_id"]: r for r in committed_registry.rows_of("dt_transmission")}
    column = "本人確認判定.identity_level"
    assert rows["tr_r3"]["conditions"][column] == '"中","不一致検出","情報不足"'
    assert rows["tr_r5"]["conditions"][column] == '"不一致検出","情報不足"'
    dmn = FIXED_DMN.read_text(encoding="utf-8")
    assert "added by me" not in dmn
    # 本人確認判定の identity_result 側の「未」は残る(こちらは正しい値)
    assert '<text>"未"</text>' in dmn


# --------------------------------------------------------------------------
# 修正を外すと止まること(D34「修正後の登録簿に C5・C11 を適用」)
# --------------------------------------------------------------------------


def test_without_fixes_the_registry_fails_c11(source_dmn):
    """修正を外すと、登録簿への C11 で止まる。

    これが D34 の要点である。「未」は identity_result の値で identity_level には
    来ないため、残したままだと tr_r3・tr_r5 のその値は実行時に一度も当たらない。
    """
    with pytest.raises(SchemaWiringError, match="C11検証失敗\\(登録簿\\).*未"):
        build_registry(source_dmn, fixes=[])


def test_with_fixes_the_registry_passes(source_dmn):
    """修正を当てれば C5・C11 を通る(例外が出ないこと自体が検証)。"""
    result = build_registry(source_dmn)
    assert len(result.registry.decision_tables) == 3
    assert len(result.fixes) == 2


# --------------------------------------------------------------------------
# 評価結果が変わらないこと
# --------------------------------------------------------------------------


@pytest.mark.parametrize("identity_level", sorted(IDENTITY_SCENARIOS))
@pytest.mark.parametrize("sensitivity", sorted(SENSITIVITY_SCENARIOS))
@pytest.mark.parametrize("consent", CONSENTS)
def test_fix_does_not_change_evaluation(
    source_dmn, identity_level, sensitivity, consent
):
    """修正前と修正後の DMN を同じシナリオで評価し、結果が一致する。

    本人確認判定が出しうる確認レベルの全値(4件)と、送信可否判定の残りの入力
    (機微度レベル4件 × 連絡同意2件)の組み合わせで確かめる。「未」は
    identity_level に来ない値なので、消しても結果は変わらない。
    """
    facts = {
        **IDENTITY_SCENARIOS[identity_level],
        **SENSITIVITY_SCENARIOS[sensitivity],
        "contact_consent": consent,
    }
    before = DmnModel(str(source_dmn))
    after = DmnModel(str(FIXED_DMN))

    # まず、このシナリオが狙った中間判定の値になっていること
    assert before.evaluate("decision_identity", facts)["identity_level"] == identity_level
    assert before.evaluate("decision_classification", facts)["sensitivity"] == sensitivity

    expected = before.evaluate("decision_transmission", facts)
    actual = after.evaluate("decision_transmission", facts)
    assert actual == expected, f"{identity_level}/{sensitivity}/{consent}"


# --------------------------------------------------------------------------
# 再実行とソースの保護
# --------------------------------------------------------------------------


def test_bootstrap_is_reproducible(source_dmn):
    """同じ入力なら、登録簿も DMN XML もバイト単位で同じになる。"""
    first = build_registry(source_dmn)
    second = build_registry(source_dmn)
    assert first.dmn_xml == second.dmn_xml
    assert first.registry.to_json() == second.registry.to_json()
    # コミット済みのものとも一致する(初期登録を流し直す必要がない)
    assert first.dmn_xml == FIXED_DMN.read_text(encoding="utf-8")


def test_source_dmn_is_not_modified(source_dmn):
    """D34: `reference/` の元ファイルは書き換えない。"""
    build_registry(source_dmn)
    text = source_dmn.read_text(encoding="utf-8")
    assert "added by me" in text
    assert "&quot;中&quot;,&quot;未&quot;" in text


def test_remaining_problems_are_only_the_unused_outputs(source_dmn):
    """find_problems(): 修正した2件は消え、使われない出力の報告だけが残る。

    identity_result・info_class は、あおいの実行時に説明やログで使っている
    可能性があるため削除しない(HANDOFF タスク4)。
    """
    result = build_registry(source_dmn)
    assert len(result.problems) == 2
    assert all("をどの決定表も参照していない" in p for p in result.problems)
    assert any("identity_result" in p for p in result.problems)
    assert any("info_class" in p for p in result.problems)
    assert not any("added by me" in p for p in result.problems)
    assert not any("当たらない" in p for p in result.problems)


# --------------------------------------------------------------------------
# D35: 層1の表し方(確認のみ。設計書に合わせて現状どおり)
# --------------------------------------------------------------------------


def test_layer1_uses_input_names_and_first_hit_policy(committed_registry):
    """D35: depends_on_inputs は inputData の**名前**(input_columns と同じ語)。

    id を入れると input_columns・required_facts・バインディング設定と別の語に
    なり、同じものを2通りで引くことになる。hit_policy は3枚とも FIRST で、
    既存の表への行の挿入(D15)が許される形になっている。
    """
    for entry in committed_registry.decision_tables:
        assert entry["hit_policy"] == "FIRST", entry["decision_table_id"]
        facts = [c for c in entry["input_columns"] if "." not in c]
        assert entry["depends_on_inputs"] == facts, entry["decision_table_id"]
        for name in entry["depends_on_inputs"]:
            assert committed_registry.find_fact(name) is not None, name


def test_fixture_matches_the_upstream_file_when_available():
    """D42: コミットしたコピーが、あおいの既存実装側のファイルと一致する。

    `reference/` はリポジトリに入っていないので、照合できる環境でだけ行う。
    skip にするとテスト件数が環境で変わるため、無い場合は何もせず終わる。
    """
    if not UPSTREAM_DMN_PATH.exists():
        return  # reference/ が無い環境（claude.ai 側など）。照合はできないが skip にしない
    assert SOURCE_DMN.read_bytes() == UPSTREAM_DMN_PATH.read_bytes(), (
        "tests/fixtures/ のコピーが、あおいの既存実装側の DMN と食い違っています。"
        "コピーを更新し、初期登録を流し直してください。"
    )
