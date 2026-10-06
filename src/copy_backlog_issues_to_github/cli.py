from __future__ import annotations

import argparse
import os
import sys

from .copy_from_backlog import copy_from_backlog
from .copy_to_github import (
    copy_to_github,
    list_oversized_files,
    list_unattachable_files,
)


def build_parser() -> argparse.ArgumentParser:
    # Backlogのドメインを使うサブコマンド共通の引数(projectKeyより前に置く)
    domain_parser = argparse.ArgumentParser(add_help=False)
    domain_parser.add_argument(
        "domain",
        metavar="backlogDomain",
        help="Backlogのドメイン(例: example.backlog.jp)",
    )

    # 全サブコマンド共通の引数
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "project_key",
        metavar="projectKey",
        help="Backlogのプロジェクトキー",
    )
    common_parser.add_argument(
        "--working-dir",
        metavar="workingDir",
        default=".",
        help="作業ディレクトリ。省略時はカレントディレクトリになります",
    )
    common_parser.add_argument(
        "--issue-key",
        metavar="issueKey",
        dest="issue_keys",
        action="append",
        help="対象の課題キー(複数指定可)。省略時は全課題が対象になります",
    )

    # ファイルパスを一覧するサブコマンド共通の引数
    list_files_parser = argparse.ArgumentParser(add_help=False)
    list_files_parser.add_argument(
        "--null",
        action="store_true",
        help="パスの区切り文字をNUL文字にします(xargs -0 との併用を想定)",
    )

    parser = argparse.ArgumentParser(prog="copy-backlog-issues-to-github")
    subparsers = parser.add_subparsers(dest="command", required=True)

    copy_from_backlog_parser = subparsers.add_parser(
        "copy-from-backlog",
        parents=[domain_parser, common_parser],
        help="Backlogの課題を取得します",
    )
    copy_from_backlog_parser.add_argument(
        "--no-download",
        action="store_true",
        help="課題添付ファイルのダウンロードを行いません",
    )

    copy_to_github_parser = subparsers.add_parser(
        "copy-to-github",
        parents=[domain_parser, common_parser],
        help="GitHubにIssueを作成します",
    )
    copy_to_github_parser.add_argument(
        "repo_url",
        metavar="repoUrl",
        help="ghコマンドの-Rオプションに渡すGitHubリポジトリ",
    )
    copy_to_github_parser.add_argument(
        "repo_branch",
        metavar="repoBranch",
        help="添付ファイルへのリンクに使用するブランチ名",
    )

    subparsers.add_parser(
        "list-unattachable-files",
        parents=[common_parser, list_files_parser],
        help="ghコマンドの--attachで添付できないファイルパスを一覧します",
    )

    subparsers.add_parser(
        "list-oversized-files",
        parents=[common_parser, list_files_parser],
        help=(
            "ghコマンドの--attachで添付できず, "
            "GitHubの1ファイル100MB制限にも引っかかるファイルパスを一覧します"
        ),
    )

    return parser


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"環境変数 {name} を設定してください")
    return value


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "copy-from-backlog":
        api_key = _require_env("BACKLOG_API_KEY")
        copy_from_backlog(
            domain=args.domain,
            api_key=api_key,
            working_dir=args.working_dir,
            project_key=args.project_key,
            issue_keys=args.issue_keys,
            download=not args.no_download,
        )
    elif args.command == "copy-to-github":
        copy_to_github(
            working_dir=args.working_dir,
            project_key=args.project_key,
            repo_url=args.repo_url,
            repo_branch=args.repo_branch,
            issue_keys=args.issue_keys,
            domain=args.domain,
        )
    elif args.command == "list-unattachable-files":
        list_unattachable_files(
            working_dir=args.working_dir,
            project_key=args.project_key,
            issue_keys=args.issue_keys,
            null=args.null,
        )
    elif args.command == "list-oversized-files":
        list_oversized_files(
            working_dir=args.working_dir,
            project_key=args.project_key,
            issue_keys=args.issue_keys,
            null=args.null,
        )


if __name__ == "__main__":
    main()
