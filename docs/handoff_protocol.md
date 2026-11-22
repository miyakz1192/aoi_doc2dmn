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

## 決定事項（2026-11-22）

### 書き込みの分担（上の節の確定）

- **claude.ai は main に直接 push してよい。** ブランチを切らなくてよい。
- ただし claude.ai が push してよいファイルは、次の2つだけとする。
  - `HANDOFF_*_to_claudecode.md`
  - `docs/handoff_protocol.md`（本書）
- コード・テストデータ・設計書・運用メモ・評価の結果には触れない。

### Claude Code の手順

1. **作業を始める前に必ず `git pull` する。**
2. まだ実行していない最新の `HANDOFF_*_to_claudecode.md` を読み、その指示に従って作業する。
3. 作業が終わったら、報告を `HANDOFF_*_to_web.md` として書き、**コミットして push する**。
4. 運用メモの止まる条件（S1〜S5）に当たったら、そこで止まって報告する。

この手順は、カスタムコマンド **`/next-code`**（`.claude/commands/next-code.md`）として置いてある。

### どちらで何を打つか（2026-11-22 に整理）

`/next` という名前は、claude.ai 側のスキル `aoi-next`（アカウントに同期されている）が
使っている。中身は作戦検討者の手順で、Claude Code 側の手順とは役割が逆である。
同じ名前で役割が違うと取り違えるので、名前を分けた。

| 打つ場所 | コマンド | すること |
|---|---|---|
| claude.ai（作戦検討） | `/next` （スキル `aoi-next`） | 最新の報告（`HANDOFF_*_to_web.md`）を読み、判断が要る点を出す。判断を受けたら次の `HANDOFF_*_to_claudecode.md` を main に push する |
| Claude Code（実装・実験） | **`/next-code`** | `git pull` し、最新の `HANDOFF_*_to_claudecode.md` を実行し、報告を `HANDOFF_*_to_web.md` として push する |

Claude Code のセッションで `/next` を打つと、同期済みスキルの方が起動する。その手順の
「番号が一番大きいのが `_to_claudecode.md` なら Claude Code の作業待ち」という分岐に
当たるので、そこからこの `/next-code` の手順に移ればよい。

### 衝突が起きたとき

- 両者が同じファイルを編集しないので、**衝突は起きない想定**である。
- もし起きたら、**Claude Code は自分では解決せず、報告して止まる**（運用メモ 4章と同じ扱い）。
  どちらの変更を採るかは Kazuhiro さんの判断とする。

### 注記

- 運用メモは、リポジトリでは `docs/design/aoi_doc2dmn_operating_rules.md` に置いてある
  （上の「止まる条件と報告の形式」の節にある `claude/aoi_doc2dmn_operating_rules.md` は
  claude.ai プロジェクト側の置き場所）。
