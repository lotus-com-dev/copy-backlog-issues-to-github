from __future__ import annotations

from pathlib import Path

import pytest

from copy_backlog_issues_to_github.common import (
    atomic_write_chunks,
    atomic_write_json,
    attachment_filename,
    attachment_relative_path,
    filter_target_issues,
    format_duration,
    read_json,
)


def test_atomic_write_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "data.json"
    atomic_write_json(path, {"キー": [1, 2]})

    assert read_json(path) == {"キー": [1, 2]}
    assert list(path.parent.iterdir()) == [path]


def test_atomic_write_chunks_leaves_nothing_when_interrupted(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"

    def chunks():
        yield b"partial"
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        atomic_write_chunks(path, chunks())

    assert list(tmp_path.iterdir()) == []


def test_atomic_write_chunks_rejects_size_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"

    with pytest.raises(OSError, match="一致しません"):
        atomic_write_chunks(path, [b"abc"], expected_size=4)

    assert list(tmp_path.iterdir()) == []


def test_atomic_write_chunks_keeps_existing_file_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"old")

    with pytest.raises(OSError):
        atomic_write_chunks(path, [b"new"], expected_size=10)

    assert path.read_bytes() == b"old"


def test_filter_target_issues_warns_missing_keys_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    issues = [{"issueKey": "P-1"}, {"issueKey": "P-2"}]

    result = filter_target_issues(issues, ["P-2", "P-9"])

    assert result == [{"issueKey": "P-2"}]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "P-9" in captured.err


def test_filter_target_issues_returns_all_without_keys() -> None:
    issues = [{"issueKey": "P-1"}]
    assert filter_target_issues(issues, None) is issues


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("image.png", "image.png"),
        ("../../etc/passwd", "passwd"),
        ("/abs/path.txt", "path.txt"),
    ],
)
def test_attachment_filename_strips_directories(name: str, expected: str) -> None:
    assert attachment_filename({"name": name}) == expected


@pytest.mark.parametrize("name", ["", ".", "..", "foo/.."])
def test_attachment_filename_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError):
        attachment_filename({"name": name})


def test_attachment_relative_path_uses_attachment_id_directory() -> None:
    assert attachment_relative_path({"id": 12, "name": "../a.png"}) == Path("12/a.png")


@pytest.mark.parametrize(
    ("secs", "expected"),
    [
        (0, "0秒"),
        (59.4, "59秒"),
        (60, "1分0秒"),
        (3599, "59分59秒"),
        (3600, "1時間0分0秒"),
        (3661, "1時間1分1秒"),
        (90061, "25時間1分1秒"),
    ],
)
def test_format_duration(secs: float, expected: str) -> None:
    assert format_duration(secs) == expected
