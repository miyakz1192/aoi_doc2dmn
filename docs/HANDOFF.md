# aoi_doc2dmn 引き継ぎ資料

**作成日**: 2026年8月
**引き継ぎ元**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）
**引き継ぎ先**: Kazuhiroさんのサーバ環境上のClaude Code

このドキュメントは、ここまでの設計チャットで確定した事項と、リポジトリの現状、
未決事項、次のタスク候補をまとめたものです。Claude Codeにこのファイルを
読み込ませることで、チャット履歴を再現しなくても続きの実装に入れることを
意図しています。

---

## 1. 決定済み事項の要約

### 1.1 プロジェクトのスコープ

- 擬人化アーキテクチャ全体（決定表生成→AIエージェントの実行時ガバナンス）は
  スコープが大きすぎるため、直近ゴールを**「ドキュメントから決定表（DMN）を
  作る」**（抽出パイプライン①〜③、既存設計文書の呼称）に絞る
- さらにその中でも、まず**(a) ①条文抽出・関連フィルタ＋②2軸分類**の実装を
  固めることを優先する。(b) ③内容生成（決定表の中身の自動導出）は次フェーズとし、
  (a)の実装と合体させる想定
- ③以降の内容生成は、既存の`llm_vs_hardcoded_in_tests.md`（Claude Code生成の
  既存e2eテストの分析メモ）で指摘された通り、現状は完全にハードコードされており
  自動化されていない。ここが本プロジェクト最大のギャップであり、本命の作業対象

### 1.2 競合状況（参考・要約）

- IBM Decision Intelligence（旧ODM後継、Gartner MQ 2026リーダー）が
  「規程文書→決定表」を謳う最も直接的な競合。ただしローコードエディタで
  構文エラーが出る＝プログラミングができる情シス向けのUXであり、
  「業務部門が直接使える」という擬人化アーキテクチャの差別化点はまだ検証されていない
- 日本国内に「文書→DMN」特化の直接競合は見当たらない。InnoRules（BRMSベンダー）が
  「LLMでルール抽出→人間レビュー→BRMS登録」という同型の2段階パイプラインを
  業界トレンドとして言及している程度
- OSS活用方針：DMN標準まわりの周辺（dmn-js、dmnlint、Camunda ModelerのExcel⇔DMN
  変換プラグイン等）は協調領域として活用する。条文→決定表内容の抽出ロジック自体は
  既存OSSでは解決されておらず、独自実装が必要（競争領域）

### 1.3 リポジトリ構成方針

- `miyakz1192/aoi_doc2dmn`（GitHub）を新規リポジトリとして開始
- ディレクトリ構成は本チャットで提案・合意済み（下記2.1参照）
- ②の実装は**ゼロベース**で書き直す。既存のClaude Code生成コード
  （`LocalLlmAxis1Classifier`等）は`reference/`にコピーした上で、
  プロンプトの骨子・Ollama接続方法など「参考にしてよい部分」のみ移植可
- 評価基盤（オラクルとの一致率測定）をリポジトリに含め、テストデータ作成を重視する

### 1.4 テストデータ方針

- 出所は**厚生労働省モデル就業規則（令和7年12月版）**を第一段階として使用
  （手元の実データは将来的な選択肢として保留）
- カバレッジ設計：②の6セル中5セル（判断ガイド×手続き的は既存設計により
  非発生前提のため除外）＋ノイズ条文（①で弾かれるべき無関係条文）＋境界事例
  （軸1判定が割れうる条文、分類器の較正テスト用）を意図的に含める

### 1.5 空欄（`［　］`）の扱い方針【本チャットで確定】

- **背景**：本プロジェクトの本番入力スコープは就業規則・情報管理規程の**両方**
  （種別を限定しない）。ターゲット層は一人情シス・中小企業で、
  「モデル就業規則的な汎用文がそのまま残っているケース」と
  「カスタマイズ済みケース」の**両方**が実在すると想定する
- **決定**：`text_raw`（空欄あり原文）と`text_filled`（決定論的ルールで
  空欄を埋めたもの）を両方正式に保持し、`parameter_slots`メタデータを付与する。
  評価ハーネスは`--variant {raw,filled,both}`で両方を独立に測定できる
- 詳細な調査根拠は`blank_handling_report.md`（本チャット内で作成済み）を参照。
  埋め方ルール・評価時の判定基準は`testdata/schema.md`「空欄の扱い方針」節に記載

---

## 2. 現在のリポジトリ状態

### 2.1 ディレクトリ構成（実装済み）

```
aoi_doc2dmn/
├── README.md                    実行方法・現状サマリ
├── src/
│   ├── common/clause_io.py      testdata読み込み。ClauseOracle.text(variant)で
│   │                             raw/filledを切り替え可能
│   ├── stage1_filter/base.py    ①フィルタのインターフェースのみ（未実装）
│   └── stage2_classify/base.py  ②分類器のインターフェースのみ（未実装）
├── eval/
│   ├── run_eval.py              評価ハーネス。--variant {raw,filled,both}対応。
│   │                             DummyFilter/DummyAxisClassifierで動作確認済み
│   └── metrics.py               正解率・混同行列・confidence較正・raw/filled差分判定
├── testdata/
│   ├── clauses/clauses_v1.json  厚労省モデル就業規則ベース15件
│   │                             （text_raw/text_filled/parameter_slots付き）
│   └── schema.md                オラクルスキーマ定義・カバレッジ表・空欄方針
└── reference/README.md          既存Claude Code実装の移植方針メモ（中身は未コピー）
```

### 2.2 動作確認済みの内容

```bash
python3 -m eval.run_eval --variant both
```

を実行すると、ダミー実装（常に固定ラベルを返すだけ）で①②③まで一気通貫で動作し、
以下が出力されることを確認済み：

- Stage1（関連フィルタ）の accuracy/precision/recall
- Stage2軸1・軸2の strict/lenient accuracy、混同行列、confidence較正
- raw版とfilled版の差分（現状ダミー実装のため差分0、これは想定通り）

### 2.3 まだ実装されていないもの

- `src/stage1_filter/` の実装本体（`RuleBasedFilter`, `LlmFallbackFilter`,
  `CompositeFilter`）
- `src/stage2_classify/` の実装本体（`LocalLlmAxis1Classifier`,
  `LocalLlmAxis2Classifier`）
- `eval/run_eval.py`内の`build_filter()` / `build_axis1_classifier()` /
  `build_axis2_classifier()`は、まだDummy実装を返している。実装ができ次第
  差し替える

---

## 3. 未決事項リスト（実装しながら判断が必要なもの）

| # | 項目 | 内容 | 優先度 |
|---|---|---|---|
| 1 | `escalation_procedural`セルのカバレッジ不足 | 現状1件のみ。モデル就業規則（一般的な就業規則）にはこのセルの明確な条文が少ないため、業務特化規程（情報管理規程等）からのサンプル追加を検討 | 中 |
| 2 | 情報管理規程由来のテストデータが未整備 | 1.5節の通り本番スコープは就業規則・情報管理規程の両方。現行15件は就業規則のみが出典。情報管理規程は性質が異なる可能性があり（第14条のような「あおい」向け条文が典型例）、別途サンプルが必要 | 高（①②の精度を情報管理規程でも保証するために必須） |
| 3 | `reference/`への既存コード移植 | 元のClaude Code生成コード（`local_llm_strategy.py`等）の実ファイルの場所が
特定でき次第、`reference/`にコピーし、参考にする範囲を決める | 実装着手時に対応 |
| 4 | `text_filled`の埋め方ルールの妥当性 | 現状は「25日締め・翌月10日払い」等、レポート内の簡易ルールで固定。実際の分類結果への影響は`--variant both`で測定した上で、必要なら見直す | 低（測定してから判断） |
| 5 | n=15の検出力の低さ | 現状の規模では小さな精度差を検出できない（1件の変化が約9ポイントに相当）。②の実装が一定精度に達した段階で、30〜50件への拡張を検討 | 中 |

---

## 4. 次にやるべきタスクの候補

優先度が高いと思われる順（Kazuhiroさんの判断で並び替えて構いません）：

1. **`src/stage1_filter/`の実装**：ルールベース一次判定＋LLMフォールバックの
   構成（`llm_vs_hardcoded_in_tests.md` 2.2節の既存設計を踏襲）
2. **`src/stage2_classify/`の実装**：Ollama + `qwen2.5:14b-instruct`を使った
   `LocalLlmAxis1Classifier` / `LocalLlmAxis2Classifier`。プロンプトの骨子は
   ストラテジーパターン設計文書（`gijinka_extraction_pipeline_design_v1.docx`
   2.3節）に既に決定事項として記載されている
3. **`eval/run_eval.py`の`build_*()`を実装に差し替え**、実際にオラクルとの
   一致率を測定
4. **`--variant both`で空欄の影響を実測**し、`testdata/schema.md`の判定基準に
   従って結論を出す
5. 上記4の結果を踏まえて、情報管理規程由来のテストデータ追加（未決事項#2）に着手
6. `escalation_procedural`セルのカバレッジ追加（未決事項#1）

---

## 5. Claude Codeへの引き継ぎプロンプト例

サーバ環境のClaude Codeに以下のように渡すと、このドキュメントと現在の
リポジトリ状態を踏まえた続きの作業に入りやすいと思います。

```
このリポジトリ（aoi_doc2dmn）で、擬人化アーキテクチャの抽出パイプライン
①条文抽出・関連フィルタ、②2軸分類の実装を進めています。

まず HANDOFF.md を読み、これまでの設計チャットでの決定事項・現在のリポジトリ
状態・未決事項を把握してください。次に testdata/schema.md と
eval/run_eval.py を読んで、既存の評価ハーネスの動作を理解してください
（python3 -m eval.run_eval --variant both で実行できます。現状はダミー実装
なので、まずこれを動かして仕組みを確認してください）。

その上で、まず src/stage2_classify/ に LocalLlmAxis1Classifier と
LocalLlmAxis2Classifier を実装してください。設計方針は
src/stage2_classify/base.py のTODOコメント、および HANDOFF.md 4節の
「次にやるべきタスクの候補」#2を参照してください。Ollamaは
localhost:11434 で qwen2.5:14b-instruct が動作している前提です。

実装ができたら eval/run_eval.py の build_axis1_classifier() /
build_axis2_classifier() をDummy実装から差し替えて、
python3 -m eval.run_eval --variant both を実行し、結果を見せてください。
```

---

## 6. このドキュメントの位置づけ

このドキュメントおよび`testdata/schema.md`「空欄の扱い方針」節、
`blank_handling_report.md`は、claude.aiでの設計チャットの決定事項を
Claude Code環境に引き継ぐための静的なスナップショットです。今後
Claude Code側での実装・議論で決定事項が更新された場合、このファイルは
その時点の情報のまま残るため、更新履歴の管理（このファイル自体を
version管理するか、決定事項をリポジトリ内の別の場所に集約するか）は
Kazuhiroさんの既存の「チャット境界の明示的管理」の運用に委ねます。
