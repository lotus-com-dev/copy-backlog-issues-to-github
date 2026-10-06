import pytest

from copy_backlog_issues_to_github.backlog_markdown import (
    convert_backlog_notation_to_markdown,
)


def test_heading() -> None:
    text = "* 見出し1\n** 見出し2\n*** 見出し3"
    expected = "# 見出し1\n## 見出し2\n### 見出し3"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_bold_and_italic() -> None:
    assert convert_backlog_notation_to_markdown("''太字''") == "**太字**  "
    assert convert_backlog_notation_to_markdown("'''斜体'''") == "*斜体*  "


def test_strikethrough() -> None:
    assert convert_backlog_notation_to_markdown("%%取り消し%%") == "~~取り消し~~  "


def test_escaped_percent_is_not_converted_to_strikethrough() -> None:
    assert convert_backlog_notation_to_markdown(r"\%\%テキスト\%\%") == "%%テキスト%%  "


def test_bullet_list_with_nesting() -> None:
    text = "- 箇条書き1\n-- 箇条書き2-1\n--- 箇条書き2-1-1"
    expected = "- 箇条書き1\n  - 箇条書き2-1\n    - 箇条書き2-1-1"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_ordered_list_with_nesting() -> None:
    text = "+ 箇条書き1\n+ 箇条書き2\n++ ネスト"
    expected = "1. 箇条書き1\n1. 箇条書き2\n   1. ネスト"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_checklist() -> None:
    text = "- [ ] Item-A\n- [x] Item-B\n-- [X] Item-B-1"
    expected = "- [ ] Item-A\n- [x] Item-B\n  - [x] Item-B-1"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_single_line_quote() -> None:
    assert convert_backlog_notation_to_markdown(">引用テキスト") == "> 引用テキスト"


def test_quote_block_inline() -> None:
    assert (
        convert_backlog_notation_to_markdown("{quote}テキスト{/quote}")
        == "> テキスト  "
    )


def test_quote_block_multiline() -> None:
    text = "{quote}\n1行目\n2行目\n{/quote}"
    expected = "> 1行目  \n> 2行目  "
    assert convert_backlog_notation_to_markdown(text) == expected


def test_code_block_inline_without_language() -> None:
    assert (
        convert_backlog_notation_to_markdown("{code}int x = 1;{/code}")
        == "```\nint x = 1;\n```"
    )


def test_code_block_inline_with_language() -> None:
    assert (
        convert_backlog_notation_to_markdown("{code:java}int x = 1;{/code}")
        == "```java\nint x = 1;\n```"
    )


def test_code_block_multiline_is_not_inline_converted() -> None:
    text = "{code:java}\nString s = \"''not bold''\";\nint y = 2;\n{/code}"
    expected = "```java\nString s = \"''not bold''\";\nint y = 2;\n```"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_table_with_header_marker() -> None:
    text = "| セル1 | セル2 |h\n| データ1 | データ2 |\n| データ3 | データ4 |"
    expected = (
        "| セル1 | セル2 |\n| --- | --- |\n| データ1 | データ2 |\n| データ3 | データ4 |"
    )
    assert convert_backlog_notation_to_markdown(text) == expected


def test_table_without_header_marker_defaults_to_first_row() -> None:
    text = "| a | b |\n| c | d |"
    expected = "| a | b |\n| --- | --- |\n| c | d |"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_line_break_tag() -> None:
    assert convert_backlog_notation_to_markdown("1行目&br;2行目") == "1行目<br>2行目  "


def test_url_link() -> None:
    assert (
        convert_backlog_notation_to_markdown("[[https://backlog.jp/ja/]]")
        == "<https://backlog.jp/ja/>  "
    )


def test_labeled_link_with_angle_bracket_separator() -> None:
    assert (
        convert_backlog_notation_to_markdown("[[Backlog>https://backlog.jp/ja/]]")
        == "[Backlog](https://backlog.jp/ja/)  "
    )


def test_labeled_link_with_colon_separator() -> None:
    assert (
        convert_backlog_notation_to_markdown("[[Backlog:https://backlog.jp/ja/]]")
        == "[Backlog](https://backlog.jp/ja/)  "
    )


def test_issue_link_with_domain() -> None:
    assert (
        convert_backlog_notation_to_markdown("[[BLG-87]]", domain="example.backlog.jp")
        == "[BLG-87](https://example.backlog.jp/view/BLG-87)  "
    )


def test_issue_link_without_domain_is_left_as_is() -> None:
    assert convert_backlog_notation_to_markdown("[[BLG-87]]") == "BLG-87  "


def test_plain_line_breaks_are_preserved_with_trailing_spaces() -> None:
    text = "1行目\n2行目"
    expected = "1行目  \n2行目  "
    assert convert_backlog_notation_to_markdown(text) == expected


def test_blank_line_separates_paragraphs() -> None:
    text = "段落1\n\n段落2"
    expected = "段落1  \n\n段落2  "
    assert convert_backlog_notation_to_markdown(text) == expected


def test_heading_up_to_level_6() -> None:
    assert convert_backlog_notation_to_markdown("**** 見出し4") == "#### 見出し4"
    assert convert_backlog_notation_to_markdown("****** 見出し6") == "###### 見出し6"


def test_ordered_and_bullet_list_mixed_nesting() -> None:
    text = "+ a\n++ b\n--- c\n- d\n-- e"
    expected = "1. a\n   1. b\n      - c\n- d\n  - e"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_paragraph_after_quote_is_separated_by_blank_line() -> None:
    assert convert_backlog_notation_to_markdown(">引用\n本文") == "> 引用\n\n本文  "


def test_paragraph_after_quote_block_is_separated_by_blank_line() -> None:
    text = "{quote}\n引用\n{/quote}\n本文"
    assert convert_backlog_notation_to_markdown(text) == "> 引用  \n\n本文  "


def test_paragraph_after_list_is_separated_by_blank_line() -> None:
    assert convert_backlog_notation_to_markdown("- a\n本文") == "- a\n\n本文  "


def test_table_is_separated_from_surrounding_paragraphs() -> None:
    text = "前\n| a | b |\n後"
    expected = "前  \n\n| a | b |\n| --- | --- |\n\n後  "
    assert convert_backlog_notation_to_markdown(text) == expected


def test_table_header_cells_with_tilde() -> None:
    text = "|~見出し1|~見出し2|\n|~行見出し|値|"
    expected = "| 見出し1 | 見出し2 |\n| --- | --- |\n| **行見出し** | 値 |"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_table_row_with_trailing_whitespace() -> None:
    text = "| a | b |h  \n| c | d | "
    expected = "| a | b |\n| --- | --- |\n| c | d |"
    assert convert_backlog_notation_to_markdown(text) == expected


def test_crlf_line_endings_are_normalized() -> None:
    text = "1行目\r\n{code}\r\nfoo\r\n{/code}\r\n| a |h\r\n| b |"
    expected = "1行目  \n```\nfoo\n```\n| a |\n| --- |\n| b |"
    assert convert_backlog_notation_to_markdown(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# 見出しではない", "\\# 見出しではない  "),
        ("1. 番号付きリストではない", "1\\. 番号付きリストではない  "),
        ("2) これも", "2\\) これも  "),
        ("10年後", "10年後  "),
        ("```フェンスではない", "\\```フェンスではない  "),
        ("<div>", "\\<div>  "),
        ("[ref]: https://example.com", "\\[ref]: https://example.com  "),
        ("_a_", "\\_a_  "),
        (
            "    コードブロックではない",
            "&nbsp;&nbsp;&nbsp;&nbsp;コードブロックではない  ",
        ),
        ("\tタブ", "&nbsp;&nbsp;&nbsp;&nbsp;タブ  "),
        ("  - リストではない", "&nbsp;&nbsp;\\- リストではない  "),
        ("''太字''から始まる", "**太字**から始まる  "),
        ("[[https://example.com]] から始まる", "<https://example.com> から始まる  "),
    ],
)
def test_paragraph_line_start_is_escaped(text: str, expected: str) -> None:
    assert convert_backlog_notation_to_markdown(text) == expected


def test_setext_underline_is_escaped() -> None:
    assert convert_backlog_notation_to_markdown("段落\n===") == "段落  \n\\===  "
    assert convert_backlog_notation_to_markdown("段落\n-") == "段落  \n\\-  "


def test_list_and_quote_content_start_is_escaped() -> None:
    assert convert_backlog_notation_to_markdown("- # x") == "- \\# x"
    assert convert_backlog_notation_to_markdown("+ 1. x") == "1. 1\\. x"
    assert convert_backlog_notation_to_markdown("> - x") == "> \\- x"
    assert convert_backlog_notation_to_markdown("{quote}\n# x\n{/quote}") == "> \\# x  "


def test_heading_and_table_content_is_not_escaped() -> None:
    assert convert_backlog_notation_to_markdown("* #1") == "# #1"
    assert (
        convert_backlog_notation_to_markdown("| # | - |") == "| # | - |\n| --- | --- |"
    )
