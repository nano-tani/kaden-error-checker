# 家電エラーコード検索

エラーコードを1つ入力すると、メーカー公式サポートで確認済みの原因・対処候補を返す静的サイトです。

## 方針

- 入力はエラーコードだけ
- 同じコードが複数メーカーで使われる場合は候補をすべて表示
- 情報源は原則メーカー公式サイト
- 各データに公式URLと確認日を保存
- AI推測だけでエラー内容を追加しない

## データ追加

`data/errors.json` にレコードを追加します。

```json
{
  "manufacturer": "メーカー名",
  "appliance": "製品種別",
  "code": "E01",
  "aliases": ["E1"],
  "summary": "公式情報を要約した内容",
  "actions": ["対処1", "対処2"],
  "source": "https://メーカー公式URL",
  "verified": "YYYY-MM-DD"
}
```

## 公開

`.github/workflows/pages.yml` で GitHub Pages にデプロイします。
GitHub の Settings → Pages → Build and deployment → Source を **GitHub Actions** に設定してください。

## AdSense / SEO の次段階

検索ツール1ページだけでは、検索流入やAdSense審査には弱い構成です。データが増えたらJSONから静的HTMLを生成し、

`/errors/<manufacturer>/<appliance>/<code>/`

のようなコード別ページを作る想定です。各ページには公式情報の要約、対象機種、確認手順、出典、更新日を持たせます。
