# FFTA 日本語化 (US版ベース)

**ファイナルファンタジータクティクスアドバンス** の **US版ROM** を、
日本版ROMの日本語テキスト・フォント・グラフィックを用いて日本語化する、
**非公式のファンプロジェクト**です。

US版が独自に持つ追加ミッション・システム改善・バグ修正はそのまま維持したまま、
表示を日本語に置き換えます。

> **Public Beta 2 (RC23-based)**
> 現在は公開ベータです。正式版 (1.0) ではありません。
> 既知の問題は [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) を必ずお読みください。

---

## このプロジェクトは何をするのか

| | |
|---|---|
| ベース | **US版** FFTA (Final Fantasy Tactics Advance, USA) |
| 目的 | US版を**日本語表示**にする |
| 日本語の出所 | **ユーザー自身のJP版ROM** (ビルド時に抽出) |
| US版のみの内容 | 本プロジェクトが**新規に翻訳** |
| 配布物 | **ROMではなく、ビルドスクリプト** |

US版には日本版に存在しない追加コンテンツ (追加ミッション、追加シナリオ、
一部システム) があります。これらは日本版に対応する日本語が存在しないため、
**本プロジェクトが日本語訳を新規作成**しています。

### なぜ2本のROMが必要なのか

このリポジトリは**公式のテキスト・フォント・グラフィックを一切含みません**。
日本語のフォントも文章も、ジョブアイコンなどの画像も、
ビルド時にあなた自身のJP版ROMから読み出します。

そのため、ビルドには**あなた自身が用意した2本のROM**が必要です。

- pristine な **US版** ROM
- pristine な **JP版** ROM

**ROMは配布しません。入手先の案内もしません。ダウンロードもしません。**
ROMはご自身が適法に用意したものをお使いください。
本プロジェクトは利用の適法性について保証しません。
利用にあたっては、お住まいの地域の法令等をご自身でご確認ください。

---

## 必要なもの

| | |
|---|---|
| **Python** | 3.9 以降 (3.11〜3.14 で動作確認) |
| **Pillow** | `pip install -r requirements.txt` |
| **OS** | Windows / macOS / Linux (開発の主環境は Windows 11) |
| **空き容量** | 約 200 MB (ビルド中の一時領域を含む) |
| **所要時間** | おおむね 2〜10 分 |

エミュレータは**不要**です (ビルドには一切使いません)。

### 対応ROM

| | |
|---|---|
| **US版** `AGB-AFXE-0` | SHA-256 `43FC8204C6DCEEE58828AEBC7AF0C72EB807E99F35AD641C8BB0A4FA8B6EDC19` |
| **JP版** `AGB-AFXJ-0` | SHA-256 `B13DD536808EF5D0FD4494386A9499F6FEB8310835D3F867CD17CC340D82BF9A` |

いずれも 16,777,216 バイト (16 MiB) の無改変ダンプです。
対応するのは**この2つのリビジョンだけ**で、**SHA-256 の完全一致が唯一の判定基準**です。
トリミング済み・ヘッダ付き・パッチ済み・他リージョンのROMは受け付けません。

手元のファイルが対応版か調べるには:

```
python build.py --identify path/to/your.gba
```

---

## ビルド方法

```
python -m pip install -r requirements.txt

python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba
```

Windows の PowerShell / コマンドプロンプトでも、同じ1行がそのまま使えます
(ROMをこのフォルダに置いた場合)。

```
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba
```

ROMを別の場所に置いている場合は、パスを指定してください。

```
python build.py --us "C:\path\to\FFTA_US.gba" --jp "C:\path\to\FFTA_JP.gba" --output "C:\path\to\FFTA_US_JP.gba"
```

### 成功したときの出力

```
FFTA US->JP localization -- Public Beta 2 (RC23-based)

[1/5] verifying your ROMs
      US  OK  43FC8204...
      JP  OK  B13DD536...

[2/5] staging the build in ...
[3/5] building (this rebuilds every layer from your ROMs; expect a few minutes)
      done in 154s   log: ...
[4/5] verifying the build
      SHA-256 6C78CDEE7914056CEBF6A39354A6A82C8DA132C2D6A0D88928B4B6A2CDB717E6
      CRC32   8262D569
[5/5] writing the output
      FFTA_US_JP.gba
```

ビルドされたROMは**必ず**次のハッシュになります。

```
SHA-256  6C78CDEE7914056CEBF6A39354A6A82C8DA132C2D6A0D88928B4B6A2CDB717E6
CRC32    8262D569
サイズ   16,777,216 バイト
```

一致しない場合、`build.py` は**出力を書かずに停止**します。

### ハッシュを確認する

```
# Windows PowerShell
Get-FileHash .\FFTA_US_JP.gba -Algorithm SHA256

# macOS / Linux
shasum -a 256 FFTA_US_JP.gba
```

生成されたROMは**あなたのローカル環境にのみ**作られます。
本プロジェクトはROMをどこにも送信しません。

詳しい手順は [`docs/BUILD.md`](docs/BUILD.md)、
うまくいかないときは [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) を参照してください。

---

## ハッシュが一致しないとき

| 症状 | 主な原因 |
|---|---|
| `--us is not the supported pristine ROM` | 別のダンプ / 別リージョン / ヘッダ付き / トリミング済み |
| `It looks like --us and --jp are swapped.` | `--us` と `--jp` が逆 |
| `That file is an already-localized ROM ...` | すでに日本語化済みのROMを入力にしている |
| `--us is N bytes; ... is 16,777,216 bytes` | `.zip` / `.7z` のまま、または壊れたファイル |

対処は [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) にまとめています。

---

## 現在の状態 — 正確な範囲

過大な表現を避けるため、実測値のみを記載します。

**できていること**

- 本編を通してプレイ可能な状態まで日本語化されています。
- 製品ROM全体のテキスト再解析で、**プレイヤーから見える英語の残存は0件**でした
  (14,363レコードを走査)。
- 日本語化されたテキストエントリは合計 **12,598**
  (JP版からの移植 11,166 / US版のみの新規翻訳 1,332 / その他 100)。
- グリフ描画の照合では、**4,606 / 4,606** のエントリが
  JP版ROMと同一のグリフビットマップで描画されました。
- **ジョブアイコン 44/44** が日本版ROM本来の日本語ラベル入りのものに
  置き換わっています (Public Beta 2 で追加)。
- **アイテム情報の「装備可能ジョブ」ページ 29分類 / 361レコード**が
  正しく描画されます (Public Beta 2 で修正)。
- クエスト期限表示、バトル中 SYSTEM メニュー、OPTIONS の値表示の
  文字欠けを修正しました (Public Beta 2 で修正)。
- 自動探索QAで **26ルート / 3,130 UI状態 / 12,766 遷移** を走査し、
  クラッシュ0・ハング0・リブート0 (この計測は同系列の以前のビルドで実施)。
- 固定回帰スイート **84/84 フレームが画素単位で一致**。

**まだ終わっていないこと**

- 一部の**終盤・特殊なUS版追加シナリオ**は、そのシナリオに到達する
  ゲーム内状態を構築中で、**実機描画の確認が継続中**です。
- 軽微な表示上の問題が既知として残っています。

いずれも [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) に記載しています。

> 「完全翻訳」「バグなし」「全数検証済み」とは主張しません。
> 現時点で確認できている範囲のみを上に記載しています。

---

## 不具合の報告

**バグ報告を歓迎します。** 公開ベータの目的はそこにあります。

→ [Issues から報告](../../issues/new/choose)

報告時のお願い:

- **ROMファイルを添付しないでください。**
- セーブデータを添付する場合は、プレイヤーが入力したキャラクター名・
  クラン名が含まれることにご注意ください (添付は任意です)。

参加方法の詳細は [`CONTRIBUTING.md`](CONTRIBUTING.md) を参照してください。

---

## ドキュメント

| ファイル | 内容 |
|---|---|
| [`docs/BUILD.md`](docs/BUILD.md) | ビルド手順 (初めての方向け) |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | うまくいかないときの対処 |
| [`docs/FAQ.md`](docs/FAQ.md) | よくある質問 |
| [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) | 既知の問題 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | ビルドの仕組み |
| [`docs/US_ADDED_CONTENT.md`](docs/US_ADDED_CONTENT.md) | US版追加コンテンツについての技術ノート |
| [`docs/PUBLIC_BETA_RELEASE_NOTES.md`](docs/PUBLIC_BETA_RELEASE_NOTES.md) | リリースノート |
| [`docs/PROVENANCE.md`](docs/PROVENANCE.md) | データの出所と監査 |
| [`docs/LOCAL_ROM_QA.md`](docs/LOCAL_ROM_QA.md) | ROMが必要なローカル検証 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 参加方法 |
| [`SECURITY.md`](SECURITY.md) | 安全性・プライバシー上の取り扱い |
| [`NOTICE.md`](NOTICE.md) | 第三者著作物と帰属表示 |
| [`docs/PUBLISH_CHECKLIST.md`](docs/PUBLISH_CHECKLIST.md) | 公開・運用時の手順 (メンテナ向け) |

---

## ライセンスと権利表示

- 本リポジトリのプログラムは **GNU General Public License v3.0** です
  ([`LICENSE`](LICENSE))。
  ROM解析層は GPL-3.0 の第三者プロジェクト
  [`BSoD123456/ffta_us_cn`](https://github.com/BSoD123456/ffta_us_cn) 由来です。
  詳細は [`NOTICE.md`](NOTICE.md)。
- **Final Fantasy Tactics Advance** および同作のROM・テキスト・フォント・
  グラフィック等の一切は **株式会社スクウェア・エニックス** の著作物です。
  GPL-3.0 はそれらには及びません。
- 本プロジェクトは **スクウェア・エニックスおよびその関連会社とは
  一切関係のない、非公式のファンプロジェクト**です。
  公式製品ではなく、公式の日本語版でもありません。
- **本リポジトリはROMを一切含みません。**
- 本プロジェクトは、利用が適法であることを保証しません。
  利用にあたっては、お住まいの地域の法令等をご自身でご確認ください。

---

## English summary

An **unofficial fan project** that localizes the **US release** of
*Final Fantasy Tactics Advance* into Japanese, keeping the US version's own
added content and fixes as the baseline.

**No ROM is included, linked or downloaded.** The repository ships build
tooling only. Japanese text, fonts and graphics are extracted at build time
from **your own** Japanese ROM; content that exists only in the US release is
newly translated by this project.

```
python -m pip install -r requirements.txt
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba
```

Both inputs are gated on exact SHA-256, and the finished ROM is verified
against the pinned release hash (`6C78CDEE…`, CRC32 `8262D569`) before it is
written.

This is **Public Beta 2 (RC23-based)**, not a 1.0 release — see
[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md). Program code is GPL-3.0
(see [`NOTICE.md`](NOTICE.md) for third-party attribution). Final Fantasy
Tactics Advance is the property of Square Enix; this project is not affiliated
with or endorsed by Square Enix. **Please do not attach ROM files to issues.**
