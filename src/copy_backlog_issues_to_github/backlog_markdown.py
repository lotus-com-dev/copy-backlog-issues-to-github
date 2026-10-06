"""Backlog記法をMarkdown記法(GitHub Flavored Markdown)へ変換する."""

from __future__ import annotations

import re

_ESCAPED_PERCENT_PLACEHOLDER = "@@ESCAPED_PERCENT@@"

_ESCAPED_PERCENT_RE = re.compile(r"\\%\\%(.*?)\\%\\%")
_STRIKETHROUGH_RE = re.compile(r"%%(.*?)%%")
_ITALIC_RE = re.compile(r"'''(.*?)'''")
_BOLD_RE = re.compile(r"''(.*?)''")
_LINK_RE = re.compile(r"\[\[(.*?)\]\]")

_HEADING_RE = re.compile(r"^(\*{1,6})\s*(.*)$")
_CHECKLIST_RE = re.compile(r"^(-+)\s+\[([ xX])\]\s*(.*)$")
_BULLET_LIST_RE = re.compile(r"^(-+)\s+(.*)$")
_ORDERED_LIST_RE = re.compile(r"^(\++)\s+(.*)$")
_SINGLE_LINE_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|(h)?\s*$")

_CODE_BLOCK_INLINE_RE = re.compile(r"^\{code(?::(\w+))?\}(.*)\{/code\}$")
_CODE_BLOCK_OPEN_RE = re.compile(r"^\{code(?::(\w+))?\}\s*$")
_CODE_BLOCK_CLOSE_RE = re.compile(r"^\{/code\}\s*$")

_QUOTE_BLOCK_INLINE_RE = re.compile(r"^\{quote\}(.*)\{/quote\}$")
_QUOTE_BLOCK_OPEN_RE = re.compile(r"^\{quote\}\s*$")
_QUOTE_BLOCK_CLOSE_RE = re.compile(r"^\{/quote\}\s*$")

# Markdownではこれらのブロックの直後に通常行が続くと, 遅延継続行として
# ブロックに取り込まれてしまうため, 間に空行を挟む.
_BLOCKS_ABSORBING_FOLLOWING_LINE = {"list", "quote", "table"}

# リストマーカーの幅. ネストしたリストは親の本文開始位置までインデントする必要がある.
_BULLET_MARKER_WIDTH = len("- ")
_ORDERED_MARKER_WIDTH = len("1. ")

# 行頭にあるとMarkdownのブロック記法 (見出し, 引用, リスト, コードフェンス, 区切り線,
# Setext見出し, HTMLブロック) として解釈され得る文字
_BLOCK_START_CHARS = frozenset("#>-+*=_`~<")
_LEADING_WHITESPACE_RE = re.compile(r"^[ \t]+")
_ORDERED_LIST_MARKER_RE = re.compile(r"^(\d{1,9})([.)])(?=\s|$)")
_LINK_REFERENCE_DEFINITION_RE = re.compile(r"^\[[^\]]+\]:")


def convert_backlog_notation_to_markdown(
    text: str, *, domain: str | None = None
) -> str:
    """Backlog記法のテキストをMarkdown記法に変換する.

    `domain` を指定すると `[[課題キー]]` 形式の課題リンクを
    `https://<domain>/view/<課題キー>` へのリンクに変換する.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output_lines: list[str] = []
    prev_kind: str | None = None
    # 現在のリストの各階層のマーカー幅
    list_widths: list[int] = []

    def emit(kind: str, new_lines: list[str]) -> None:
        nonlocal prev_kind
        needs_blank_line = (
            kind == "paragraph" and prev_kind in _BLOCKS_ABSORBING_FOLLOWING_LINE
        ) or (kind == "table" and prev_kind in {"paragraph", "list", "quote"})
        if needs_blank_line:
            output_lines.append("")
        if kind != "list":
            list_widths.clear()
        output_lines.extend(new_lines)
        prev_kind = kind

    def list_indent(level: int, marker_width: int) -> str:
        del list_widths[level - 1 :]
        while len(list_widths) < level - 1:
            list_widths.append(_BULLET_MARKER_WIDTH)
        indent = " " * sum(list_widths)
        list_widths.append(marker_width)
        return indent

    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        inline_code_match = _CODE_BLOCK_INLINE_RE.match(line)
        if inline_code_match:
            lang, content = inline_code_match.groups()
            emit("code", _render_code_block(lang, [content]))
            i += 1
            continue

        code_open_match = _CODE_BLOCK_OPEN_RE.match(line)
        if code_open_match:
            lang = code_open_match.group(1)
            content_lines, consumed = _collect_until(lines, i + 1, _CODE_BLOCK_CLOSE_RE)
            emit("code", _render_code_block(lang, content_lines))
            i = consumed
            continue

        inline_quote_match = _QUOTE_BLOCK_INLINE_RE.match(line)
        if inline_quote_match:
            content = inline_quote_match.group(1)
            emit("quote", _render_quote_block([content], domain=domain))
            i += 1
            continue

        if _QUOTE_BLOCK_OPEN_RE.match(line):
            content_lines, consumed = _collect_until(
                lines, i + 1, _QUOTE_BLOCK_CLOSE_RE
            )
            emit("quote", _render_quote_block(content_lines, domain=domain))
            i = consumed
            continue

        if _TABLE_ROW_RE.match(line):
            table_lines, consumed = _collect_table_block(lines, i)
            emit("table", _render_table_block(table_lines, domain=domain))
            i = consumed
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            stars, content = heading_match.groups()
            level = len(stars)
            emit(
                "heading", [f"{'#' * level} {_convert_inline(content, domain=domain)}"]
            )
            i += 1
            continue

        checklist_match = _CHECKLIST_RE.match(line)
        if checklist_match:
            markers, check, content = checklist_match.groups()
            indent = list_indent(len(markers), _BULLET_MARKER_WIDTH)
            mark = "x" if check.lower() == "x" else " "
            emit(
                "list",
                [
                    f"{indent}- [{mark}] {_convert_block_content(content, domain=domain)}"
                ],
            )
            i += 1
            continue

        bullet_match = _BULLET_LIST_RE.match(line)
        if bullet_match:
            markers, content = bullet_match.groups()
            indent = list_indent(len(markers), _BULLET_MARKER_WIDTH)
            emit(
                "list", [f"{indent}- {_convert_block_content(content, domain=domain)}"]
            )
            i += 1
            continue

        ordered_match = _ORDERED_LIST_RE.match(line)
        if ordered_match:
            markers, content = ordered_match.groups()
            indent = list_indent(len(markers), _ORDERED_MARKER_WIDTH)
            emit(
                "list", [f"{indent}1. {_convert_block_content(content, domain=domain)}"]
            )
            i += 1
            continue

        single_line_quote_match = _SINGLE_LINE_QUOTE_RE.match(line)
        if single_line_quote_match:
            content = single_line_quote_match.group(1)
            emit("quote", [f"> {_convert_block_content(content, domain=domain)}"])
            i += 1
            continue

        if line.strip() == "":
            emit("blank", [line])
            i += 1
            continue

        # 通常の段落行. Backlogでは単純な改行がそのまま改行として表示されるため,
        # Markdownでも改行として表示されるよう行末にスペース2つを付与する.
        emit("paragraph", [f"{_convert_block_content(line, domain=domain)}  "])
        i += 1

    return "\n".join(output_lines)


def _collect_until(
    lines: list[str], start: int, close_pattern: re.Pattern[str]
) -> tuple[list[str], int]:
    content_lines: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        if close_pattern.match(lines[i]):
            return content_lines, i + 1
        content_lines.append(lines[i])
        i += 1
    return content_lines, i


def _render_code_block(lang: str | None, content_lines: list[str]) -> list[str]:
    fence = f"```{lang}" if lang else "```"
    return [fence, *content_lines, "```"]


def _render_quote_block(content_lines: list[str], *, domain: str | None) -> list[str]:
    return [
        f"> {_convert_block_content(line, domain=domain)}  " for line in content_lines
    ]


def _collect_table_block(lines: list[str], start: int) -> tuple[list[str], int]:
    table_lines: list[str] = []
    i = start
    n = len(lines)
    while i < n and _TABLE_ROW_RE.match(lines[i]):
        table_lines.append(lines[i])
        i += 1
    return table_lines, i


def _render_table_block(table_lines: list[str], *, domain: str | None) -> list[str]:
    rows: list[list[str]] = []
    header_index: int | None = None

    for row_index, line in enumerate(table_lines):
        match = _TABLE_ROW_RE.match(line)
        assert match is not None
        cells_text, header_marker = match.groups()
        raw_cells = [cell.strip() for cell in cells_text.split("|")]

        # 行末の h はヘッダー行, セル先頭の ~ はヘッダーセルを表す.
        # Markdownはセル単位のヘッダーに対応していないため, 全セルが ~ の行はヘッダー行とし,
        # それ以外の行の ~ セルは太字で表現する.
        is_header_row = bool(header_marker) or all(
            cell.startswith("~") for cell in raw_cells
        )
        cells = []
        for cell in raw_cells:
            is_header_cell = cell.startswith("~")
            content = _convert_inline(cell.removeprefix("~").strip(), domain=domain)
            if is_header_cell and not is_header_row and content:
                content = f"**{content}**"
            cells.append(content)
        rows.append(cells)

        if is_header_row and header_index is None:
            header_index = row_index

    if header_index is None:
        header_index = 0
    header = rows[header_index]
    data_rows = [row for index, row in enumerate(rows) if index != header_index]

    output = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join(['---'] * len(header))} |",
    ]
    for row in data_rows:
        output.append(f"| {' | '.join(row)} |")
    return output


def _convert_block_content(text: str, *, domain: str | None) -> str:
    """段落, リスト項目, 引用の本文を変換する."""
    return _convert_inline(_escape_block_start(text), domain=domain)


def _escape_block_start(text: str) -> str:
    """Backlogでは通常の文字である行頭の文字が, Markdownのブロック記法として解釈されないよう
    エスケープする."""
    leading_whitespace = _LEADING_WHITESPACE_RE.match(text)
    if leading_whitespace:
        # 4文字以上の字下げはコードブロックになり, 字下げ後の記号はリスト等になるため,
        # 字下げは&nbsp;で表現する
        indent = leading_whitespace.group(0).replace("\t", "    ")
        rest = text[leading_whitespace.end() :]
        return "&nbsp;" * len(indent) + _escape_block_start(rest)

    ordered_list_marker = _ORDERED_LIST_MARKER_RE.match(text)
    if ordered_list_marker:
        digits, delimiter = ordered_list_marker.groups()
        return f"{digits}\\{delimiter}{text[ordered_list_marker.end() :]}"

    if text[:1] in _BLOCK_START_CHARS or _LINK_REFERENCE_DEFINITION_RE.match(text):
        return "\\" + text

    return text


def _convert_inline(text: str, *, domain: str | None) -> str:
    text = _ESCAPED_PERCENT_RE.sub(
        _ESCAPED_PERCENT_PLACEHOLDER + r"\1" + _ESCAPED_PERCENT_PLACEHOLDER, text
    )
    text = _ITALIC_RE.sub(r"*\1*", text)
    text = _BOLD_RE.sub(r"**\1**", text)
    text = _STRIKETHROUGH_RE.sub(r"~~\1~~", text)
    text = text.replace("&br;", "<br>")
    text = _LINK_RE.sub(lambda m: _convert_link(m.group(1), domain=domain), text)
    text = text.replace(_ESCAPED_PERCENT_PLACEHOLDER, "%%")
    return text


def _convert_link(inner: str, *, domain: str | None) -> str:
    if inner.startswith(("http://", "https://")):
        return f"<{inner}>"

    if ">" in inner:
        label, _, url = inner.partition(">")
        return f"[{label}]({url})"

    if ":" in inner:
        label, _, url = inner.partition(":")
        return f"[{label}]({url})"

    if domain:
        return f"[{inner}](https://{domain}/view/{inner})"

    return inner
