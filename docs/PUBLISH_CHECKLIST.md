# 公開前チェックリスト

このリポジトリを実際に GitHub へ公開するときの手順です。

**現時点では公開していません。** remote は設定されておらず、push も
行っていません。

---

## 0. 新しいマイルストーンへ更新する手順 (メンテナ向け)

このツリーは**手で編集しません**。開発リポジトリから機械的に導出します。

1. 終端ビルダー名を差し替える (3 か所)

   | ファイル | 定数 |
   |---|---|
   | `tools/sync_from_canonical.py` | `TERMINAL` |
   | `tools/public_audit.py` | `TERMINAL` |
   | `tests/public/test_public_surface.py` | `test_chain_is_closed_under_import` 内の起点 |

2. チェーンを再生成する

   ```
   python tools/sync_from_canonical.py --canonical <開発リポジトリのパス>
   python tools/sync_from_canonical.py --canonical <開発リポジトリのパス> --check
   ```

3. `build.py` を更新する

   - `TERMINAL` (終端ビルダーのファイル名)
   - `RELEASE` (公開向けの呼称)
   - `EXPECTED_OUTPUT` / `EXPECTED_OUTPUT_CRC32`
   - `produced` (チェーンが書き出すROMのファイル名)
   - `KNOWN` に旧リリースのハッシュを追加

4. ドキュメントのハッシュ・リリース名・既知の問題を更新する

5. §2 の検査をすべて実行する

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
`commit-email` が `SKIP` のまま公開しないでください
(shallow clone では履歴を検査できません)。

### コミットのメールアドレス

`public_audit.py` の `commit-email` は、到達可能なすべてのコミットとタグの
author / committer / tagger アドレスが GitHub の noreply
(`ID+ユーザー名@users.noreply.github.com`) であることを検査します。

個人のメールアドレスは、リポジトリを public にした瞬間に公開されます。
そして **force push では取り戻せません**。履歴を書き換えても、古いオブジェクトは
ホスト側に残り、SHA を知っていれば取得できる状態が続きます。
つまりこれは、最初の 1 回で正しくしなければならない項目です。

作業前に、このリポジトリで:

```
git config --local user.email <ID>+<ユーザー名>@users.noreply.github.com
```

GitHub 側でも次の 2 つを有効にしてください (別々の保護です)。

- Settings → Emails → **Keep my email addresses private**
- Settings → Emails → **Block command line pushes that expose my email**

後者は、ローカル設定を間違えたコミットの push そのものを止めます。

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

出力 SHA-256 が `F1D673A1966C6C42B6F2CEF157F11EF984BB61E6A2184D7F0F47AC17EE2CA695`
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

RC24 の BPS パッチは開発側に存在しますが、
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

内部の RC 番号 (RC24 等) を公開タグに使わないでください
([`PUBLIC_BETA_RELEASE_NOTES.md`](PUBLIC_BETA_RELEASE_NOTES.md) の
「バージョンについて」を参照)。
