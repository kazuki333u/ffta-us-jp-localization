# 公開前チェックリスト

このリポジトリを実際に GitHub へ公開するときの手順です。

**現時点では公開していません。** remote は設定されておらず、push も
行っていません。

---

## 1. 公開前に埋めるプレースホルダ

`.github/ISSUE_TEMPLATE/config.yml` の `contact_links` は
GitHub の仕様上、**絶対URL**しか受け付けません。
リポジトリのURLが決まるまで埋められないため、`OWNER/REPO` のままです。

```
python tools/public_audit.py
```

の `publish-placeholders` 行に、残っている箇所が一覧されます。
公開前にすべて実際の `オーナー名/リポジトリ名` へ置き換えてください。

> それ以外のドキュメント内リンクは**相対リンク**なので、
> リポジトリ名が何であっても正しく解決されます。書き換え不要です。

---

## 2. 公開前に必ず実行するもの

```
# ROMありの完全監査 (rom-substring を SKIP させないこと)
python tools/public_audit.py --us <US版ROM> --jp <JP版ROM>

# 公開テストスイート
python -m pytest tests/public -q

# ROM必須テスト
FFTA_US_ROM=<US版ROM> FFTA_JP_ROM=<JP版ROM> python -m pytest tests/rom -q
```

すべて PASS することを確認してください。

`rom-substring` が `SKIP` のまま公開しないでください。

### クリーンルーム確認 (推奨)

開発ツリーへの依存が残っていないことを確かめるため、
このリポジトリを**別のディレクトリへ複製**し、その複製だけからビルドして
出力が一致することを確認してください。

```
# 例 (PowerShell)
Copy-Item -Recurse <このリポジトリ> <一時ディレクトリ>\clean
cd <一時ディレクトリ>\clean
python build.py --us <US版ROM> --jp <JP版ROM> --output out.gba
```

出力 SHA-256 が `6C78CDEE7914056CEBF6A39354A6A82C8DA132C2D6A0D88928B4B6A2CDB717E6`
になれば合格です。

---

## 3. 公開時の設定

### リポジトリ名と説明文 (案)

| 項目 | 案 |
|---|---|
| リポジトリ名 | `ffta-us-jp-localization` |
| Description | `非公式ファンプロジェクト: FFTA US版を、あなた自身のJP版ROMから日本語化するビルドツール (ROMは同梱しません)` |
| Description (英語併記する場合) | `Unofficial fan project: build a Japanese-localized FFTA (US) ROM from your own US and JP ROMs. No ROM included.` |

最初は **private** で作成し、ユーザー本人のローカル再現確認と
private 状態での監査が終わってから public へ切り替えてください。

### リポジトリ設定

- [ ] リポジトリ名がある種の説明性を持ち、**公式製品と誤認されない**こと
- [ ] Description に「非公式ファンプロジェクト」であることを含める
- [ ] Topics: `fan-translation`, `japanese`, `gba`, `romhacking` など
- [ ] **Releases に ROM や BPS を添付しない**
- [ ] Discussions を有効にするかは任意 (最初は Issues だけでも十分)

### ラベル

[`LABELS.md`](LABELS.md) の「最小構成」だけ作成してください。
全部作る必要はありません。

### ブランチ保護 (任意)

- [ ] `main` への直接 push を制限
- [ ] PR に `public-ci` の PASS を必須にする

---

## 4. 公開後にやらないこと

- **ROM を Releases・Issue・Wiki・Discussions のどこにも置かない**
- **ROM の入手先を案内しない** (Issue・コメントを含む)
- CI の secret / cache / artifact に ROM を入れない
- `.build/` や出力 ROM をコミットしない

---

## 5. BPS パッチの公開について

RC23 の BPS パッチは開発側に存在しますが、
**このリポジトリには含めていません。**

「差分パッチだから安全」と自動的に言えるわけではないため、
公開するかどうかは**別途あらためて判断**します。

現時点の公開形態は **2本のROMからのローカルビルドのみ**です。

---

## 6. バージョンタグ

公開時に付けるなら:

```
v0.2.0-beta.2
```

内部の RC 番号 (RC23 等) を公開タグに使わないでください
([`PUBLIC_BETA_RELEASE_NOTES.md`](PUBLIC_BETA_RELEASE_NOTES.md) の
「バージョンについて」を参照)。
