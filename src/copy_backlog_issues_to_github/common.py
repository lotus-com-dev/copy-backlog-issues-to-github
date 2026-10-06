from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class SizeMismatchError(OSError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_chunks(path, [content])


def atomic_write_chunks(
    path: Path, chunks: Iterable[bytes], *, expected_size: int | None = None
) -> int:
    """一時ファイルに書き込んでから置き換えることで, 書き込み中断による破損を避ける.

    `expected_size` を指定した場合, 書き込んだサイズが一致しなければ例外を送出し,
    `path` には何も書き込まない.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".part")
    try:
        written = 0
        with os.fdopen(fd, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
                written += len(chunk)
        if expected_size is not None and written != expected_size:
            raise SizeMismatchError(
                f"{path} の書き込みサイズ({written})が期待値({expected_size})と一致しません"
            )
        os.replace(tmp_path, path)
        return written
    except BaseException:
        os.unlink(tmp_path)
        raise


def filter_target_issues(
    issues: list[dict[str, Any]], issue_keys: list[str] | None
) -> list[dict[str, Any]]:
    if not issue_keys:
        return issues

    target_issue_keys = set(issue_keys)
    target_issues = [
        issue for issue in issues if issue["issueKey"] in target_issue_keys
    ]

    found_keys = {issue["issueKey"] for issue in target_issues}
    for missing_key in sorted(target_issue_keys - found_keys):
        print(
            f"警告: 課題 {missing_key} は課題一覧に見つかりませんでした",
            file=sys.stderr,
        )

    return target_issues


def attachment_filename(attachment: dict[str, Any]) -> str:
    """添付ファイルのローカル保存名. 課題ディレクトリの外を指さないよう無害化する."""
    name = Path(attachment["name"]).name
    if name in ("", ".", ".."):
        raise ValueError(f"添付ファイル名が不正です: {attachment['name']!r}")
    return name


def attachment_relative_path(attachment: dict[str, Any]) -> Path:
    """課題ディレクトリからの添付ファイルの相対パス.

    同じ課題に同名の添付ファイルが複数あっても衝突しないよう, 添付IDのディレクトリに保存する.
    """
    return Path(str(attachment["id"])) / attachment_filename(attachment)


_PROGRESS_PRINT_INTERVAL_SECONDS = 30.0
_PROGRESS_HISTORY_SIZE = 30


class ProgressPrinter:
    """進捗状況プリンター.

    updateを呼んだ際, 前回の出力から一定時間経過しているか全件完了していれば進捗状況を出力する.
    残り時間は, 直近30件の更新履歴から推定する.
    """

    def __init__(
        self, total: int, *, interval: float = _PROGRESS_PRINT_INTERVAL_SECONDS
    ) -> None:
        if total < 0:
            raise ValueError(f"totalは0以上である必要があります: {total}")
        self._total = total
        self._current = 0
        self._interval = interval

        now = time.monotonic()
        self._last_printed_time: float | None = None
        self._last_update_time = now
        # 更新履歴 [(完了数, 前回の更新からの経過秒数)]
        self._update_history: deque[tuple[int, float]] = deque(
            maxlen=_PROGRESS_HISTORY_SIZE
        )

    def update(self, increment: int = 1) -> None:
        if increment < 0:
            raise ValueError(f"incrementは0以上である必要があります: {increment}")
        self._current += increment

        now = time.monotonic()
        self._update_history.append((increment, now - self._last_update_time))
        self._last_update_time = now

        is_finished = self._current >= self._total
        if (
            not is_finished
            and self._last_printed_time is not None
            and now - self._last_printed_time < self._interval
        ):
            return
        self._last_printed_time = now

        percent = self._current / self._total * 100 if self._total else 100.0
        message = f"進捗: {self._current}/{self._total} ({percent:.1f}%) 完了"
        if not is_finished:
            remaining_secs = self._remaining_secs()
            if remaining_secs is None:
                message += ". 残り時間不明"
            else:
                message += f". 残り {format_duration(remaining_secs)}"
        print(message)

    def _remaining_secs(self) -> float | None:
        total_count = sum(count for count, _ in self._update_history)
        total_elapsed = sum(elapsed for _, elapsed in self._update_history)
        if total_count == 0:
            return None

        secs_per_item = total_elapsed / total_count
        return (self._total - self._current) * secs_per_item


def format_duration(secs: float) -> str:
    """秒数を「H時間M分S秒」形式にする. 0時間なら時間を, 0時間0分なら時間と分を省略する."""
    hours, rest = divmod(round(secs), 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}時間{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"
