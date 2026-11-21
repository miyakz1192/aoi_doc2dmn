# HANDOFF の受け渡し手順（試行版）

claude.ai（作戦検討）と Claude Code（実装・実験）のあいだの受け渡しを、
Kazuhiro さんの手によるファイルのコピーではなく、このリポジトリ経由で行う。

## ファイルの置き場所と名前

これまでと同じく、リポジトリのルートに置く。

- claude.ai → Claude Code：`HANDOFF_YYYYMMDD_roundNN_to_claudecode.md`
- Claude Code → claude.ai：`HANDOFF_YYYYMMDD_roundNN_to_web.md`

## 書き込みの分担

- claude.ai が push するのは `HANDOFF_*_to_claudecode.md` と `docs/handoff_protocol.md` だけ。
  コード・テストデータ・設計書には触れない。
- それ以外は Claude Code が書く。

## Kazuhiro さんの操作

- Claude Code へ：「pull して第NN回の HANDOFF を実行して」
- claude.ai へ：「第NN回の報告を読んで」

止まる条件と報告の形式は、運用メモ（claude.ai プロジェクトの
`claude/aoi_doc2dmn_operating_rules.md`）に従う。

## 試行の記録

- 2026-11-22：claude.ai から初めて push した（このファイル）。
