# 家電エラーコード検索

家電に表示されたエラーコードを入力すると、メーカー公式情報をもとに原因と対処方法の候補を表示する静的サイトです。

## 仕組み

編集する一次データは `data/errors.json` だけです。

`data/errors.json` を main ブランチへ更新すると GitHub Actions が `tools/generate.py` を実行し、次のファイルを自動生成して main へコミットします。

- `errors/*.html` — メーカー・家電種別・エラーコードごとの個別ページ
- `data/search-index.json` — トップページ検索用データ
- `sitemap.xml` — 検索エンジン向けサイトマップ
- `robots.txt` — クローラー向け設定

個別ページのファイル名は「メーカー + 家電種別 + エラーコード」から固定IDを作るため、`errors.json` の並び順を変えてもURLは変わりません。

## データ追加

`data/errors.json` に次の形式で1件追加します。

```json
{
  "manufacturer": "メーカー名",
  "appliance": "家電種別",
  "code": "エラーコード",
  "aliases": ["別表記"],
  "summary": "原因・状態の要約",
  "actions": [
    "確認事項1",
    "確認事項2"
  ],
  "source": "メーカー公式ページURL",
  "verified": "YYYY-MM-DD"
}
```

掲載データはメーカー公式サポート・公式取扱説明書で確認した内容だけを追加してください。AIの推測だけでエラー内容を登録しない方針です。

## 手動生成

```bash
python3 tools/generate.py
```

標準の公開URLは `https://nano-tani.github.io/kaden-error-checker` です。変更する場合は `SITE_URL` 環境変数を指定します。

```bash
SITE_URL=https://example.com python3 tools/generate.py
```

## GitHub Pages

GitHub Pages はリポジトリの `Settings` → `Pages` で次の設定にします。

- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/(root)`

GitHub Pages の初回有効化だけはリポジトリ設定から行う必要があります。
