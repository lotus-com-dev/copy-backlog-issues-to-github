import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from copy_backlog_issues_to_github import copy_to_github


@pytest.fixture(autouse=True)
def _reset_throttle_state() -> None:
    copy_to_github._last_request_monotonic = None


def _result(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_gh_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        copy_to_github.subprocess,
        "run",
        lambda *a, **k: _result(0, stdout="https://example.com/issues/1\n"),
    )
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda _s: None)

    output = copy_to_github._run_gh(
        ["gh", "issue", "create"], body="body", attach_paths=[], error_message="失敗"
    )
    assert output == "https://example.com/issues/1\n"


def test_run_gh_retries_on_secondary_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        if len(calls) == 1:
            return _result(1, stderr="GraphQL: was submitted too quickly (addComment)")
        return _result(0, stdout="ok")

    sleeps: list[float] = []
    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda s: sleeps.append(s))

    output = copy_to_github._run_gh(
        ["gh", "issue", "comment"], body="body", attach_paths=[], error_message="失敗"
    )

    assert output == "ok"
    assert len(calls) == 2
    assert len(sleeps) == 2  # throttle分 + リトライ待機分


def test_run_gh_retries_non_rate_limit_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        if len(calls) == 1:
            return _result(
                1,
                stderr="GraphQL: Something went wrong while executing your query.",
            )
        return _result(0, stdout="ok")

    sleeps: list[float] = []
    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda s: sleeps.append(s))

    output = copy_to_github._run_gh(
        ["gh", "issue", "comment"], body="body", attach_paths=[], error_message="失敗"
    )

    assert output == "ok"
    assert len(calls) == 2
    assert len(sleeps) == 2  # throttle分 + リトライ待機分


def test_run_gh_raises_after_exhausting_generic_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return _result(1, stderr="GraphQL: Something went wrong")

    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda _s: None)
    monkeypatch.setattr(copy_to_github, "_MAX_GENERIC_RETRIES", 2)

    with pytest.raises(copy_to_github.CopyToGithubError, match="Something went wrong"):
        copy_to_github._run_gh(
            ["gh", "issue", "create"],
            body="body",
            attach_paths=[],
            error_message="失敗",
        )

    assert len(calls) == 3  # 初回 + リトライ2回


def test_run_gh_raises_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        copy_to_github.subprocess,
        "run",
        lambda *a, **k: _result(1, stderr="secondary rate limit exceeded"),
    )
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda _s: None)
    monkeypatch.setattr(copy_to_github, "_MAX_RATE_LIMIT_RETRIES", 2)

    with pytest.raises(copy_to_github.CopyToGithubError, match="secondary rate limit"):
        copy_to_github._run_gh(
            ["gh", "issue", "create"],
            body="body",
            attach_paths=[],
            error_message="失敗",
        )


def test_run_gh_without_body_does_not_pass_body_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return _result(0)

    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda _s: None)

    copy_to_github._run_gh(["gh", "issue", "close", "1"], error_message="失敗")

    assert calls == [["gh", "issue", "close", "1"]]


def test_throttle_sleeps_when_called_too_soon(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([100.0, 100.0, 100.5, 100.5])
    monkeypatch.setattr(copy_to_github.time, "monotonic", lambda: next(times))
    sleeps: list[float] = []
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda s: sleeps.append(s))

    copy_to_github._last_request_monotonic = None
    copy_to_github._throttle()
    assert sleeps == []

    copy_to_github._throttle()
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(
        copy_to_github._MIN_REQUEST_INTERVAL_SECONDS - 0.5
    )


def test_is_retryable_error() -> None:
    assert copy_to_github._is_retryable_error("was submitted too quickly")
    assert copy_to_github._is_retryable_error(
        "You have exceeded a secondary rate limit"
    )
    assert copy_to_github._is_retryable_error("API rate limit exceeded for user")
    assert copy_to_github._is_retryable_error(
        "GraphQL: API rate limit already exceeded for user ID 1. (createIssue)"
    )
    assert not copy_to_github._is_retryable_error("validation failed: title required")


def test_is_oversized() -> None:
    assert not copy_to_github._is_oversized(None)
    assert not copy_to_github._is_oversized(100 * 1024 * 1024)
    assert copy_to_github._is_oversized(100 * 1024 * 1024 + 1)


def test_list_oversized_files_only_lists_files_exceeding_github_limit(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_dir = tmp_path / "PROJ"
    issue_dir = project_dir / "TEST-1"
    issue_dir.mkdir(parents=True)

    (project_dir / "issues.json").write_text(
        json.dumps([{"issueKey": "TEST-1"}]), encoding="utf-8"
    )
    (issue_dir / "info.json").write_text(
        json.dumps(
            {
                "issueKey": "TEST-1",
                "attachments": [
                    {"id": 1, "name": "small.png", "size": 1000},
                    {"id": 2, "name": "big.zip", "size": 200 * 1024 * 1024},
                    {"id": 3, "name": "medium.zip", "size": 50 * 1024 * 1024},
                ],
            }
        ),
        encoding="utf-8",
    )
    (issue_dir / "comments.json").write_text("[]", encoding="utf-8")
    for attachment_id, name in ((1, "small.png"), (2, "big.zip"), (3, "medium.zip")):
        (issue_dir / str(attachment_id)).mkdir()
        (issue_dir / str(attachment_id) / name).write_bytes(b"")

    copy_to_github.list_oversized_files(
        working_dir=str(tmp_path), project_key="PROJ", issue_keys=None, null=False
    )

    assert capsys.readouterr().out == f"{issue_dir / '2' / 'big.zip'}\n"


def test_list_oversized_files_warns_when_file_missing(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_dir = tmp_path / "PROJ"
    issue_dir = project_dir / "TEST-1"
    issue_dir.mkdir(parents=True)

    (project_dir / "issues.json").write_text(
        json.dumps([{"issueKey": "TEST-1"}]), encoding="utf-8"
    )
    (issue_dir / "info.json").write_text(
        json.dumps(
            {
                "issueKey": "TEST-1",
                "attachments": [
                    {"id": 1, "name": "big.zip", "size": 200 * 1024 * 1024},
                ],
            }
        ),
        encoding="utf-8",
    )
    (issue_dir / "comments.json").write_text("[]", encoding="utf-8")

    copy_to_github.list_oversized_files(
        working_dir=str(tmp_path), project_key="PROJ", issue_keys=None, null=False
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "big.zip" in captured.err


def _write_project(
    tmp_path,
    *,
    attachments: list[dict] | None = None,
    comments: list[dict] | None = None,
    description: str = "本文",
    text_formatting_rule: str = "markdown",
    status_id: int = 1,
):
    project_dir = tmp_path / "PROJ"
    issue_dir = project_dir / "P-1"
    issue_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        json.dumps({"textFormattingRule": text_formatting_rule}), encoding="utf-8"
    )
    (project_dir / "issues.json").write_text(
        json.dumps([{"issueKey": "P-1"}]), encoding="utf-8"
    )
    (issue_dir / "info.json").write_text(
        json.dumps(
            {
                "id": 100,
                "issueKey": "P-1",
                "summary": "タイトル",
                "description": description,
                "createdUser": {"name": "作成者"},
                "created": "2024-01-01T00:00:00Z",
                "attachments": attachments or [],
                "status": {"id": status_id, "name": "未対応", "color": "#ed8077"},
                "issueType": {"id": 10, "name": "タスク", "color": "#7ea800"},
            }
        ),
        encoding="utf-8",
    )
    (issue_dir / "comments.json").write_text(
        json.dumps(comments or []), encoding="utf-8"
    )
    return project_dir, issue_dir


def _comment(comment_id: int, content: str | None, *, attachment_ids=()) -> dict:
    return {
        "id": comment_id,
        "content": content,
        "createdUser": {"name": "投稿者"},
        "created": f"2024-01-02T00:00:{comment_id:02d}Z",
        "changeLog": [
            {
                "field": "attachment",
                "attachmentInfo": {"id": aid, "name": f"deleted-{aid}.zip"},
                "originalValue": None,
                "newValue": f"file-{aid}",
            }
            for aid in attachment_ids
        ],
    }


class FakeGh:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[dict] = []
        self.fail_on_call = fail_on_call

    def __call__(
        self, args, *, body=None, attach_paths=None, error_message, find_existing=None
    ):
        self.calls.append({"args": args, "body": body, "attach_paths": attach_paths})
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise copy_to_github.CopyToGithubError(error_message)
        if args[:3] == ["gh", "issue", "create"]:
            return "https://github.com/o/r/issues/42\n"
        return ""


_LABELS = ["状態: 未対応", "種別: タスク"]
_EXISTING_LABELS = [
    {"name": "状態: 未対応", "color": "ED8077", "description": "Backlogの課題の状態"},
    {"name": "種別: タスク", "color": "7ea800", "description": "Backlogの課題の種別"},
]


def _copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    gh: FakeGh,
    *,
    existing_labels: list[dict] = _EXISTING_LABELS,
) -> None:
    def run_gh_query(args: list[str]) -> str:
        assert args[:3] == ["gh", "label", "list"]
        return json.dumps(existing_labels)

    monkeypatch.setattr(copy_to_github, "_run_gh", gh)
    monkeypatch.setattr(copy_to_github, "_run_gh_query", run_gh_query)
    copy_to_github.copy_to_github(
        working_dir=str(tmp_path),
        project_key="PROJ",
        repo_url="o/r",
        repo_branch="main",
        issue_keys=None,
        domain=None,
    )


def _record(project_dir) -> dict:
    return json.loads((project_dir / "github-issues.json").read_text(encoding="utf-8"))


def test_copy_to_github_records_issue_and_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(
        tmp_path, comments=[_comment(2, "後"), _comment(1, "先"), _comment(3, None)]
    )
    gh = FakeGh()

    _copy(monkeypatch, tmp_path, gh)

    assert [c["args"][:3] for c in gh.calls] == [
        ["gh", "issue", "create"],
        ["gh", "issue", "comment"],
        ["gh", "issue", "comment"],
    ]
    # 古い順に追加され, 内容が空のコメントは投稿しないが記録はする
    assert gh.calls[1]["body"].startswith("先")
    assert gh.calls[2]["body"].startswith("後")
    assert gh.calls[1]["args"][3] == "42"
    # フッターに作成成否の確認に使う識別情報が含まれる
    assert "> Backlog課題: P-1 (issueId: 100)" in gh.calls[0]["body"].splitlines()
    assert "> Backlogコメント: P-1 (commentId: 1)" in gh.calls[1]["body"].splitlines()
    assert gh.calls[0]["args"][-4:] == ["--label", _LABELS[0], "--label", _LABELS[1]]
    assert _record(project_dir) == {
        "P-1": {"number": 42, "comments": [1, 2, 3], "labels": _LABELS}
    }


def test_copy_to_github_closes_completed_issue_after_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(
        tmp_path, comments=[_comment(1, "コメント")], status_id=4
    )
    gh = FakeGh()

    _copy(monkeypatch, tmp_path, gh)

    assert [c["args"] for c in gh.calls[1:]] == [
        ["gh", "issue", "comment", "42", "-R", "o/r"],
        ["gh", "issue", "close", "42", "-R", "o/r", "--reason", "completed"],
    ]
    assert gh.calls[2]["body"] is None
    assert _record(project_dir) == {
        "P-1": {"number": 42, "comments": [1], "labels": _LABELS, "closed": True}
    }

    # 再実行してもクローズ済みのIssueは閉じ直さない
    gh = FakeGh()
    _copy(monkeypatch, tmp_path, gh)
    assert gh.calls == []


def test_copy_to_github_closes_already_created_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(tmp_path, status_id=4)
    (project_dir / "github-issues.json").write_text(
        json.dumps({"P-1": {"number": 7, "comments": [], "labels": _LABELS}}),
        encoding="utf-8",
    )
    gh = FakeGh()

    _copy(monkeypatch, tmp_path, gh)

    assert [c["args"][:4] for c in gh.calls] == [["gh", "issue", "close", "7"]]
    assert _record(project_dir) == {
        "P-1": {"number": 7, "comments": [], "labels": _LABELS, "closed": True}
    }


@pytest.mark.parametrize("status_id", [1, 2, 3, 5])
def test_copy_to_github_keeps_open_unless_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path, status_id: int
) -> None:
    project_dir, _ = _write_project(tmp_path, status_id=status_id)
    gh = FakeGh()

    _copy(monkeypatch, tmp_path, gh)

    assert [c["args"][:3] for c in gh.calls] == [["gh", "issue", "create"]]
    assert "closed" not in _record(project_dir)["P-1"]


def test_copy_to_github_creates_missing_or_outdated_labels_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _write_project(tmp_path)
    gh = FakeGh()

    # 状態のラベルは色が異なり, 種別のラベルは大文字小文字違いで存在する扱い
    _copy(
        monkeypatch,
        tmp_path,
        gh,
        existing_labels=[
            {**_EXISTING_LABELS[0], "color": "000000"},
            {**_EXISTING_LABELS[1], "name": _LABELS[1].upper()},
        ],
    )

    assert [c["args"] for c in gh.calls[:-1]] == [
        ["gh", "label", "create", _LABELS[0], "-R", "o/r"]
        + ["--color", "ed8077", "--description", "Backlogの課題の状態", "--force"]
    ]
    assert gh.calls[-1]["args"][:3] == ["gh", "issue", "create"]


def test_copy_to_github_adds_labels_to_issue_created_before_label_support(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(tmp_path)
    (project_dir / "github-issues.json").write_text(
        json.dumps({"P-1": {"number": 7, "comments": []}}), encoding="utf-8"
    )
    gh = FakeGh()

    _copy(monkeypatch, tmp_path, gh)

    assert [c["args"] for c in gh.calls] == [
        ["gh", "issue", "edit", "7", "-R", "o/r"]
        + ["--add-label", _LABELS[0], "--add-label", _LABELS[1]]
    ]
    assert _record(project_dir) == {
        "P-1": {"number": 7, "comments": [], "labels": _LABELS}
    }

    # 再実行してもラベル付与済みのIssueには付与し直さない
    gh = FakeGh()
    _copy(monkeypatch, tmp_path, gh, existing_labels=[])
    assert gh.calls == []


@pytest.mark.parametrize(
    ("status_name", "color", "message"),
    [
        ("a,b", "#ed8077", "カンマ"),
        ("あ" * 47, "#ed8077", "長すぎ"),
        ("未対応", "red", "色が不正"),
    ],
)
def test_issue_labels_rejects_invalid_label(
    status_name: str, color: str, message: str
) -> None:
    info = {"issueKey": "P-1", "status": {"name": status_name, "color": color}}
    with pytest.raises(copy_to_github.CopyToGithubError, match=message):
        copy_to_github._issue_labels(info)


def test_copy_to_github_resumes_after_failure_without_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(
        tmp_path, comments=[_comment(1, "1件目"), _comment(2, "2件目")]
    )

    # 1回目: Issue作成と1件目のコメントは成功し, 2件目で失敗する
    with pytest.raises(copy_to_github.CopyToGithubError):
        _copy(monkeypatch, tmp_path, FakeGh(fail_on_call=3))
    assert _record(project_dir) == {
        "P-1": {"number": 42, "comments": [1], "labels": _LABELS, "pendingComment": 2}
    }

    # 2回目: 2件目は追加済みでないことを確認した上で, 2件目だけが追加される
    checked = []

    def is_comment_posted(repo_url: str, issue_number: int, marker: str) -> bool:
        checked.append((issue_number, marker))
        return False

    monkeypatch.setattr(copy_to_github, "_is_comment_posted", is_comment_posted)
    gh = FakeGh()
    _copy(monkeypatch, tmp_path, gh)

    assert checked == [(42, "Backlogコメント: P-1 (commentId: 2)")]

    assert len(gh.calls) == 1
    assert gh.calls[0]["args"][:4] == ["gh", "issue", "comment", "42"]
    assert gh.calls[0]["body"].startswith("2件目")
    assert _record(project_dir) == {
        "P-1": {"number": 42, "comments": [1, 2], "labels": _LABELS}
    }


def test_copy_to_github_splits_attachments_between_issue_and_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    attachments = [
        {"id": 1, "name": "issue.png", "size": 10},
        {"id": 2, "name": "comment.png", "size": 10},
        {"id": 3, "name": "仕様[v2].pdf", "size": 10},
    ]
    _, issue_dir = _write_project(
        tmp_path,
        attachments=attachments,
        comments=[_comment(1, "添付", attachment_ids=[2, 3, 9])],
    )
    for attachment in attachments:
        (issue_dir / str(attachment["id"])).mkdir()
        (issue_dir / str(attachment["id"]) / attachment["name"]).write_bytes(b"x")
    gh = FakeGh()

    _copy(monkeypatch, tmp_path, gh)

    issue_call, comment_call = gh.calls
    assert issue_call["attach_paths"] == [str(issue_dir / "1" / "issue.png")]
    assert comment_call["attach_paths"] == [str(issue_dir / "2" / "comment.png")]
    assert (
        "- [仕様\\[v2\\].pdf](../blob/main/PROJ/P-1/3/%E4%BB%95%E6%A7%98%5Bv2%5D.pdf)"
        in comment_call["body"]
    )
    assert "- deleted-9.zip (削除済み)" in comment_call["body"]


def test_issue_level_and_comment_attachments() -> None:
    attachment_by_id = {
        1: {"id": 1, "name": "a.png"},
        2: {"id": 2, "name": "b.png"},
    }
    comments = [_comment(1, "x", attachment_ids=[2, 5])]

    assert copy_to_github._issue_level_attachments(attachment_by_id, comments) == [
        {"id": 1, "name": "a.png"}
    ]
    assert copy_to_github._comment_attachments(comments[0], attachment_by_id) == [
        {"id": 2, "name": "b.png"},
        {"id": 5, "name": "deleted-5.zip", "deleted": True},
    ]


def test_list_unattachable_files_excludes_deleted_attachments(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, issue_dir = _write_project(
        tmp_path,
        attachments=[{"id": 1, "name": "doc.pdf", "size": 10}],
        comments=[_comment(1, None, attachment_ids=[9])],
    )
    (issue_dir / "1").mkdir()
    (issue_dir / "1" / "doc.pdf").write_bytes(b"x")

    copy_to_github.list_unattachable_files(
        working_dir=str(tmp_path), project_key="PROJ", issue_keys=None, null=True
    )

    captured = capsys.readouterr()
    assert captured.out == f"{issue_dir / '1' / 'doc.pdf'}\0"
    assert captured.err == ""


def test_build_attachment_link_normalizes_to_nfc() -> None:
    nfd = "バ.pdf"  # 「ハ」+ 結合用濁点
    nfc = "バ.pdf"  # 「バ」
    assert copy_to_github._build_attachment_link(
        "PROJ", "P-1", Path("7") / nfd, "main"
    ) == copy_to_github._build_attachment_link("PROJ", "P-1", Path("7") / nfc, "main")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@tanaka さん", "@\u200btanaka さん"),
        ("確認@tanaka", "確認@\u200btanaka"),
        ("(@team/sub)", "(@\u200bteam/sub)"),
        ("foo@example.com", "foo@example.com"),
        ("https://medium.com/@user", "https://medium.com/@user"),
        ("`@inline` と @out", "`@inline` と @\u200bout"),
        ("```\n@code\n```\n@after", "```\n@code\n```\n@\u200bafter"),
        ("@ 単独", "@ 単独"),
        ("#12 を参照", "#\u200b12 を参照"),
        ("課題#3と(#4)", "課題#\u200b3と(#\u200b4)"),
        ("https://example.com/page#1", "https://example.com/page#1"),
        ("https://example.com/#1", "https://example.com/#1"),
        ("&#123;", "&#123;"),
        ("# 見出し", "# 見出し"),
        ("#abc", "#abc"),
        ("`#1` と #2", "`#1` と #\u200b2"),
    ],
)
def test_escape_autolinks(text: str, expected: str) -> None:
    assert copy_to_github._escape_autolinks(text) == expected


def test_convert_text_escapes_mentions_for_both_formats() -> None:
    assert copy_to_github._convert_text("@a", "markdown", None) == "@\u200ba"
    assert copy_to_github._convert_text("@a", "backlog", None) == "@\u200ba  "


def test_escape_link_text() -> None:
    assert copy_to_github._escape_link_text("a_b*[c].png") == "a\\_b\\*\\[c\\].png"


def test_run_gh_fails_immediately_on_non_retryable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return _result(
            1, stderr="GraphQL: Body is too long (maximum is 65536 characters)"
        )

    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda _s: None)

    with pytest.raises(copy_to_github.CopyToGithubError, match="too long"):
        copy_to_github._run_gh(
            ["gh", "issue", "create"],
            body="body",
            attach_paths=[],
            error_message="失敗",
        )

    assert len(calls) == 1


def test_run_gh_rejects_too_long_body_without_calling_gh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        raise AssertionError("gh should not be called")

    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)

    with pytest.raises(copy_to_github.CopyToGithubError, match="本文が長すぎます"):
        copy_to_github._run_gh(
            ["gh", "issue", "create"],
            body="x" * (copy_to_github._MAX_BODY_LENGTH + 1),
            attach_paths=[],
            error_message="失敗",
        )


def test_run_gh_does_not_retry_when_previous_attempt_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return _result(1, stderr="HTTP 502: Bad Gateway")

    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda _s: None)

    output = copy_to_github._run_gh(
        ["gh", "issue", "create"],
        body="body",
        attach_paths=[],
        error_message="失敗",
        find_existing=lambda: "https://github.com/o/r/issues/7",
    )

    assert output == "https://github.com/o/r/issues/7"
    assert len(calls) == 1


def test_run_gh_retries_when_previous_attempt_did_not_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [_result(1, stderr="HTTP 502: Bad Gateway"), _result(0, stdout="ok")]
    monkeypatch.setattr(
        copy_to_github.subprocess, "run", lambda *a, **k: results.pop(0)
    )
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda _s: None)
    checks = []

    output = copy_to_github._run_gh(
        ["gh", "issue", "comment"],
        body="body",
        attach_paths=[],
        error_message="失敗",
        find_existing=lambda: checks.append(1),
    )

    assert output == "ok"
    assert checks == [1]


def test_run_gh_aborts_when_existence_cannot_be_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        return _result(1, stderr="HTTP 502: Bad Gateway")

    def find_existing() -> str | None:
        raise copy_to_github.CopyToGithubError("gh issue list に失敗しました")

    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)
    monkeypatch.setattr(copy_to_github.time, "sleep", lambda _s: None)

    with pytest.raises(copy_to_github.CopyToGithubError, match="確認できませんでした"):
        copy_to_github._run_gh(
            ["gh", "issue", "create"],
            body="body",
            attach_paths=[],
            error_message="失敗",
            find_existing=find_existing,
        )

    assert len(calls) == 1


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("GraphQL: Body is too long (maximum is 65536 characters)", True),
        ("HTTP 422: Validation Failed", True),
        ("GraphQL: Could not resolve to a Repository with the name 'o/r'.", True),
        ("HTTP 401: Bad credentials", True),
        ("To get started with GitHub CLI, please run:  gh auth login", True),
        ("HTTP 502: Bad Gateway", False),
        ("GraphQL: Something went wrong while executing your query.", False),
    ],
)
def test_is_non_retryable_error(stderr: str, expected: bool) -> None:
    assert copy_to_github._is_non_retryable_error(stderr) is expected


def test_find_created_issue_url_matches_footer_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = copy_to_github._issue_marker("P-1", 100)
    issues = [
        {
            "url": "https://github.com/o/r/issues/3",
            "body": "本文\n\n> Backlog課題: P-1 (issueId: 1000)",
        },
        {"url": "https://github.com/o/r/issues/2", "body": f"本文 {marker}"},
        {
            "url": "https://github.com/o/r/issues/1",
            "body": f"本文\r\n\r\n> {marker}\r\n> 作成者: a",
        },
    ]
    captured_args = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        captured_args.append(args)
        return _result(0, stdout=json.dumps(issues))

    monkeypatch.setattr(copy_to_github.subprocess, "run", fake_run)

    assert (
        copy_to_github._find_created_issue_url("o/r", marker)
        == "https://github.com/o/r/issues/1"
    )
    assert captured_args[0][:3] == ["gh", "issue", "list"]
    assert "--state" in captured_args[0] and "all" in captured_args[0]


def test_find_created_issue_url_returns_none_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        copy_to_github.subprocess, "run", lambda *a, **k: _result(0, stdout="[]")
    )
    assert copy_to_github._find_created_issue_url("o/r", "x") is None


def test_is_comment_posted(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = copy_to_github._comment_marker("P-1", 5)
    output = json.dumps({"comments": [{"body": "a"}, {"body": f"b\n\n> {marker}"}]})
    monkeypatch.setattr(
        copy_to_github.subprocess, "run", lambda *a, **k: _result(0, stdout=output)
    )

    assert copy_to_github._is_comment_posted("o/r", 42, marker)
    assert not copy_to_github._is_comment_posted(
        "o/r", 42, copy_to_github._comment_marker("P-1", 6)
    )


def test_find_existing_query_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        copy_to_github.subprocess,
        "run",
        lambda *a, **k: _result(1, stderr="HTTP 502"),
    )
    with pytest.raises(copy_to_github.CopyToGithubError, match="HTTP 502"):
        copy_to_github._find_created_issue_url("o/r", "x")


def test_copy_to_github_does_not_check_existence_without_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _write_project(tmp_path, comments=[_comment(1, "1件目")])

    def fail(*args: object) -> None:
        raise AssertionError("existence check should not be called")

    monkeypatch.setattr(copy_to_github, "_find_created_issue_url", fail)
    monkeypatch.setattr(copy_to_github, "_is_comment_posted", fail)

    _copy(monkeypatch, tmp_path, FakeGh())


def test_copy_to_github_records_pending_issue_while_creating(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(tmp_path)
    records_during_call = []

    class RecordingGh(FakeGh):
        def __call__(self, args, **kwargs):
            records_during_call.append(_record(project_dir))
            return super().__call__(args, **kwargs)

    _copy(monkeypatch, tmp_path, RecordingGh())

    assert records_during_call == [{"P-1": {"comments": [], "pendingIssue": True}}]
    assert _record(project_dir) == {
        "P-1": {"comments": [], "labels": _LABELS, "number": 42}
    }


def test_copy_to_github_skips_issue_created_by_interrupted_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(tmp_path)
    (project_dir / "github-issues.json").write_text(
        json.dumps({"P-1": {"comments": [], "pendingIssue": True}}), encoding="utf-8"
    )
    markers = []

    def find_created_issue_url(repo_url: str, marker: str) -> str | None:
        markers.append(marker)
        return "https://github.com/o/r/issues/7"

    monkeypatch.setattr(
        copy_to_github, "_find_created_issue_url", find_created_issue_url
    )
    gh = FakeGh()
    _copy(monkeypatch, tmp_path, gh)

    # 作成済みのIssueにラベルが付いているかは分からないため, 改めて付与する
    assert [c["args"] for c in gh.calls] == [
        ["gh", "issue", "edit", "7", "-R", "o/r"]
        + ["--add-label", _LABELS[0], "--add-label", _LABELS[1]]
    ]
    assert markers == ["Backlog課題: P-1 (issueId: 100)"]
    assert _record(project_dir) == {
        "P-1": {"comments": [], "number": 7, "labels": _LABELS}
    }


def test_copy_to_github_creates_issue_when_interrupted_run_did_not_create_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(tmp_path)
    (project_dir / "github-issues.json").write_text(
        json.dumps({"P-1": {"comments": [], "pendingIssue": True}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        copy_to_github, "_find_created_issue_url", lambda repo_url, marker: None
    )
    gh = FakeGh()
    _copy(monkeypatch, tmp_path, gh)

    assert len(gh.calls) == 1
    assert _record(project_dir) == {
        "P-1": {"comments": [], "labels": _LABELS, "number": 42}
    }


def test_copy_to_github_skips_comment_posted_by_interrupted_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(
        tmp_path, comments=[_comment(1, "1件目"), _comment(2, "2件目")]
    )
    (project_dir / "github-issues.json").write_text(
        json.dumps(
            {
                "P-1": {
                    "number": 42,
                    "comments": [],
                    "labels": _LABELS,
                    "pendingComment": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        copy_to_github,
        "_is_comment_posted",
        lambda repo_url, issue_number, marker: marker.endswith("(commentId: 1)"),
    )
    gh = FakeGh()
    _copy(monkeypatch, tmp_path, gh)

    assert len(gh.calls) == 1
    assert gh.calls[0]["body"].startswith("2件目")
    assert _record(project_dir) == {
        "P-1": {"number": 42, "comments": [1, 2], "labels": _LABELS}
    }


def test_copy_to_github_aborts_when_pending_check_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    project_dir, _ = _write_project(tmp_path)
    (project_dir / "github-issues.json").write_text(
        json.dumps({"P-1": {"comments": [], "pendingIssue": True}}), encoding="utf-8"
    )

    def find_created_issue_url(repo_url: str, marker: str) -> str | None:
        raise copy_to_github.CopyToGithubError("gh issue list に失敗しました")

    monkeypatch.setattr(
        copy_to_github, "_find_created_issue_url", find_created_issue_url
    )
    gh = FakeGh()
    with pytest.raises(copy_to_github.CopyToGithubError):
        _copy(monkeypatch, tmp_path, gh)

    assert gh.calls == []
    assert _record(project_dir) == {"P-1": {"comments": [], "pendingIssue": True}}


def test_list_unattachable_files_removes_duplicates(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, issue_dir = _write_project(
        tmp_path,
        attachments=[{"id": 1, "name": "doc.pdf", "size": 10}],
        # 同じ添付ファイルが複数のコメントの変更履歴に現れる
        comments=[
            _comment(1, None, attachment_ids=[1]),
            _comment(2, None, attachment_ids=[1]),
        ],
    )
    (issue_dir / "1").mkdir()
    (issue_dir / "1" / "doc.pdf").write_bytes(b"x")

    copy_to_github.list_unattachable_files(
        working_dir=str(tmp_path),
        project_key="PROJ",
        issue_keys=["P-1", "P-1"],
        null=False,
    )

    assert capsys.readouterr().out == f"{issue_dir / '1' / 'doc.pdf'}\n"
