# ビルド手順

このページだけを読めばビルドできるように書いています。
Windows を主対象としていますが、macOS / Linux でも同じ手順です。

うまくいかないときは [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) を参照してください。

---

## 0. 全体像

```
あなたのUS版ROM  ┐
                 ├─→  python build.py  ─→  日本語化されたUS版ROM
あなたのJP版ROM  ┘
```

- このリポジトリは **ROMを含みません**。
- ビルドは**すべてローカル**で完結します。ネットワークアクセスはありません。
- 出力ROMは**必ず同じハッシュ**になります (再現性があります)。
- エミュレータは**不要**です。

### なぜリポジトリにROMが入っていないのか

日本語のフォント・文章・グラフィックは、すべて日本版ROMに含まれる
**株式会社スクウェア・エニックスの著作物**です。
本プロジェクトはそれらを一切複製・再配布せず、
**ビルド時にあなた自身のROMから読み出す**設計にしています。

そのため、US版ROMとJP版ROMの2本が必須です。片方だけではビルドできません。

---

## 1. 必要環境

| 項目 | 条件 |
|---|---|
| Python | **3.9 以降** (3.11〜3.14 で確認) |
| 追加パッケージ | **Pillow** のみ |
| ディスク空き容量 | 約 **200 MB** (中間ROMを一時的に生成します) |
| 所要時間 | おおむね **2〜10分** (PCによります) |
| OS | Windows / macOS / Linux (OS依存の処理はありません) |

Python が入っているかの確認:

```
python --version
```

`python` が見つからない場合、Windows では `py --version`、
macOS / Linux では `python3 --version` を試してください。
以降のコマンドの `python` を、動いた方に読み替えてください。

---

## 2. このリポジトリを取得する

Git を使う場合:

```
git clone <このリポジトリのURL>
cd ffta-us-jp-localization
```

Git を使わない場合は、**Code → Download ZIP** から取得し、
展開したフォルダへ移動してください。

### 取得後のフォルダ構成

```
ffta-us-jp-localization/
  build.py                  ← 実行するのはこれ
  requirements.txt
  README.md
  LICENSE
  NOTICE.md
  SECURITY.md
  docs/                     ← ドキュメント
  src/localizer/chain/      ← ビルドチェーン本体
  tests/
  tools/
```

`build.py` がフォルダ直下にあることを確認してください。

---

## 3. 依存パッケージを入れる

```
python -m pip install -r requirements.txt
```

インストールされるのは **Pillow** だけです。

> 仮想環境を使う場合 (任意):
> ```
> python -m venv .venv
> .venv\Scripts\activate        # Windows
> source .venv/bin/activate     # macOS / Linux
> python -m pip install -r requirements.txt
> ```

---

## 4. US版ROMを用意する

**あなた自身が適法に用意したROM**を使用してください。

本プロジェクトは **ROMを配布しません。入手先も案内しません。**
利用の適法性についての保証もしません。

対応するのは次の1リビジョン (`AGB-AFXE-0`) のみです。

```
SHA-256  43FC8204C6DCEEE58828AEBC7AF0C72EB807E99F35AD641C8BB0A4FA8B6EDC19
CRC32    5645E56C
サイズ   16,777,216 バイト
```

---

## 5. JP版ROMを用意する

同じく、**あなた自身が適法に用意したROM**を使用してください。

対応するのは次の1リビジョン (`AGB-AFXJ-0`) のみです。

```
SHA-256  B13DD536808EF5D0FD4494386A9499F6FEB8310835D3F867CD17CC340D82BF9A
サイズ   16,777,216 バイト
```

日本語のフォント・文章・グラフィック (ジョブアイコンを含む) は、
すべてこのROMから**ビルド時に抽出**されます。
リポジトリには含まれていません。

---

## 6. ROMの置き場所

どこに置いても構いません。`build.py` にパスを渡すだけです。

**A) リポジトリ直下に置く場合** (最も簡単)

```
ffta-us-jp-localization/
  build.py
  FFTA_US.gba
  FFTA_JP.gba
```

```
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba
```

**B) 別のフォルダに置く場合**

```
python build.py --us "C:\path\to\FFTA_US.gba" --jp "C:\path\to\FFTA_JP.gba" --output "C:\path\to\FFTA_US_JP.gba"
```

> `.gba` はリポジトリの `.gitignore` で除外されているため、
> 直下に置いても誤ってコミットされることはありません。

---

## 7. ROMを確認する

ビルド前に、手元のファイルが対応版かどうかを確認できます。

```
python build.py --identify FFTA_US.gba
python build.py --identify FFTA_JP.gba
```

出力例:

```
FFTA_US.gba
  size    16,777,216 bytes
  SHA-256 43FC8204C6DCEEE58828AEBC7AF0C72EB807E99F35AD641C8BB0A4FA8B6EDC19
  CRC32   5645E56C
  -> pristine US ROM (correct --us input)
```

`-> not a ROM revision this project knows about` と表示された場合は、
そのROMでは**ビルドできません**。
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) を参照してください。

対応ハッシュの一覧は次でも確認できます。

```
python build.py --print-hashes
```

---

## 8. ビルドする

```
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba
```

進行表示 (5段階):

```
FFTA US->JP localization -- Public Beta 2 (RC23-based)

[1/5] verifying your ROMs
      US  OK  43FC8204C6DCEEE58828AEBC7AF0C72EB807E99F35AD641C8BB0A4FA8B6EDC19
      JP  OK  B13DD536808EF5D0FD4494386A9499F6FEB8310835D3F867CD17CC340D82BF9A

[2/5] staging the build in ...\.build

[3/5] building (this rebuilds every layer from your ROMs; expect a few minutes)
      done in 154s   log: ...\.build\build.log

[4/5] verifying the build
      SHA-256 6C78CDEE7914056CEBF6A39354A6A82C8DA132C2D6A0D88928B4B6A2CDB717E6
      CRC32   8262D569

[5/5] writing the output
      FFTA_US_JP.gba

      removed the scratch directory (...\.build)

Done.  This is a public beta: please read docs/KNOWN_ISSUES.md,
and do not attach ROM files to bug reports.
```

`[3/5]` の間、進行表示は止まったように見えます (数分かかります)。
異常ではありません。詳細ログは `.build/build.log` に随時書かれています。

### 主なオプション

| オプション | 意味 |
|---|---|
| `--work-dir <path>` | 中間ファイルの置き場所 (既定: `./.build`) |
| `--keep-work` | ビルド後も中間ファイルを残す (不具合調査用) |
| `--identify <rom>` | ROMの正体を表示して終了 |
| `--print-hashes` | 対応ハッシュを表示して終了 |

> `--keep-work` で残る `.build/` には**あなたのROMのコピー**が入ります。
> Issue に添付したりコミットしたりしないでください。
> 既定ではビルド成功時に自動削除されます。

---

## 9. 出力を確認する

ビルドが成功した時点で、`build.py` が既にハッシュを検証しています。
`[4/5]` に表示される値が次と一致していれば成功です。

```
SHA-256  6C78CDEE7914056CEBF6A39354A6A82C8DA132C2D6A0D88928B4B6A2CDB717E6
CRC32    8262D569
サイズ   16,777,216 バイト
```

自分でも確認する場合:

```
# このツールで
python build.py --identify FFTA_US_JP.gba

# Windows PowerShell
Get-FileHash .\FFTA_US_JP.gba -Algorithm SHA256

# Windows コマンドプロンプト
certutil -hashfile FFTA_US_JP.gba SHA256

# macOS / Linux
shasum -a 256 FFTA_US_JP.gba
```

`--identify` が
`-> an already-localized ROM built by this project (Public Beta 2 / RC23)`
と表示すれば成功です。

あとはお使いの GBA エミュレータ、またはフラッシュカートで起動してください。
生成されたROMは**あなたのローカル環境にのみ**存在します。

---

## 10. ビルドは再現可能です

同じ2本のROMからは、**常に同じバイト列**が出力されます。

自分で確かめる場合は、作業ディレクトリを分けて2回ビルドしてください。

```
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output out1.gba --work-dir .build1
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output out2.gba --work-dir .build2
```

**Windows PowerShell**

```
(Get-FileHash .\out1.gba -Algorithm SHA256).Hash
(Get-FileHash .\out2.gba -Algorithm SHA256).Hash
```

**macOS / Linux**

```
shasum -a 256 out1.gba out2.gba
```

2つが一致し、かつ §9 の値と同じであれば再現性の確認は完了です。

ビルドチェーン自体も内部で2回ビルドして自己比較しています
([`ARCHITECTURE.md`](ARCHITECTURE.md) §5)。

---

## 11. うまくいかないとき

代表的なエラーと対処は [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) に
まとめています。

Issue を立てる際は次を添えてください。

- `python --version` の出力
- OS
- `build.py` が表示したエラー全文
- `.build/build.log` の**末尾**

**ROMファイル・`.build/` フォルダは添付しないでください。**

---

## 12. 開発者向け

ROM を必要としない検査 (CIで実行しているもの) をローカルで走らせる:

```
python -m pip install -r requirements-dev.txt
python tools/public_audit.py
python -m pytest tests/public -q
```

ROM が必要な検証については [`LOCAL_ROM_QA.md`](LOCAL_ROM_QA.md) を参照してください。
