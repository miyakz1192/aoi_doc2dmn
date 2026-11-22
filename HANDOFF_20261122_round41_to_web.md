# aoi_doc2dmn 引き継ぎ資料（第41回）

**作成日**: 2026年11月22日
**引き継ぎ元**: Kazuhiroさんのサーバ環境上の Claude Code
**引き継ぎ先**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）

```
分かったこと：__D_HEADLINE__
次にすること：新しい保留評価（18件）を1回だけ流す。Kazuhiroさんの「はい」待ちで止まっている。
判断が要る点：要判断 0件／一目確認 __N_CHECK__件（既定採用 7件は末尾に記録）
```

**位置づけ**: 第40回（`HANDOFF_20261122_round40_to_claudecode.md`、コミット 50ed097）への回答。パートA〜F を実施し、運用メモ 4章の **S2（1回しか流せない評価の実行の直前）** で止まっている。保留評価は実行していない。

これは、HANDOFF をリポジトリ経由で受け渡す仕組みで回した初めての回である（`docs/handoff_protocol.md`）。`/next` で pull → 第40回の指示を実行 → この報告を push した。

---

## 1. 判断が要る点

要判断は0件。一目確認が __N_CHECK__件ある。

__CHECK_TABLE__

---

## 2. パートA：同義語の判定の寄せ先を具体の段だけにした（P31）

`src/stage3_frontstage/action_normalizer.py`：

```python
def concrete_entries(ledger: ActionLedger) -> list[ActionEntry]:
    """同義語の判定(手順3c)に渡す候補は、具体の段の Action だけ(P31)。"""
    return [e for e in ledger.entries if e.kind == ACTION_CONCRETE]
```

- `_judge_synonym` は `concrete_entries(ledger)` だけを `offered` と `_verb_block` に渡す。**抽象の段は名前も過去の動詞も渡さない。**
- 渡していない名前が返ってきたら「該当なし」として扱い、手順4（新規候補）へ進める。理由の文に、台帳の抽象の段のものか台帳の外のものかを書き分けて残す。
- 件数は `SynonymJudgment.off_list` → `ItemResult.synonym_off_list` → 集計の `synonym_off_list`（とログの1行）に出る。
- **手順3a は変えていない。**抽象の Action の過去の動詞と一致すれば、これまでどおり抽象に寄り、`action_abstract_selected` が立つ。
- **プロンプトの文面（`SYNONYM_PROMPT`）は変えていない**（指示のとおり。第39回で文面の直しは効かないと分かったため）。

単体テスト（3件追加）:

| テスト | 確かめること |
|---|---|
| `test_the_synonym_candidates_never_include_an_abstract_action` | 3c の候補の一覧に抽象の段が入らない（具体の段は入る）。`offered` にも入らない |
| `test_an_abstract_name_returned_by_the_synonym_step_goes_to_a_new_action` | 渡していない抽象の名前が返ったら `off_list=True`・寄せ方は「4」・新規候補になる |
| `test_abstract_selected_raises_the_gate`（書き直し） | 3a で抽象の過去の動詞と一致したら、LLM 1回で抽象に寄り `action_abstract_selected` が立つ（変わっていないことの確認） |

既存のテスト3件は、3c で抽象に寄る道が無くなったので、**人が同義語を承認して台帳の過去の語句に足した状態**（P30 の想定）から 3a で抽象に寄る形に書き直した。

## 3. パートB：新規の取り違えを中心の動詞で比べる（P32）

`src/stage3_frontstage/run_action_eval.py`：

```python
def _matches(expected, chosen, is_new, core_verb=""):
    if is_new:
        if not expected["is_new"]:
            return False
        target = expected.get("core_verb") or expected["normalized"]
        return core_verbs_match(core_verb or chosen, [target])[0]
    return (not expected["is_new"]) and chosen == expected["normalized"]
```

```python
    if is_new:
        if any(not exp["is_new"] for exp in options):
            return "missed"        # 正解に既存があるのに新規にした
        return "new_mismatch"      # 正解も全部新規だが、中心の動詞が合わない
```

- 比べるのは**前段が抜き出した中心の動詞**（`trace.run.candidates[0].core_verb`）と、正解・並びの `core_verb`。名前は比べない。
- 分類し直し（`recompute_metrics`）では Trace が無いので、生データの `actual.core_verb` を渡す。

### 分類し直した結果（LLM は回していない）

| 評価 | 件数 | 正しい | 誤統合 | 適用漏れ | 過剰適用 | 取りこぼし | 新規の取り違え | すり抜け |
|---|---|---|---|---|---|---|---|---|
| 第32回（開発用・v0.4、v3 の正解） | 18 | 13→**12** | 2 | 0 | 2 | 1 | 0→**1** | 0 |
| 第32回（参考：v4 の正解で） | 18 | **13** | 2 | 0 | 2 | 1 | **0** | 0 |
| 第37回（旧保留用・v0.6） | 14 | 5 | 8 | 0 | 0 | 0→**1** | 1→**0** | 0 |
| 第39回 v4（開発用・v0.7） | 18 | **12** | 2 | 1 | 2 | 1 | 0 | 0 |
| 第39回 dev2（旧保留用・v0.7） | 14 | 10→**12** | 1 | 0 | 0 | 0→**1** | 3→**0** | 0 |

変わった項目は4件だけで、どれも想定どおりだった。

| 評価 | 項目 | 変化 | 理由 |
|---|---|---|---|
| dev2 | 13 `mhlw_r7dec_art11_3` | 新規の取り違え → **正しい** | 出力「離脱」／正解「離席」。抜き出した動詞「離れる」が正解の中心の動詞「離れ」と一致（名前の違いだけだった） |
| dev2 | 15 `mhlw_r7dec_art58_3_5` | 新規の取り違え → **正しい** | 出力「連行」／正解「同伴」。動詞「連れて行く」と「連れて行」が一致 |
| dev2 | 10 `ipa_secguide_v4_5_6_outdoor` | 新規の取り違え → **取りこぼし** | 正解に既存の「送信」があるので取りこぼしに数える（指示のとおり） |
| 第37回 | 10 同じ条文 | 正しい（v0.6）→ **取りこぼし** | 同上。v0.7 の数え方では「新規の取り違え」だった |

**dev2 の「正しい」は 10件 → 12件**で、web Claude の見込みと一致した。

**第32回は、当時の正解（v3）で数え直すと1件減る。**項目4（`mhlw_r7dec_art21_3`、第21条第3項）は正解も出力も「労働指示」だが、**v3 の正解の中心の動詞が「従事」**（使役を付ける直しは v4 のタスクC1 で入れた）、**出力は「従事させ」**なので、P28 で不一致になる。P28 より前の正解データであることによる見かけの1件で、**v4 の正解で数え直すと「正しい」に戻る**（参考の行）。両方のファイルを残した（K26）。

ファイル：

| ファイル | 内容 |
|---|---|
| `docs/eval/action_eval_20261108_reclassified_v08.json` | 第32回（v3 の正解） |
| `docs/eval/action_eval_20261108_reclassified_v08_with_v4.json` | 第32回（参考：v4 の正解） |
| `docs/eval/action_eval_holdout_20261121_reclassified_v08.json` | 第37回 |
| `docs/eval/action_eval_v4_20261121_reclassified_v08.json` | 第39回 v4 |
| `docs/eval/action_eval_dev2_20261121_reclassified_v08.json` | 第39回 dev2 |

## 4. パートC：新しい保留用の正解ファイル

| ファイル | 内容 |
|---|---|
| `testdata/action_gold/action_gold_holdout_v2.json` | 18件。`split: holdout`／`status: approved`／全件 `approved_by: "Kazuhiro"`・`reviewed_at: "2026-11-22"` |
| `testdata/clauses/clauses_holdout_v2.json` | 候補30件すべての軸1・軸2のラベル。`in_action_eval` で評価に入れる18件を示す（K23） |

- **値は付録B 表1 のとおりに写した。**18件・並び4件・新規の Action 14件（全部 `kind: concrete`・`parent: null`）・定義は `status: provisional`、`source` は「第40回 HANDOFF 付録B（Kazuhiro 承認 2026-11-22）」。
- `order` と評価の順は `candidates_round4.json` の並び順（評価に入れる18件だけ）。
- **surface は18件すべてが条文の逐語**で、様相の語を含まず、中心の動詞を含む（プログラムで確認）。
- `_meta.excluded` に外す12件と理由（付録B 表2）。`_meta.review_process` に2案の一致率（30件中20件）・案A（Opus）・案B（サブエージェント Sonnet）・乱数の種（20261122）。
- **出発点の台帳は21件**で、v4（18件）→ dev2（14件）を教え終えた状態から作り直したものと一致する（テストで確認）。抽象は提供と取得の2つ。
- **オラクル（`--use-oracle`）で 18/18・すり抜け0件**（`docs/eval/action_eval_holdout_v2_oracle_20261122.json`）。寄せ方は 3a 5件／3c 1件／新規候補12件、P29 は4件で当たった。
- 承認の欄や正解の値が欠けていれば `run_action_eval` が止まることも、テストで確認した。

単体テスト（12件追加）：split と承認・候補の並び順・逐語・surface の3条件・新規の定義の形・台帳21件の作り直し・抽象2件と親子・`ledger_source: seed`・承認が欠けたら止まる・2案の記録・30件のラベル・外す12件の理由とラベルの一致。

__PART_D__

## 6. パートE：保留評価の準備（実行していない）

**ここで止まっている（運用メモ S2）。** 新しい保留評価（18件）は、Kazuhiroさんの「はい」で1回だけ流す。

### 6.1 凍結コミット

| 項目 | 値 |
|---|---|
| 凍結コミット | **`__FROZEN__`** |
| 内容 | 前段 v0.8（P31・P32）の実装、`action_gold_holdout_v2.json`（18件）、`clauses_holdout_v2.json`（30件） |
| このコミットで流す理由 | パートD の結果を出したコードと同じものである（これ以降のコミットは報告・記録・設計書だけで、`src/` と `testdata/` を変えていない） |

### 6.2 実行コマンド（そのまま貼れる形）

```bash
cd /home/miyakz/aoi_doc2dmn
git log --oneline -1                      # __FROZEN__ であることを確かめる
.venv/bin/python3 -m pytest tests/ -q     # 全件パスを確かめる

# 保留評価（1回だけ。約50〜60分）
.venv/bin/python3 -m src.stage3_frontstage.run_action_eval \
  --gold testdata/action_gold/action_gold_holdout_v2.json \
  --out out/action_eval_holdout_v2
```

### 6.3 実行前の確認項目（第36回と同じ形）

| # | 確認すること | 状態 |
|---|---|---|
| 1 | 凍結コミットのハッシュ | `__FROZEN__`。`git status` はきれい |
| 2 | 承認の欄が18件すべてに入っている | 入っている（`approved_by: "Kazuhiro"`・`reviewed_at: "2026-11-22"`）。欠けていれば `run_action_eval` が `HoldoutNotApproved` で止まることをテストで確認済み |
| 3 | オラクルで 18/18・すり抜け0件 | 確認済み（4章） |
| 4 | 入力と出力 | 入力 `testdata/action_gold/action_gold_holdout_v2.json`、出力先 `out/action_eval_holdout_v2/` |
| 5 | 正解ファイルと候補ファイルを、評価の前後で変えない | `action_gold_holdout_v2.json`・`clauses_holdout_v2.json`・`candidates_round4.json` は触らない |
| 6 | 結果を見てからプロンプト・規則・正解を直さない（P20） | 直さない。合格の条件は「すり抜け0件」（P9）だけ |
| 7 | 中断したときの扱い | 凍結コミットのまま、最初から通し直す（第37回 K10 と同じ）。中断分は記録として残す |
| 8 | モデルと条件 | `qwen2.5:14b-instruct`・k=1・temperature 0.0・教師あり（第39回と同じ） |
| 9 | 所要時間の見込み | 1件あたり 120〜160秒 × 18件 ＝ **約40〜50分** |

### 6.4 流したあとに報告すること

第39回と同じ形（項目ごとの表・集計・比較・すり抜けの1件ずつ・3c の誤り）に加えて、**正しい割合が v0.6 の保留評価（14件中5件）より上がったか**を書く（設計書 9章の次の作業の条件）。

## 7. パートF：設計書と記録

| ファイル | 変更 |
|---|---|
| `docs/design/aoi_doc2dmn_frontstage_design.md` | **v0.8 に改訂**（付録A のとおり）。版と改訂日、前提とする実装（コミット 88b0f30）、2章に P31・P32、4.5節のゲートの表（`action_verb_mismatch` に「v0.7 で廃止」、`action_synonym_merge` の行を追加、設計上の確認に追加）、4.7節の 3c の行と P31 の段落、5.1節の新規の取り違え、**5.5節（新設）**新しい保留評価セット、**7.5節（新設）**第39回の分析、8章 PU12、9章 |
| `docs/design/aoi_doc2dmn_operating_rules.md` | 7章に第40回の判断6件（要判断1・2、Q2'、Q3、Q4、第39回 Q1）と、この回の既定採用7件（K21〜K27）を足した |
| `README.md` | v0.8 の開発用32件の結果を記録 |

Word 版は v0.7 のままなので、Markdown 版が v0.8 の正本であることを冒頭の注記に書いた。

## 8. 既定採用の一覧

| # | 内容 | 根拠 |
|---|---|---|
| K21 | 保留用の正解の定義の根拠の語句（`definition.basis`）には surface をそのまま入れた | 付録B は根拠の語句を指定していない。surface は条文の逐語なので `basis: "verbatim"` で足りる |
| K22 | 判断ガイド × 手続き的の `cell_id` を `guide_procedural` にした | 既存の命名（`hard_declarative`・`guide_declarative` など）の流れ |
| K23 | `clauses_holdout_v2.json` の各条文に `in_action_eval` を足し、評価に入れる18件を示した | 30件すべてにラベルを入れる指示と、18件だけを評価に使う指示を1つのファイルで表すため |
| K24 | 保留用の正解に `context` は付けていない | 付録B が指定していない（旧保留用は第33回追補が3件を指定していた） |
| K25 | `action_gold_holdout_v2.json` も `_meta.ledger_source: "seed"` にした | 出発点の21件は登録簿とは別物（第39回 K15 と同じ） |
| K26 | 第32回の分類し直しは当時の正解（v3）で行い、v4 の正解での結果も参考として別ファイルに残した | v3 の中心の動詞は P28 より前のもので、見かけの「新規の取り違え」1件が出るため（3章） |
| K27 | v0.8 の結果ファイルは `..._20261122_v08.json`、分類し直しは `..._reclassified_v08.json` にした | 第39回 K19 と同じ付け方 |

## 9. push したコミットと、テストの件数

| ハッシュ | 内容 |
|---|---|
| `50ed097` | 第40回 HANDOFF（**web Claude が push**） |
| `fda0722` | 第40回 パートA〜C・F1・F2：前段 v0.8（P31・P32）と新しい保留用の正解18件 |
__HASHES__

- **テストの件数：__TESTS__件パス**（第39回は344件）。実行は `.venv/bin/python3 -m pytest tests/`。

## 10. 止まった理由

運用メモ 4章の **S2（1回しか流せない評価を実行する直前）**。パートE の確認項目をそろえたので、Kazuhiroさんの「はい」で実行する。再試行の上限には達していない（__RETRY__）。
