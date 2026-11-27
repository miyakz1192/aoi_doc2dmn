# aoi_doc2dmn 引き継ぎ資料（第52回）

**作成日**: 2026年11月28日
**引き継ぎ元**: Claude（claude.ai チャット、擬人化アーキテクチャ プロジェクト）
**引き継ぎ先**: Kazuhiroさんのサーバ環境上の Claude Code

## 位置づけ

第51回の報告（`HANDOFF_20261127_round51_to_web.md`）への回答。**事実の抽出の開発用20件の正解を Kazuhiro さんが承認した**ので、正解ファイルを作り、手順1 のプロンプトを（結果を見る前に）直し、開発用25件で評価する。

保留用の候補20件（`candidates_round5.json`）は流さない。

## Kazuhiro さんの判断

### 第51回の報告への判断（2026年11月27日）

第51回の要判断1・2 は、戻しやすい数え方・照合の細部なので、web Claude が一目確認に下げた。

| # | 判断 |
|---|---|
| Q1（旧 要判断1） | はい：別の行為の fact と値が一致して立った fact_possible_duplicate は、疑いの誤報に数えず、別枠（寄せ先の確認）で数える。ゲートは立てたまま |
| Q2（旧 要判断2） | はい：手順3b の答えを並び順で当てるのは「該当なし」のときだけ（K71 のまま） |
| Q3〜Q6 | はい：K60（中間判定の fact も範囲）・K61（層1・層2も教える）・K62（表記の違いの3条件）・K72（生の答えの1回の診断） |
| Q7 | はい：**比較型の候補には K50（条文に無い値を落とす歯止め）を当てない**。比較型の値はプリミティブの定義で決まる（D59） |

第51回の既定採用 K60〜K72 は、そのまま採用。

### 正解の承認（2026年11月28日）

事実の抽出の開発用20件の正解（付録）を、**推奨どおり承認**した。作り方は運用メモ 3章（案A＝web Claude（Opus）、案B＝サブエージェント（Sonnet）。構造が一致9件・表記だけの違い2件・食い違い9件。乱数の種 20261127 で一致した件から3件を精読）。食い違い9件のうち、案B を採ったのは5件（暗号化の値・保存の条件・20歳未満の列・Wi-Fi の対象と契約・パスワードの使い回しの展開）。

## 守ること（毎回）

- **運用メモ（`docs/design/aoi_doc2dmn_operating_rules.md`）4章の止まる条件に従う。** この回で当たりうるのは次の3つ。
  - **S1**：付録の正解に誤りがあると思われる件、または付録のとおりに書けない件が見つかった。**自分では直さず**報告して止まる。
  - **S3**：パートD の評価で、P40 の合格の条件で2回続けて不合格になった。
  - **S4**：確定済みの D・P 番号と食い違う実装が必要になった。黙って変えず、要判断として報告して止まる。
- **1回の HANDOFF の中で、同じ作業の再試行は2回まで。** 超えたら報告して止まる。
- **手順1 のプロンプトの直し（パートC）は、開発用25件の結果を見る前に1回だけ行う。** 直した後に25件を流し、その結果を見てからの直しは、パートD の再試行（2回まで）として数える。
- **報告は `HANDOFF_20261128_round53_to_web.md` に書き、コミットして push する。** 冒頭は次の3行の形式にする（運用メモ 5章）。本文は、要判断 → 一目確認 → 中身 → 既定採用の一覧の順に並べる。

```
分かったこと：（1行）
次にすること：（1行）
判断が要る点：要判断 N件／一目確認 N件（既定採用 N件は末尾に記録）
```

---

## パートA：小さな直し（Q1・Q7）

1. Q1：別の行為の fact と値が一致したことで立った fact_possible_duplicate を、誤報の集計から外し、別枠（寄せ先の確認）で数える。
2. Q7：比較型の候補には K50 を当てない。比較型の候補は、値なしで IR の new_facts に入る（D59・D62）。第14条で、生年月日・連絡先の比較型の候補が IR に入ることを単体テストで確かめる。

**完了の条件**：テストが通る。

## パートB：開発用20件の正解ファイルを作る

1. `testdata/fact_gold/fact_gold_dev_v1.json`（名前は任意）を作る。正解 IR 5件（manifest の順）に続けて、付録の20件をこの順に並べる。`split: dev`、20件すべてに `review: {approved_by: "Kazuhiro", reviewed_at: "2026-11-28"}`。
2. 20件の正解は、付録の JSON のとおりに写す（fact の名前・既存か新規か・種類・値・根拠の語句・値の追加かどうか・other_value_note・列にしない条件）。行為（Action）は Action の開発用の正解（v4・dev2・dev3）のとおり。
3. 教師ありの評価では、各件の後で正解の fact を層3に足し、**層1・層2も教える**（K61）。この20件は正解 IR を持たないので、その件の行為の表がその fact を入力に使うことを、配線として足す（決定表の中身は作らない）。足し方を報告に書く。
4. 逐語のテスト：根拠の語句が条文か context にあること。値が条文の語のままでないもの（並列の展開・全角→半角・「暗号化する」のような語の切り出し）は、テストの例外の一覧に理由と一緒に書く。
5. オラクルで流し、上限と、正解どおりでも取りこぼしになる件（別の行為の列の再利用など。P37 改訂）の一覧を出す。

**完了の条件**：テストが通り、オラクルの上限と取りこぼしの一覧が報告にある。

## パートC：手順1 のプロンプトを直す（結果を見る前に1回だけ）

**目的**：正解 IR 5件で見えた課題（前段設計書 10.7節）を直す。

1. **並列の片方を落とす**：「妊娠中の女性、産後１年を経過しない女性労働者」「時間外労働、休日労働、深夜労働」の型。並列の各項を別の値として必ず出すことを、プロンプトの規則と例で示す。
2. **行為のきっかけを条件にする**：「問い合わせに対し」の型。行為の相手やきっかけ（〜に対し、〜の際に）は、規範の条件でなければ条件にしないことを示す。
3. 直した内容と、直した理由（どの件のどの誤り）を報告に書く。**付録の20件の条文や値をプロンプトの例に使わない**（評価の対象を例にすると、測れなくなる）。

**完了の条件**：正解 IR 5件での単体テスト（モックの LLM）が通る。

## パートD：開発用25件で評価する

1. 第50回と同じ条件（`qwen2.5:14b-instruct`・k=1・temperature 0.0・教師あり）で、25件（正解 IR 5件＋付録の20件）を流す。所要は1件あたり約280秒、計2時間ほどの見込み。
2. 第50回と同じ形の1件ごとの表と集計を出す。参考指標（重大な誤りで疑いのゲートが1つも立たなかった件）、提案の当たり・誤報（列の単位）、別の行為の一致の件数を必ず入れる。正解 IR 5件の部分は、第50回と並べる。
3. 不合格（P40）なら原因を書いて直してよい（2回まで）。
4. 結果を見てからの直しは、どの件を見てどう直したかを必ず書く（開発用なので直してよいが、後で保留用で測るときの記録になる）。

**完了の条件**：結果を `docs/eval/fact_eval_dev25_20261128_v013.json`（とログ）に残す。

## パートE：記録

1. 運用メモ 7章に、この回の判断と既定採用を足す。
2. README に、開発用25件の結果を書く。

---

## 付録：事実の抽出の開発用20件の正解（Kazuhiro さん承認、2026年11月28日）

評価の順。出発点の層3は、初期登録の7件＋正解 IR 5件の fact。「既存」は、その時点の層3にある fact（前の件で新規に足したものを含む）。「（追加）」は既存の fact に足す値（value_additions）。

| # | 条文 | Action | 正解の fact（名前（既存/新規・種類）［値］） | 列にしない条件 |
|---|---|---|---|---|
| 1 | ipa_secguide_v4_6_1_5 | 操作 | requested_privilege（既存・状況）［特権］<br>authentication_method（新規・状況）［多要素認証］　その他＝多要素認証以外の認証<br>device_ownership（新規・状況）［社有機器・私有機器］　その他＝社有機器・私有機器以外の機器<br>access_route（新規・状況）［指定経路］　その他＝指定経路以外の経路 | — |
| 2 | ipa_secguide_v4_phys_1_2_level2 | 操作 | operation_target（新規・対象）［情報機器・設備］　その他＝情報機器・設備以外<br>operation_authorization（新規・状態）［無断］　その他＝許可を得ている<br>area_level（新規・状況）［レベル2領域］　その他＝レベル2領域以外の場所 | — |
| 3 | ipa_secguide_v4_2_3_3_forward | 送信 | send_target（新規・対象）［社用メールアドレスで受信したメール］　その他＝それ以外のデータ<br>destination（新規・状況）［従業員個人のアドレス］　その他＝従業員個人のアドレス以外の宛先 | — |
| 4 | ipa_secguide_v4_2_3_3_send | 送信 | send_target（既存・対象）［社内で利用したデータ（追加）］<br>destination（既存・状況）［従業員個人のアドレス］ | — |
| 5 | ipa_secguide_v4_5_6_outdoor | 送信 | location（新規・状況）［屋外］　その他＝屋外以外の場所<br>information_classification（新規・対象）［極秘・社外秘］　その他＝極秘・社外秘以外の区分<br>encryption_status（新規・状態）［暗号化する］　その他＝暗号化していない | — |
| 6 | ipa_secguide_v4_phys_3_printer | 放置 | leave_place（新規・状況）［複合機・プリンタ］　その他＝複合機・プリンタ以外の場所<br>leave_target（新規・対象）［原稿・印刷物］　その他＝原稿・印刷物以外の物 | — |
| 7 | ipa_secguide_v4_phys_1_2_level1 | 放置 | usage_state（新規・状態）［未使用時］　その他＝使用中<br>information_classification（既存・対象）［社外秘・極秘］<br>area_level（既存・状況）［レベル1領域（追加）］ | — |
| 8 | ipa_secguide_v4_personnel_6_4_4_leave | 放置 | leave_target（既存・対象）［機器（追加）・書類（追加）］ | 目の届く範囲に置き |
| 9 | ipa_secguide_v4_2_3_3_store | 保存 | information_classification（既存・対象）［社外秘・極秘］<br>storage_medium（新規・状況）［IT機器・電子媒体］　その他＝IT機器・電子媒体以外の保存先<br>usage_period（新規・状況）［利用期間中］　その他＝利用期間外 | 以下に該当する機能がある場合には実行する |
| 10 | mhlw_r7dec_art58_3_6 | 立入り | area_type（新規・状況）［立入禁止区域・通行禁止区域］　その他＝それ以外の区域 | — |
| 11 | mhlw_r7dec_art58_3_4 | 立入り | entrant_age（新規・主体の属性）［20歳未満の者］　その他＝20歳以上の者<br>area_type（既存・状況）［喫煙可能な場所（追加）］ | — |
| 12 | mhlw_r7dec_art58_3_5 | 同伴 | companion_attribute（新規・対象）［受動喫煙を望まない者］　その他＝受動喫煙を望まない者以外<br>area_type（既存・状況）［喫煙可能な場所］ | — |
| 13 | mhlw_r7dec_art25_2 | 労働指示 | worker_category（既存・主体の属性）［産後8週間を経過していない女性労働者（追加）］ | — |
| 14 | mhlw_r7dec_art21_4 | 労働指示 | worker_category（既存・主体の属性）［妊産婦（追加）］<br>exemption_request（既存・状態）［請求のあった（追加）］<br>work_type（既存・対象）［所定労働時間外労働（追加）・休日労働］ | 災害その他避けることのできない事由によって臨時の必要がある場合 |
| 15 | ipa_secguide_v4_2_3_3_wifi | 使用 | usage_target（既存・対象）［Wi-Fiサービス（追加）］<br>service_contract（新規・状態）［会社で契約したサービス社外］　その他＝会社契約のサービス | — |
| 16 | ipa_secguide_v4_6_10_password_reuse | 使用 | usage_target（既存・対象）［パスワード（追加）］<br>reuse_scope（新規・状況）［機器間・サービス間］　その他＝使い回しなし | — |
| 17 | ipa_secguide_v4_personnel_6_4_2_cloud | 使用 | usage_purpose（既存・状況）［送受信（追加）・保存（追加）・共有（追加）］<br>handled_data（新規・対象）［業務関連データ］　その他＝業務関連データ以外 | — |
| 18 | ipa_secguide_v4_6_10_priv_name | 共有 | share_target（新規・対象）［特権アカウント名］　その他＝特権アカウント名以外<br>sharer_count（新規・状況）［2名以上］　その他＝1名 | — |
| 19 | ipa_secguide_v4_6_2_1_item2 | 共有 | share_target（既存・対象）［同一アカウント（追加）］<br>share_partner（新規・対象）［他者］　その他＝本人 | — |
| 20 | ipa_secguide_v4_5_6_share | 共有 | information_classification（既存・対象）［社外秘・極秘］<br>share_target（既存・対象）［情報ファイル（追加）］<br>share_scope（新規・状況）［社外］　その他＝社内<br>share_partner（既存・対象）［秘密保持に関する合意をした相手（追加）］ | — |

正解の値・根拠の語句（quote）・値の追加かどうかは、次の JSON が正本である。

```json
[
 {
  "order": 1,
  "id": "ipa_secguide_v4_6_1_5",
  "action": "操作",
  "facts": [
   {
    "variable": "requested_privilege",
    "status": "existing",
    "kind": "状況",
    "values": [
     {
      "value": "特権",
      "quote": "特権操作",
      "is_addition": false
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "authentication_method",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "多要素認証",
      "quote": "多要素認証",
      "is_addition": false
     }
    ],
    "other_value_note": "多要素認証以外の認証"
   },
   {
    "variable": "device_ownership",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "社有機器",
      "quote": "社有機器",
      "is_addition": false
     },
     {
      "value": "私有機器",
      "quote": "私有機器",
      "is_addition": false
     }
    ],
    "other_value_note": "社有機器・私有機器以外の機器"
   },
   {
    "variable": "access_route",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "指定経路",
      "quote": "指定経路",
      "is_addition": false
     }
    ],
    "other_value_note": "指定経路以外の経路"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 2,
  "id": "ipa_secguide_v4_phys_1_2_level2",
  "action": "操作",
  "facts": [
   {
    "variable": "operation_target",
    "status": "new",
    "kind": "対象",
    "values": [
     {
      "value": "情報機器",
      "quote": "情報機器・設備",
      "is_addition": false
     },
     {
      "value": "設備",
      "quote": "情報機器・設備",
      "is_addition": false
     }
    ],
    "other_value_note": "情報機器・設備以外"
   },
   {
    "variable": "operation_authorization",
    "status": "new",
    "kind": "状態",
    "values": [
     {
      "value": "無断",
      "quote": "無断操作",
      "is_addition": false
     }
    ],
    "other_value_note": "許可を得ている"
   },
   {
    "variable": "area_level",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "レベル2領域",
      "quote": "レベル2領域",
      "is_addition": false
     }
    ],
    "other_value_note": "レベル2領域以外の場所"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 3,
  "id": "ipa_secguide_v4_2_3_3_forward",
  "action": "送信",
  "facts": [
   {
    "variable": "send_target",
    "status": "new",
    "kind": "対象",
    "values": [
     {
      "value": "社用メールアドレスで受信したメール",
      "quote": "社用メールアドレスで受信したメール",
      "is_addition": false
     }
    ],
    "other_value_note": "それ以外のデータ"
   },
   {
    "variable": "destination",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "従業員個人のアドレス",
      "quote": "従業員個人のアドレス",
      "is_addition": false
     }
    ],
    "other_value_note": "従業員個人のアドレス以外の宛先"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 4,
  "id": "ipa_secguide_v4_2_3_3_send",
  "action": "送信",
  "facts": [
   {
    "variable": "send_target",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "社内で利用したデータ",
      "quote": "社内で利用したデータ",
      "is_addition": true
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "destination",
    "status": "existing",
    "kind": "状況",
    "values": [
     {
      "value": "従業員個人のアドレス",
      "quote": "従業員個人のアドレス",
      "is_addition": false
     }
    ],
    "other_value_note": null
   }
  ],
  "not_modeled": []
 },
 {
  "order": 5,
  "id": "ipa_secguide_v4_5_6_outdoor",
  "action": "送信",
  "facts": [
   {
    "variable": "location",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "屋外",
      "quote": "屋外",
      "is_addition": false
     }
    ],
    "other_value_note": "屋外以外の場所"
   },
   {
    "variable": "information_classification",
    "status": "new",
    "kind": "対象",
    "values": [
     {
      "value": "極秘",
      "quote": "極秘又は社外秘",
      "is_addition": false
     },
     {
      "value": "社外秘",
      "quote": "極秘又は社外秘",
      "is_addition": false
     }
    ],
    "other_value_note": "極秘・社外秘以外の区分"
   },
   {
    "variable": "encryption_status",
    "status": "new",
    "kind": "状態",
    "values": [
     {
      "value": "暗号化する",
      "quote": "暗号化する",
      "is_addition": false
     }
    ],
    "other_value_note": "暗号化していない"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 6,
  "id": "ipa_secguide_v4_phys_3_printer",
  "action": "放置",
  "facts": [
   {
    "variable": "leave_place",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "複合機",
      "quote": "複合機",
      "is_addition": false
     },
     {
      "value": "プリンタ",
      "quote": "プリンタ",
      "is_addition": false
     }
    ],
    "other_value_note": "複合機・プリンタ以外の場所"
   },
   {
    "variable": "leave_target",
    "status": "new",
    "kind": "対象",
    "values": [
     {
      "value": "原稿",
      "quote": "原稿",
      "is_addition": false
     },
     {
      "value": "印刷物",
      "quote": "印刷物",
      "is_addition": false
     }
    ],
    "other_value_note": "原稿・印刷物以外の物"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 7,
  "id": "ipa_secguide_v4_phys_1_2_level1",
  "action": "放置",
  "facts": [
   {
    "variable": "usage_state",
    "status": "new",
    "kind": "状態",
    "values": [
     {
      "value": "未使用時",
      "quote": "未使用時",
      "is_addition": false
     }
    ],
    "other_value_note": "使用中"
   },
   {
    "variable": "information_classification",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "社外秘",
      "quote": "社外秘又は極秘",
      "is_addition": false
     },
     {
      "value": "極秘",
      "quote": "社外秘又は極秘",
      "is_addition": false
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "area_level",
    "status": "existing",
    "kind": "状況",
    "values": [
     {
      "value": "レベル1領域",
      "quote": "レベル1領域",
      "is_addition": true
     }
    ],
    "other_value_note": null
   }
  ],
  "not_modeled": []
 },
 {
  "order": 8,
  "id": "ipa_secguide_v4_personnel_6_4_4_leave",
  "action": "放置",
  "facts": [
   {
    "variable": "leave_target",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "機器",
      "quote": "機器や書類",
      "is_addition": true
     },
     {
      "value": "書類",
      "quote": "機器や書類",
      "is_addition": true
     }
    ],
    "other_value_note": null
   }
  ],
  "not_modeled": [
   {
    "quote": "目の届く範囲に置き",
    "reason": "評価の判断を含む条件"
   }
  ]
 },
 {
  "order": 9,
  "id": "ipa_secguide_v4_2_3_3_store",
  "action": "保存",
  "facts": [
   {
    "variable": "information_classification",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "社外秘",
      "quote": "社外秘又は極秘",
      "is_addition": false
     },
     {
      "value": "極秘",
      "quote": "社外秘又は極秘",
      "is_addition": false
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "storage_medium",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "IT機器",
      "quote": "IT機器",
      "is_addition": false
     },
     {
      "value": "電子媒体",
      "quote": "電子媒体",
      "is_addition": false
     }
    ],
    "other_value_note": "IT機器・電子媒体以外の保存先"
   },
   {
    "variable": "usage_period",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "利用期間中",
      "quote": "利用期間中",
      "is_addition": false
     }
    ],
    "other_value_note": "利用期間外"
   }
  ],
  "not_modeled": [
   {
    "quote": "以下に該当する機能がある場合には実行する",
    "reason": "機能の内容が文脈に無く機器依存"
   }
  ]
 },
 {
  "order": 10,
  "id": "mhlw_r7dec_art58_3_6",
  "action": "立入り",
  "facts": [
   {
    "variable": "area_type",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "立入禁止区域",
      "quote": "立入禁止又は通行禁止区域",
      "is_addition": false
     },
     {
      "value": "通行禁止区域",
      "quote": "立入禁止又は通行禁止区域",
      "is_addition": false
     }
    ],
    "other_value_note": "それ以外の区域"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 11,
  "id": "mhlw_r7dec_art58_3_4",
  "action": "立入り",
  "facts": [
   {
    "variable": "entrant_age",
    "status": "new",
    "kind": "主体の属性",
    "values": [
     {
      "value": "20歳未満の者",
      "quote": "２０歳未満の者",
      "is_addition": false
     }
    ],
    "other_value_note": "20歳以上の者"
   },
   {
    "variable": "area_type",
    "status": "existing",
    "kind": "状況",
    "values": [
     {
      "value": "喫煙可能な場所",
      "quote": "喫煙可能な場所",
      "is_addition": true
     }
    ],
    "other_value_note": null
   }
  ],
  "not_modeled": []
 },
 {
  "order": 12,
  "id": "mhlw_r7dec_art58_3_5",
  "action": "同伴",
  "facts": [
   {
    "variable": "companion_attribute",
    "status": "new",
    "kind": "対象",
    "values": [
     {
      "value": "受動喫煙を望まない者",
      "quote": "受動喫煙を望まない者",
      "is_addition": false
     }
    ],
    "other_value_note": "受動喫煙を望まない者以外"
   },
   {
    "variable": "area_type",
    "status": "existing",
    "kind": "状況",
    "values": [
     {
      "value": "喫煙可能な場所",
      "quote": "喫煙可能な場所",
      "is_addition": false
     }
    ],
    "other_value_note": null
   }
  ],
  "not_modeled": []
 },
 {
  "order": 13,
  "id": "mhlw_r7dec_art25_2",
  "action": "労働指示",
  "facts": [
   {
    "variable": "worker_category",
    "status": "existing",
    "kind": "主体の属性",
    "values": [
     {
      "value": "産後8週間を経過していない女性労働者",
      "quote": "産後８週間を経過していない女性労働者",
      "is_addition": true
     }
    ],
    "other_value_note": null
   }
  ],
  "not_modeled": []
 },
 {
  "order": 14,
  "id": "mhlw_r7dec_art21_4",
  "action": "労働指示",
  "facts": [
   {
    "variable": "worker_category",
    "status": "existing",
    "kind": "主体の属性",
    "values": [
     {
      "value": "妊産婦",
      "quote": "妊産婦",
      "is_addition": true
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "exemption_request",
    "status": "existing",
    "kind": "状態",
    "values": [
     {
      "value": "請求のあった",
      "quote": "請求のあった",
      "is_addition": true
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "work_type",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "所定労働時間外労働",
      "quote": "所定労働時間外労働",
      "is_addition": true
     },
     {
      "value": "休日労働",
      "quote": "休日労働",
      "is_addition": false
     }
    ],
    "other_value_note": null
   }
  ],
  "not_modeled": [
   {
    "quote": "災害その他避けることのできない事由によって臨時の必要がある場合",
    "reason": "評価の判断を含む条件（許可の側の例外）"
   }
  ]
 },
 {
  "order": 15,
  "id": "ipa_secguide_v4_2_3_3_wifi",
  "action": "使用",
  "facts": [
   {
    "variable": "usage_target",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "Wi-Fiサービス",
      "quote": "Wi-Fiサービス",
      "is_addition": true
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "service_contract",
    "status": "new",
    "kind": "状態",
    "values": [
     {
      "value": "会社で契約したサービス社外",
      "quote": "会社で契約したサービス社外",
      "is_addition": false
     }
    ],
    "other_value_note": "会社契約のサービス"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 16,
  "id": "ipa_secguide_v4_6_10_password_reuse",
  "action": "使用",
  "facts": [
   {
    "variable": "usage_target",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "パスワード",
      "quote": "パスワード",
      "is_addition": true
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "reuse_scope",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "機器間",
      "quote": "機器やサービス間",
      "is_addition": false
     },
     {
      "value": "サービス間",
      "quote": "機器やサービス間",
      "is_addition": false
     }
    ],
    "other_value_note": "使い回しなし"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 17,
  "id": "ipa_secguide_v4_personnel_6_4_2_cloud",
  "action": "使用",
  "facts": [
   {
    "variable": "usage_purpose",
    "status": "existing",
    "kind": "状況",
    "values": [
     {
      "value": "送受信",
      "quote": "送受信",
      "is_addition": true
     },
     {
      "value": "保存",
      "quote": "保存",
      "is_addition": true
     },
     {
      "value": "共有",
      "quote": "共有",
      "is_addition": true
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "handled_data",
    "status": "new",
    "kind": "対象",
    "values": [
     {
      "value": "業務関連データ",
      "quote": "業務関連データ",
      "is_addition": false
     }
    ],
    "other_value_note": "業務関連データ以外"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 18,
  "id": "ipa_secguide_v4_6_10_priv_name",
  "action": "共有",
  "facts": [
   {
    "variable": "share_target",
    "status": "new",
    "kind": "対象",
    "values": [
     {
      "value": "特権アカウント名",
      "quote": "特権アカウント名",
      "is_addition": false
     }
    ],
    "other_value_note": "特権アカウント名以外"
   },
   {
    "variable": "sharer_count",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "2名以上",
      "quote": "2名以上",
      "is_addition": false
     }
    ],
    "other_value_note": "1名"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 19,
  "id": "ipa_secguide_v4_6_2_1_item2",
  "action": "共有",
  "facts": [
   {
    "variable": "share_target",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "同一アカウント",
      "quote": "同一アカウント",
      "is_addition": true
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "share_partner",
    "status": "new",
    "kind": "対象",
    "values": [
     {
      "value": "他者",
      "quote": "他者",
      "is_addition": false
     }
    ],
    "other_value_note": "本人"
   }
  ],
  "not_modeled": []
 },
 {
  "order": 20,
  "id": "ipa_secguide_v4_5_6_share",
  "action": "共有",
  "facts": [
   {
    "variable": "information_classification",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "社外秘",
      "quote": "社外秘又は極秘",
      "is_addition": false
     },
     {
      "value": "極秘",
      "quote": "社外秘又は極秘",
      "is_addition": false
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "share_target",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "情報ファイル",
      "quote": "情報ファイル",
      "is_addition": true
     }
    ],
    "other_value_note": null
   },
   {
    "variable": "share_scope",
    "status": "new",
    "kind": "状況",
    "values": [
     {
      "value": "社外",
      "quote": "社外",
      "is_addition": false
     }
    ],
    "other_value_note": "社内"
   },
   {
    "variable": "share_partner",
    "status": "existing",
    "kind": "対象",
    "values": [
     {
      "value": "秘密保持に関する合意をした相手",
      "quote": "秘密保持に関する合意をした相手",
      "is_addition": true
     }
    ],
    "other_value_note": null
   }
  ],
  "not_modeled": []
 }
]```
