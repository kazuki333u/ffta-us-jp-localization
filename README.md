# FFTA 日本語化 (US版ベース)

**ファイナルファンタジータクティクスアドバンス** の **US版ROM** を、
日本版ROMの日本語テキスト・フォント・グラフィックを用いて日本語化する、
**非公式のファンプロジェクト**です。

US版が独自に持つ追加ミッション・システム改善・バグ修正はそのまま維持したまま、
表示を日本語に置き換えます。

> **Public Beta 7**
> 現在は公開ベータです。正式版 (1.0) ではありません。
> 既知の問題は [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) を必ずお読みください。

## 概要

| | |
|---|---|
| ベース | **US版** FFTA (Final Fantasy Tactics Advance, USA) |
| 日本語の出所 | **ユーザー自身のJP版ROM** (ビルド時に抽出) |
| US版のみの内容 | 本プロジェクトが**新規に翻訳** |
| 配布物 | **ROMではなく、ビルドスクリプト** |

このリポジトリは**公式のテキスト・フォント・グラフィックを一切含みません**。
日本語の文章もフォントも画像も、ビルド時にあなた自身のJP版ROMから読み出します。
そのため、ビルドには**あなた自身が用意した2本のROM** (pristine な US版と JP版) が必要です。
仕組みは [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)、
含むもの・含まないものの正確な範囲は [`docs/FAQ.md`](docs/FAQ.md) にあります。

**ROMは配布しません。入手先の案内もしません。ダウンロードもしません。**
ROMはご自身が適法に用意したものをお使いください。

## 必要なもの

| | |
|---|---|
| **Python** | 3.9 以降 |
| **Pillow** | `pip install -r requirements.txt` |
| **OS** | Windows / macOS / Linux |
| **空き容量** | 約 200 MB (ビルド中の一時領域を含む) |
| **所要時間** | おおむね 2〜10 分 |

エミュレータは**不要**です。

対応するROMは次の2つだけです。SHA-256 の完全一致が唯一の判定基準です。

| | SHA-256 |
|---|---|
| **US版** `AGB-AFXE-0` | `43FC8204C6DCEEE58828AEBC7AF0C72EB807E99F35AD641C8BB0A4FA8B6EDC19` |
| **JP版** `AGB-AFXJ-0` | `B13DD536808EF5D0FD4494386A9499F6FEB8310835D3F867CD17CC340D82BF9A` |

手元のファイルが対応版かどうかは `python build.py --identify <ファイル>` で調べられます。
ROMの用意の仕方は [`docs/BUILD.md`](docs/BUILD.md) にあります。

## ビルド方法

2本のROMをこのフォルダに置いた場合、次のとおりです
(Windows / macOS / Linux で同じコマンドです)。

```
python -m pip install -r requirements.txt
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba
python build.py --identify FFTA_US_JP.gba
```

完成したROMは必ず SHA-256
`FD4D249031C27E139FE4316E548FD047A175E5C1005952C0776D38BBFEEFA5FE` になります。
一致しない場合、`build.py` は**出力を書かずに停止**します。
生成されたROMは**あなたのローカル環境にのみ**作られます。

- 手順の詳細、成功したときの出力例、ハッシュの確かめ方 → [`docs/BUILD.md`](docs/BUILD.md)
- エラーが出たとき、ハッシュが一致しないとき → [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)

## 現在の状態

- 本編を通してプレイ可能な状態まで日本語化されています。
- 日本語化されたテキストエントリは合計 **12,598**
  (JP版からの移植 11,166 / US版のみの新規翻訳 1,332 / その他 100)。
- 製品ROM全体の再解析で、プレイヤーから見える英語の残存は 0 件でした
  (14,363 レコードを走査)。
- 一部の**終盤・特殊なUS版追加シナリオ**は、実際の表示の確認が続いています。

未解決の問題は [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md)、
版ごとの変更点は [`CHANGELOG.md`](CHANGELOG.md) にあります。

> 「完全翻訳」「バグなし」「全数検証済み」とは主張しません。

## 不具合の報告

**バグ報告を歓迎します。** 公開ベータの目的はそこにあります。
→ [Issues から報告](../../issues/new/choose)

- **ROMファイルを添付しないでください。**
- セーブデータの添付は任意です。プレイヤーが入力したキャラクター名・
  クラン名が含まれます。

参加方法は [`CONTRIBUTING.md`](CONTRIBUTING.md) を参照してください。

## ドキュメント

| ファイル | 内容 |
|---|---|
| [`docs/BUILD.md`](docs/BUILD.md) | ビルド手順 |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | 症状ごとの対処 |
| [`docs/FAQ.md`](docs/FAQ.md) | 方針についての質問 |
| [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) | 既知の問題 |
| [`CHANGELOG.md`](CHANGELOG.md) | 変更履歴 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | ビルドの仕組み |
| [`docs/PROVENANCE.md`](docs/PROVENANCE.md) | データの出所と監査 |
| [`docs/LOCAL_ROM_QA.md`](docs/LOCAL_ROM_QA.md) | ROMが必要なローカル検証 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 参加方法 |
| [`SECURITY.md`](SECURITY.md) | 脆弱性の報告 |
| [`NOTICE.md`](NOTICE.md) | 第三者著作物と帰属表示 |

## ライセンスと権利表示

- 本リポジトリのプログラムは **GNU General Public License v3.0** です
  ([`LICENSE`](LICENSE))。ROM解析層は GPL-3.0 の第三者プロジェクト
  [`BSoD123456/ffta_us_cn`](https://github.com/BSoD123456/ffta_us_cn) 由来です
  ([`NOTICE.md`](NOTICE.md))。
- **Final Fantasy Tactics Advance** および同作のROM・テキスト・フォント・
  グラフィック等の一切は **株式会社スクウェア・エニックス** の著作物です。
  GPL-3.0 はそれらには及びません。
- 本プロジェクトは **スクウェア・エニックスおよびその関連会社とは
  一切関係のない、非公式のファンプロジェクト**です。
  公式製品ではなく、公式の日本語版でもありません。
- **本リポジトリはROMを一切含みません。**
- 本プロジェクトは、利用が適法であることを保証しません。
  お住まいの地域の法令等をご自身でご確認ください。

## English summary

An **unofficial fan project** that localizes the **US release** of
*Final Fantasy Tactics Advance* into Japanese, keeping the US version's own
added content and fixes. **No ROM is included, linked or downloaded.**
Japanese text, fonts and graphics are extracted at build time from **your own**
Japanese ROM; US-only content is newly translated by this project.

```
python -m pip install -r requirements.txt
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba
```

Both inputs are gated on exact SHA-256 and the output is verified against the
pinned hash before it is written. This is **Public Beta 7**, not 1.0 — see
[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md). Code is GPL-3.0
([`NOTICE.md`](NOTICE.md)). Final Fantasy Tactics Advance is the property of
Square Enix; this project is not affiliated with Square Enix.
**Please do not attach ROM files to issues.**
