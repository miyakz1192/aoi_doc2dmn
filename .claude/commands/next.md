---
description: 最新の HANDOFF_*_to_claudecode.md を pull して実行し、報告を push する
---

次の手順で作業する。

1. **`git pull` する。**
2. **まだ実行していない最新の `HANDOFF_*_to_claudecode.md` を読む。**
   - 日付とラウンド番号が一番新しいものを見る。
   - 実行済みかどうかは、対応する `HANDOFF_*_to_web.md`（同じラウンド番号への回答）が
     あるかと、git log で判断する。迷ったら Kazuhiro さんに確認する。
3. **その HANDOFF の指示に従って作業する。**
   - 進め方は運用メモ `docs/design/aoi_doc2dmn_operating_rules.md` に従う。
   - 1回の HANDOFF の中で、同じ作業の再試行は2回まで。
4. **運用メモの止まる条件（S1〜S5）に当たったら、そこで止まる。**
5. **報告を `HANDOFF_<日付>_round<NN>_to_web.md` として書く。**
   - 冒頭は運用メモ 5章の3行（分かったこと／次にすること／判断が要る点）にする。
   - 判断が要る点は3区分（既定採用／一目確認／要判断）に分ける。迷ったら重い側に入れる。
6. **コミットして push する。**
   - テストを流し、件数を報告に書く（`.venv/bin/python3 -m pytest tests/`）。

受け渡しの分担は `docs/handoff_protocol.md` にある。`git pull` で衝突が起きたら、
自分では解決せず、報告して止まる。
