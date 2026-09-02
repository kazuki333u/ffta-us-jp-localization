# トラブルシューティング

ビルドがうまくいかないときの対処をまとめています。
基本的な手順は [`BUILD.md`](BUILD.md) を参照してください。

---

## 1. Python が見つからない

### 症状

```
'python' は、内部コマンドまたは外部コマンド、
操作可能なプログラムまたはバッチ ファイルとして認識されていません。
```

または

```
command not found: python
```

### 対処

**Windows**

```
py --version
```

これが動く場合は、以降のコマンドの `python` を `py` に置き換えてください。

```
py -m pip install -r requirements.txt
py build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba
```

どちらも動かない場合は Python 3.9 以降をインストールしてください。
インストーラの **「Add python.exe to PATH」** に必ずチェックを入れてください。

**macOS / Linux**

```
python3 --version
```

これが動く場合は `python` を `python3` に読み替えてください。

### バージョンが古い

```
python --version
```

が `3.8` 以下の場合はビルドできません。3.9 以降を入れてください。

---

## 2. `Pillow is required`

### 症状

```
ERROR: Pillow is required.  Install the dependencies first:
           python -m pip install -r requirements.txt
```

### 対処

```
python -m pip install -r requirements.txt
```

権限エラーになる場合:

```
python -m pip install --user -r requirements.txt
```

複数の Python が入っている環境では、`build.py` を動かすものと
`pip` の対象が違うことがあります。必ず `python -m pip` の形
(先頭の `python` は `build.py` に使うものと同じ) で実行してください。

---

## 3. US版ROMのハッシュが一致しない

### 症状

```
ERROR: --us is not the supported pristine ROM.
       expected SHA-256 43FC8204...
       got      SHA-256 ........
```

### 原因と対処

| 原因 | 確認方法 | 対処 |
|---|---|---|
| **別リビジョン / 別リージョン** | `python build.py --identify <rom>` が `not a ROM revision this project knows about` | 対応リビジョンの pristine ダンプを用意する |
| **すでにパッチ済み** | `--identify` が `an already-localized ROM ...` | **未改変**のROMを指定する |
| **ヘッダ付き / トリミング済み** | サイズが 16,777,216 バイトでない | 無改変の 16 MiB ダンプを用意する |
| **US と JP が逆** | エラー内に `It looks like --us and --jp are swapped.` | `--us` と `--jp` を入れ替える |

本プロジェクトは **SHA-256 の完全一致のみ**を判定基準にしています。
「たぶん同じ版」では通りません。これは、ずれた位置に日本語を書き込んで
静かに壊れたROMを作らないための意図的な設計です。

---

## 4. JP版ROMのハッシュが一致しない

### 症状

```
ERROR: --jp is not the supported pristine ROM.
       expected SHA-256 B13DD536...
```

### 対処

§3 と同じです。特に多いのは次の2つです。

- **US版を `--jp` に指定している** →
  `It looks like --us and --jp are swapped.` が表示されます。
- **JP版の別ダンプ** → 対応するのは1リビジョンのみです。

---

## 5. `... is N bytes; a Final Fantasy Tactics Advance ROM is 16,777,216 bytes`

### 原因と対処

- **`.zip` / `.7z` のまま指定している** → 展開して `.gba` を指定してください。
- **ダウンロード / コピーが途中で切れている** → もう一度用意し直してください。
- **トリミング済みダンプ** → 対応していません。
- **ヘッダ付きダンプ** → 対応していません。

---

## 6. 出力パスのエラー

### 症状

```
FileNotFoundError: ... 'FFTA_US_JP.gba'
```

または `PermissionError`。

### 対処

- **存在しないフォルダを指定している** →
  `build.py` は出力先の親フォルダを自動作成しますが、
  ドライブレターの誤り (`E:\` が無い等) は作成できません。パスを見直してください。
- **同名ファイルを他のアプリが開いている** →
  エミュレータで開いたままのROMには上書きできません。閉じてから実行してください。
- **書き込み権限がない場所** → `Program Files` 直下などは避け、
  ユーザーフォルダ配下を指定してください。

---

## 7. アクセス権 / 書き込み権限のエラー

### 症状

```
PermissionError: [Errno 13] Permission denied: ...\.build\...
```

### 対処

- リポジトリを `C:\Program Files\` などの保護された場所から
  ユーザーフォルダ配下 (例: `ドキュメント`) へ移動してください。
- ウイルス対策ソフトが `.build/` への書き込みを妨げている場合があります。
  一時的に別の作業ディレクトリを指定して切り分けてください。

```
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba --work-dir C:\path\to\ffta_build_tmp
```

---

## 8. ビルドが途中で失敗する

### 症状

```
ERROR: the build chain failed.
       full log: ...\.build\build.log
```

エラー全文の末尾25行が画面にも表示されます。

### 対処

1. ディスク空き容量を確認してください (**200 MB 以上**必要です)。
2. `--keep-work` を付けて再実行し、ログ全体を残します。

```
python build.py --us FFTA_US.gba --jp FFTA_JP.gba --output FFTA_US_JP.gba --keep-work
```

3. `.build/build.log` の**末尾**を Issue に貼ってください。

**添付しないでください:**

- ROMファイル
- `.build/` フォルダ (**あなたのROMのコピーが入っています**)

### よく出る内部エラーの意味

| メッセージ | 意味 |
|---|---|
| `ORIGINAL_ROM_HASH_MISMATCH` | 入力ROMが対応版ではない (通常 `build.py` 側で先に止まります) |
| `CANONICAL_PRODUCTION_MISMATCH` | 最終出力が固定期待値と違う → §10 |
| `..._BUILD_NONDETERMINISTIC` | 同一入力で2回ビルドした結果が食い違った → §10 |
| `*_MANIFEST_ENGLISH_DRIFT` | US版ROMの該当レコードが翻訳時の内容と違う (別リビジョンの可能性) |

---

## 9. `[3/5]` で止まっているように見える

異常ではありません。ビルドチェーンは28層すべてを再構築するため、
**数分間、画面表示が止まったように見えます**。

進行を確認したい場合は、別のウィンドウで `.build/build.log` を
見てください。

```
# Windows PowerShell
Get-Content .\.build\build.log -Tail 20 -Wait
```

10分以上まったく更新されない場合のみ、異常を疑ってください。

---

## 10. 出力ROMのハッシュが一致しない

### 症状

```
ERROR: the finished ROM does not match this release.
       expected 6C78CDEE...
       got      ........
       Nothing was written to the output path.
```

**この場合、出力ファイルは書き込まれません。** 壊れたROMは作られません。

### 原因

ビルド自体は完走したのに結果が違う、という状態です。想定される原因:

- リポジトリのファイルが一部欠けている / 改変されている
  (ZIP展開の失敗、部分的な `git clone` など)
- 改行コードの変換によりデータファイルが変質した
  (`.gitattributes` があるため通常は起きません)

### 対処

1. リポジトリを**クリーンに取得し直して**再実行してください。
2. それでも再現する場合は、表示された SHA-256 と
   `python --version`、OS、`.build/build.log` の末尾を添えて
   Issue を立ててください。

---

## 11. 生成したROMがエミュレータで動かない

- まず `python build.py --identify <出力ROM>` でハッシュを確認してください。
  `an already-localized ROM built by this project (Public Beta 2 / RC23)`
  と出るなら、ビルドは正常です。
- セーブデータ (`.sav`) は US版と互換です。
  ただし他のパッチを当てたROMのセーブとは混在させないでください。
- 表示上の問題は [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) を先に確認してください。

---

## 12. 文字化けした進行表示が出る

日本語環境の Windows コンソールでは、
一部の記号が `?` に置き換わって表示されることがあります。
**ビルド結果には影響しません** (`build.py` は表示のみを
置換して処理を継続します)。

気になる場合は、コマンドプロンプトで次を実行してから再試行してください。

```
chcp 65001
```

---

## それでも解決しない場合

[Issue](../../../issues/new/choose) をお願いします。次を添えてください。

- OS とそのバージョン
- `python --version` の出力
- 実行したコマンド全文
- 表示されたエラー全文
- `.build/build.log` の末尾 (あれば)

**ROMファイルは添付しないでください。**
