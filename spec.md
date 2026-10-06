# 概要

Backlogプロジェクトの課題をGitHubリポジトリのIssueにコピーするツール.
GitHubリポジトリは課題管理兼ファイル保管専用を想定している.
文書中の「課題」はBacklogの課題を指し, 「Issue」はGitHubのIssueを指す.

# コマンド

## copy-from-backlog: Backlogの課題を取得する

```bash
copy-backlog-issues-to-github copy-from-backlog --no-download --working-dir <workingDir> --issue-key <issueKey> <backlogDomain> <projectKey>
```

### オプション

- `--no-download`
  - 課題添付ファイルのダウンロードを行わない.
- `--working-dir <workingDir>`
  - 指定したディレクトリを作業ディレクトリとする. 指定しない場合はカレントディレクトリを作業ディレクトリとする.
- `--issue-key <issueKey>`
  - 複数指定可. 対象を `<issueKey>` にマッチする課題のみに絞り込む.

### パラメータ

- `<backlogDomain>`
  - Backlogのドメイン(`example.backlog.jp`, `example.backlog.jp` 等).
- `<projectKey>`
  - Backlogのプロジェクトキー.

### 環境変数

- `BACKLOG_API_KEY`
  - BacklogのAPIキー. コマンド履歴に残らないよう, パラメータではなく環境変数で受け取る.

### 出力仕様

以下の出力を行う.

- プロジェクト情報のレスポンス
  - `<workingDir>/<projectKey>/project.json`
- 課題一覧のレスポンス
  - `<workingDir>/<projectKey>/issues.json`
- 課題情報のレスポンス
  - `<workingDir>/<projectKey>/<issueKey>/info.json`
- 課題コメント一覧のレスポンス
  - `<workingDir>/<projectKey>/<issueKey>/comments.json`
- 課題添付ファイル
  - `<workingDir>/<projectKey>/<issueKey>/<添付ID>/<ファイル名>`
  - 同じ課題に同名の添付ファイルが複数あっても衝突しないよう, 添付ID(`attachments[].id`)のディレクトリに保存する.
  - 添付ファイルの一覧は課題情報の `attachments` を使用する(課題添付ファイル一覧取得APIは使用しない).
  - ファイル名は課題情報の `attachments[].name` からディレクトリ部分を除いたもの(課題ディレクトリ外への書き込みを防ぐため).

### その他の仕様

- 既にファイルが存在するリクエストはスキップするメッセージを出力し, 再取得, 再ダウンロードしない.
- 書き込みは一時ファイルに書いてから置き換える. 中断時に不完全なファイルが残り, 再実行時にスキップされるのを防ぐため.
- 添付ファイルはストリーミングでダウンロードし, 書き込んだサイズが課題情報の `size` と一致しない場合はエラーとする.
  - 受信途中で接続が切れた場合, サイズが一致しない場合はダウンロードをやり直す(「リトライ仕様」参照).

## copy-to-github: GitHubにIssueを作成する

```bash
copy-backlog-issues-to-github copy-to-github --working-dir <workingDir> --issue-key <issueKey> <backlogDomain> <projectKey> <repoUrl> <repoBranch>
```

### オプション

- `--working-dir <workingDir>`
- `--issue-key <issueKey>`
  - `copy-from-backlog` サブコマンドと同様の意味

### パラメータ

- `<backlogDomain>`
  - `copy-from-backlog` サブコマンドと同様の意味. 本文中の課題リンク(`[[<issueKey>]]`)を `https://<backlogDomain>/view/<issueKey>` へのリンクに変換するために使用する.
- `<projectKey>`
  - `copy-from-backlog` サブコマンドと同様の意味
- `<repoUrl>`
  - GitHubリポジトリのURL. ghコマンドの `-R` オプションで指定する形式である必要がある.
- `<repoBranch>`
  - GitHubリポジトリのデフォルトブランチ.

### 動作仕様

以下の動作を行う.

1. 課題の状態, 種別に対応するラベルを作成する(「ラベル仕様」参照).
2. 課題情報からIssueを作成する. 課題に添付されていたファイルをIssueに添付する. 課題の状態, 種別のラベルを付ける.
3. 課題コメント一覧から古い順にIssueコメントを追加する. 課題コメントに添付されていたファイルをIssueコメントに添付する.
4. 課題情報の `status.id` が `4`(完了)の場合, Issueをcloseする(「Issueのclose仕様」参照).

### ラベル仕様

- 課題情報(`info.json`)の `status` と `issueType` を, それぞれ `状態: <name>`, `種別: <name>` という名前のラベルとしてIssueに付ける.
  - 状態と種別で同名の場合があり, GitHubのラベル名は大文字小文字を区別しないため, 接頭辞で区別する.
  - 「Issueのclose仕様」と同じ理由で, 課題一覧(`issues.json`)は参照しない.
- ラベルの色は `color`(`#b0be3c` 形式)から `#` を除いたものとする. 説明は `Backlogの課題の状態`, `Backlogの課題の種別` とする.
- 次の場合はエラーとする.
  - ラベル名に `,` が含まれる. ghの `--label`, `--add-label` はカンマ区切りで複数のラベルとして解釈するため.
  - ラベル名がGitHubの上限(50文字)を超える.
  - `color` が6桁の16進数(`#` は任意)でない.
- `gh issue create --label` は存在しないラベルを指定するとエラーになるため, Issueの作成前にラベルを作成する.
  - ラベル付与済み(作業記録ファイルに `labels` がある)でない対象課題の `info.json` から, 必要なラベルを集める.
  - `gh label list --json name,color,description` で既存のラベルを取得する. 存在しない, または色, 説明が異なるラベルのみ `gh label create --force` で作成, 更新する.
  - 既存のラベルとの名前の比較は大文字小文字を区別しない.
- Issue作成時に `--label` で付与する. 付与したラベル名は作業記録ファイルの `labels` に記録する.
- 作業記録ファイルに `number` があり `labels` がないIssueには, `gh issue edit --add-label` で付与してから `labels` を記録する.
  - ラベル付与に対応する前に作成したIssueと, `pendingIssue` の確認で作成済みと分かったIssue(ラベルが付いているか不明)が該当する.
  - 付与済みのラベルを再度付与しても成功するため, 付与後, 記録前に中断して再実行しても問題ない. そのため `pending` の記録は行わない.
- `labels` を記録した後は, 課題の状態, 種別が変わってもラベルを変更しない. 状態変更への追従は未対応.

### Issueのclose仕様

- Backlogの状態はIDの1〜4が予約されており, 4が「完了」である. 状態名はプロジェクトごとに変更できるため, IDで判定する.
- 課題の状態は課題情報(`info.json`)の `status.id` を参照する. Issue作成に使う他の項目と同じ時点の情報に揃えるため, 課題一覧(`issues.json`)は参照しない.
  - `copy-from-backlog` は取得済みのファイルをスキップするため, 取得後の状態の変更を反映するには利用者が `info.json` を削除して取り直す.
- `gh issue close <number> -R <repoUrl> --reason completed` でcloseする.
- タイムラインが作成, コメント, closeの順になるよう, コメントをすべて追加した後にcloseする.
- 課題が完了でない場合はcloseしない. 完了を取り消した課題でも, close済みのIssueを再openすることはしない.

### Issue作成とIssue追加の仕様

- Issueタイトルには課題情報の `summary` を使用する.
- Issue概要の末尾(フッター部分)には, 課題情報の `issueId`, `issueKey`, `createdUser.name`, `created` を記載する.
  - 1行目は `Backlog課題: <issueKey> (issueId: <issueId>)` とし, Issue作成の成否確認に使用する(「リトライ仕様」参照).
  - フッター部分は引用記法等で視覚的に本文と分かれるようにする.
- Issueコメントの末尾には, 課題コメントの `id`, `createdUser.name`, `created` を記載する.
  - 1行目は `Backlogコメント: <issueKey> (commentId: <id>)` とし, コメント追加の成否確認に使用する(「リトライ仕様」参照).
- プロジェクト情報の `textFormattingRule` が `backlog` の場合, 課題情報と課題コメントをBacklog記法からMarkdown記法に変換する. `markdown` の場合はそのまま使用する.
- GitHubの自動リンクを防ぐため, 本文中の以下の文字の直後にゼロ幅スペース(U+200B)を挿入する. コードブロック, インラインコード内は対象外.
  - メンション(`@user`)として解釈される `@`. GitHub上の無関係なユーザーにメンションが飛ぶのを防ぐ. メールアドレス, URL中の `@`(直前が英数字, `_`, `/`)は対象外.
  - Issue参照(`#123`)として解釈される `#`. コピー先リポジトリの無関係なIssueへのリンクになるのを防ぐ. URLのフラグメント, HTMLの文字参照中の `#`(直前が英数字, `_`, `/`, `&`)は対象外.

### 添付ファイル仕様

- サポートされている拡張子(2026/10/09時点ではpng, jpg, jpeg, gif, webp, svg, mp4, mov, webmの9種. 大小文字無視)で10MB以下のファイルは, ghコマンドの `--attach` オプションにより添付する.
- `--attach` による添付対象外のファイルは, リポジトリにプッシュされているものとして, ファイルへのリンク(`../blob/<repoBranch>/<projectKey>/<issueKey>/<添付ID>/<ファイル名>`)を生成する.
  - リンクのパスセグメントはNFC形式に正規化した上で `urllib.parse.quote` でクォートする必要がある.
    - macOSのgitは既定(`core.precomposeunicode=true`)でファイル名をNFC形式にしてコミットするため. 利用者はNFC形式でコミットする必要がある.
  - リンク文字列(ファイル名)はMarkdownの特殊文字をバックスラッシュでエスケープする.
- 課題コメントの変更履歴に追加の記録があるが課題情報の `attachments` に存在しない(後から削除された)添付ファイルは, リンクを生成せず `<ファイル名> (削除済み)` と表示する. `list-unattachable-files`, `list-oversized-files` の一覧にも含めない.
  - リポジトリへの添付ファイル追加〜プッシュは利用者が行う.
  - GitHubリポジトリを利用する都合上, 100MBを超えるファイルは通常のgitではプッシュできないが, 現状はそのようなファイルの検出(`list-oversized-files` サブコマンド)までを行い, 実際の対応(Git LFS化等)は利用者が行う.

### 作業記録ファイル仕様 (github-issues.json)

- 作業の途中経過を `<projectKey>/github-issues.json` に記録し, 再実行した際に作成済みのIssue, 追加済みのコメントをスキップする.

```json
{
  "<issueKey>": {
    "number": 42,
    "comments": [1001, 1002, 1005],
    "pendingComment": 1006,
    "labels": ["状態: 完了", "種別: タスク"],
    "closed": true
  }
}
```

- トップレベルは課題の `issueKey` をキーにしたオブジェクトとする.
- `number` はIssue作成時に `gh issue create` の出力(Issue URL)から得られるGitHub Issue番号. このキーが存在する課題はIssue作成済みとみなし, 作成処理をスキップしてコメント追加処理のみ行う.
- `comments` は追加済みの課題コメントの `id` の一覧. `comments.json` の各コメントのうち, この一覧に含まれない `id` のみを追加対象とする.
- `labels` はIssueに付与したラベル名の一覧. このキーが存在する課題はラベル付与をスキップする(「ラベル仕様」参照).
- `closed` はIssueをcloseした場合に `true`. このキーが `true` の課題はclose処理をスキップする.
  - close後, 記録ファイルの書き込み前に処理が中断した場合, 再実行時に再度closeする. close済みのIssueに対する `gh issue close` は警告を出力して成功(終了コード0)するため, 事前の確認は行わない.
- Issue作成直後, 及びコメントを1件追加するごとにファイルを書き直す. まとめて最後に書き込むと, 途中で処理が中断した際に実際には投稿済みのIssue, コメントが記録から漏れ, 再実行時に重複投稿される.
- 書き込みは一時ファイルに書いてから置き換える等, 書き込み中断によるJSONの破損を避ける.
- Issue作成, コメント追加の直前に, 作成中であることを記録する. Issue作成, コメント追加の成功後, 記録ファイルの書き込み前に処理が中断した場合に, 再実行時に重複投稿しないため.
  - `pendingIssue` はIssue作成中であれば `true`. 作成が完了したら `number` を記録してこのキーを削除する.
  - `pendingComment` は追加中の課題コメントの `id`. 追加が完了したら `comments` に記録してこのキーを削除する.
  - 再実行時にこれらのキーが残っていれば, 対象のIssue, コメントが作成済みかをフッターの1行目を元に確認する(確認方法は「リトライ仕様」と同じ). 作成済みであれば記録のみ行い, 作成されていなければ改めて作成する. 確認自体に失敗した場合はエラーとする.

## list-unattachable-files: ghの `--attach` で添付できないファイルパスを一覧する

```bash
copy-backlog-issues-to-github list-unattachable-files --working-dir <workingDir> --issue-key <issueKey> <projectKey>
```

### オプション

- `--working-dir <workingDir>`
- `--issue-key <issueKey>`
  - `copy-from-backlog` サブコマンドと同様の意味

### パラメータ

- `<projectKey>`
  - `copy-from-backlog` サブコマンドと同様の意味

## 動作

「添付ファイル仕様」の基準(拡張子, サイズ)で `--attach` による添付対象外と判定される添付ファイルのローカルパスを標準出力に列挙する.
出力は `git add` にそのまま渡す運用を想定する.

```bash
copy-backlog-issues-to-github list-unattachable-files <projectKey> | xargs git add
```

### 出力仕様

- 1行に1ファイルパスを出力する. パスは `<projectKey>/<issueKey>/<添付ID>/<ファイル名>` 形式(カレントディレクトリからの相対パス)とする.
- 出力順は課題一覧, 課題内の添付ファイルの順(`copy-to-github` が処理する順序)に準じる. 重複は出力しない.
- `--null` を指定すると, ファイル名に改行やスペースが含まれる場合に備えて区切り文字をNUL(`\0`)にする. `xargs -0 git add` と組み合わせて使う.
  ```bash
  copy-backlog-issues-to-github list-unattachable-files --null <projectKey> | xargs -0 git add
  ```
- ローカルにファイルが存在しない(未ダウンロード)場合は標準エラーに警告を出力し, 一覧には含めない.

### その他の仕様

- 添付ファイルの取得元, 判定基準は「GitHubにIssueを作成する」の「添付ファイル仕様」と共通とする.

## list-oversized-files: ghの `--attach` で添付できないし, GitHubの100MB制限にも引っかかるファイルパスを一覧する

```bash
copy-backlog-issues-to-github list-oversized-files --working-dir <workingDir> --issue-key <issueKey> <projectKey>
```

### オプション

- `--working-dir <workingDir>`
- `--issue-key <issueKey>`
  - `copy-from-backlog` サブコマンドと同様の意味
- `--null`
  - `list-unattachable-files` サブコマンドと同様の意味

### パラメータ

- `<projectKey>`
  - `copy-from-backlog` サブコマンドと同様の意味

### 動作

`--attach` による添付対象外(「添付ファイル仕様」の基準)であり, かつファイルサイズがGitHubの1ファイルあたりのサイズ上限(100MB)を超える添付ファイルのローカルパスを標準出力に列挙する.
これらのファイルは `list-unattachable-files` の出力に含まれる(通常のコミットでリポジトリにプッシュする想定の)ファイルのうち, 実際には(素のgitでは)プッシュできないものであるため, Git LFS の利用やファイルの分割等, 別途の対応が必要になる.

### 出力仕様

- 出力フォーマットは `list-unattachable-files` と共通(1行1パス, 出力順, `--null` オプション, 未ダウンロードファイルの扱い).

# 使用ライブラリ, ツール

- コマンドラインのパースには `argparse` を使用する.
- HTTPリクエストには `niquests` を使用する.
- Backlog課題取得にはbacklog APIを使用する.
- GitHub Issue作成にはGitHub CLIを使用する.
  - 認証は済ませてあるものとする.
  - REST APIを使わないのは `--attach` オプション相当の機能がないため.

# Backlog API

概ね上から順に使うことになると思われる.

## プロジェクト情報取得

projectKeyとprojectIdを交換する.
後でtextFormattingRuleも参照する.

https://developer.nulab.com/ja/docs/backlog/api/2/get-project/

## 課題一覧取得

https://developer.nulab.com/ja/docs/backlog/api/2/get-issue-list/

## 課題情報取得

https://developer.nulab.com/ja/docs/backlog/api/2/get-issue/

## 課題コメント一覧取得

https://developer.nulab.com/ja/docs/backlog/api/2/get-comment-list/

## 課題添付ファイルダウンロード

https://developer.nulab.com/ja/docs/backlog/api/2/get-issue-attachment/

# Backlog記法のMarkdown記法への変換

Backlog記法については以下に記載がある.

https://help-center.backlog.jp/%E3%83%86%E3%82%AD%E3%82%B9%E3%83%88%E6%95%B4%E5%BD%A2%E3%83%AB%E3%83%BC%E3%83%AB%EF%BC%88Backlog%E8%A8%98%E6%B3%95%EF%BC%89-6a1d4d7f3abb3ada78c5658b

全て対応する必要はなく, 正確に変換できそうなもの, 目立つものを中心に変換に対応する.

- Backlogでは通常の文字だが, Markdownでは行頭にあるとブロック記法として解釈される文字は, 段落, リスト項目, 引用の本文の行頭でエスケープする.
  - `#`, `>`, `-`, `+`, `*`, `=`, `_`, `` ` ``, `~`, `<` はバックスラッシュでエスケープする.
  - `1.`, `1)` のような番号付きリストのマーカーは区切り文字をエスケープする.
  - `[ラベル]:` のようなリンク参照定義は `[` をエスケープする.
  - 行頭の空白はコードブロック等として解釈されないよう `&nbsp;` にする(タブは空白4文字として扱う).

# レートリミット(重要)

- GitHubの二次レート制限(コンテンツ作成は毎分80件, 毎時500件まで)を超えないよう, Issue作成, コメント追加の間隔を 3600/500 = 7.2秒以上空ける.
  - close, ラベル作成, ラベル付与も同様に間隔を空ける.

# リトライ仕様

## Backlog API

- タイムアウトは接続10秒, 読み込み60秒とする.
- 429の場合は `X-RateLimit-Reset` の時刻まで待って再試行する. 10回再試行しても解除されなければエラーとする.
- 5xx, 接続エラー, タイムアウトの場合は10秒, 20秒, 40秒と待って最大3回再試行する.
- 添付ファイルの受信途中で接続が切れた場合, 受信したサイズが課題情報の `size` と一致しない場合も, 同様にダウンロードを最初からやり直す.
- それ以外の4xxは再試行せずエラーとする.
- エラーメッセージ中のAPIキーは伏せる.
- 課題コメント一覧のページングでは, `minId` に指定したIDのコメントはレスポンスに含まれないため, 前ページの最後のコメントの `id` をそのまま `minId` に指定する.

## GitHub (ghコマンド)

- 本文が65536文字を超える場合はghコマンドを実行せずエラーとする.
- エラー出力からエラーを分類する.
  - レート制限(`rate limit`, `submitted too quickly` 等): 60秒からの指数バックオフ(上限3600秒)で最大10回再試行する.
  - 再試行しても成功しないエラー(`is too long`, `validation failed`, `HTTP 401` 等): 再試行せずエラーとする.
  - その他(一時的なエラーの可能性があるもの): 同様のバックオフで最大3回再試行する.
- サーバー側では作成に成功しているのにエラーになる場合があるため, 再試行の前にフッターの1行目を元に作成済みか確認し, 作成済みであれば再試行せず成功として扱う.
  - Issue: `gh issue list --state all` で直近30件のIssueの本文を確認する.
  - コメント: `gh issue view --json comments` で対象Issueの全コメントの本文を確認する.
  - 確認自体に失敗した場合は, 重複作成を避けるため再試行せずエラーとする.

- Backlog API
  - https://developer.nulab.com/ja/docs/backlog/rate-limit/
- GitHub GraphQL API(GitHub CLIは内部でGraphQL APIを使用している)
  - https://docs.github.com/ja/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api
