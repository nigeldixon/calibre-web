#!/usr/bin/env python3
"""Create JavaScript-parseable copies of Jinja templates for CodeQL.

Raw Jinja/Underscore template syntax is not valid JavaScript. CodeQL's
JavaScript extractor parses inline scripts and event-handler attributes, so
handing it unrendered templates produces extraction errors even when the
templates themselves are valid. This script uses Jinja's own lexer to
materialize a copy of every theme template with template syntax replaced by
inert values. Literal text (including every branch of a conditional and macro
bodies) is kept unchanged, and each replacement preserves the original newline
count so alert line numbers still point at the source template. CodeQL analyzes
the copies, while the original templates stay untouched.
"""

import re
import shutil
from pathlib import Path
from types import SimpleNamespace

from jinja2.lexer import Lexer

ROOT = Path.cwd()
TEMPLATE_ROOT = ROOT / "cps" / "themes"
OUTPUT_BASE = ROOT / "codeql-rendered"
OUTPUT_ROOT = OUTPUT_BASE / "templates"

# Only Jinja's lexer is needed: nothing is ever rendered. Supply the settings
# the lexer reads directly instead of creating a full Environment, which would
# imply template rendering and trip XSS-focused static analysis.
LEXER = Lexer(
    SimpleNamespace(
        block_start_string="{%",
        block_end_string="%}",
        variable_start_string="{{",
        variable_end_string="}}",
        comment_start_string="{#",
        comment_end_string="#}",
        line_statement_prefix=None,
        line_comment_prefix=None,
        trim_blocks=False,
        lstrip_blocks=False,
        newline_sequence="\n",
        keep_trailing_newline=True,
    )
)

MODE_AFTER_BEGIN = {
    "variable_begin": "variable",
    "block_begin": "block",
    "comment_begin": "comment",
    "raw_begin": "raw",
}
REPLACEMENT_AT_END = {
    "variable_end": "0",
    "block_end": "",
    "comment_end": "",
    "raw_end": "",
}

# The lexer validates delimiters and comments but not the pairing of block
# tags. Mirror Jinja's block openers so a malformed template fails loudly
# instead of being silently masked.
BLOCK_CLOSERS = {
    "if": "endif",
    "for": "endfor",
    "macro": "endmacro",
    "call": "endcall",
    "block": "endblock",
    "filter": "endfilter",
    "with": "endwith",
    "autoescape": "endautoescape",
    "trans": "endtrans",
    "translate": "endtranslate",
    "apply": "endapply",
    "set": "endset",
}

# Jinja's lexer removes whitespace adjacent to -/+ control markers. Keep it so
# every source newline survives into the rendered copy and CodeQL line numbers
# still point at the original template.
WHITESPACE_CONTROL = (
    (re.compile(r"(\{\{|\{%|\{#)[+-]"), r"\1"),
    (re.compile(r"[+-](\}\}|%\}|#\})"), r"\1"),
)

# Underscore-style templates are plain data to Jinja, so they still need an
# explicit pass. As above, each replacement keeps the matched newline count.
ERB_PATTERNS = (
    (re.compile(r"<%=\s*.*?\s*%>", re.S), "0"),
    (re.compile(r"<%[-]?.*?[-]?%>", re.S), ""),
)


def disable_whitespace_control(text):
    """Remove Jinja trim markers without removing the whitespace they trim."""
    for pattern, replacement in WHITESPACE_CONTROL:
        text = pattern.sub(replacement, text)
    return text


def validate_blocks(tokens):
    """Reject unclosed or mismatched block tags in a lexed template."""
    stack = []
    position = 0
    while position < len(tokens):
        _lineno, kind, _value = tokens[position]
        if kind not in ("block_begin", "raw_begin"):
            position += 1
            continue

        end_kind = "raw_end" if kind == "raw_begin" else "block_end"
        body = []
        position += 1
        while position < len(tokens) and tokens[position][1] != end_kind:
            body.append(tokens[position])
            position += 1
        if position == len(tokens):
            raise ValueError("Unexpected end of template: unclosed block tag")
        position += 1

        names = [value for _, token_kind, value in body if token_kind == "name"]
        tag = names[0] if names else ""
        # {% set name = value %} is complete on one line; only {% set name %}
        # opens a block that needs {% endset %}.
        inline_set = tag == "set" and any(
            token_kind == "operator" and value == "="
            for _, token_kind, value in body
        )
        if tag in BLOCK_CLOSERS and not inline_set:
            stack.append(BLOCK_CLOSERS[tag])
        elif tag.startswith("end"):
            if not stack or stack[-1] != tag:
                raise ValueError(f"Unexpected end tag {tag!r}")
            stack.pop()
        elif tag in ("else", "elif") and not stack:
            raise ValueError(f"Unexpected tag {tag!r}")

    if stack:
        raise ValueError(
            f"Unexpected end of template: expected {stack[-1]}, got end of template"
        )


def render_template(text):
    """Return a JavaScript-parseable rendering of one template's text."""
    source = disable_whitespace_control(text)
    tokens = list(LEXER.tokeniter(source, None))
    validate_blocks(tokens)

    output = []
    mode = "data"
    pending_newlines = 0
    for _lineno, kind, value in tokens:
        newlines = value.count("\n")

        if kind == "data" and mode in ("data", "raw"):
            output.append(value)
        elif kind in MODE_AFTER_BEGIN:
            mode = MODE_AFTER_BEGIN[kind]
            pending_newlines += newlines
        elif kind in REPLACEMENT_AT_END:
            pending_newlines += newlines
            output.append(REPLACEMENT_AT_END[kind] + "\n" * pending_newlines)
            pending_newlines = 0
            mode = "data"
        else:
            # Expression tokens and comment bodies are masked; count their
            # newlines so the next emitted line lands on the original line.
            pending_newlines += newlines

    output.append("\n" * pending_newlines)

    rendered = "".join(output)
    for pattern, replacement in ERB_PATTERNS:
        rendered = pattern.sub(
            lambda match, value=replacement: value + "\n" * match.group(0).count("\n"),
            rendered,
        )
    return rendered


def render_all_templates():
    """Write rendered copies of all theme templates and return their count."""
    if OUTPUT_BASE.exists():
        shutil.rmtree(OUTPUT_BASE)

    templates = sorted(TEMPLATE_ROOT.glob("**/templates/**/*.html"))
    for source in templates:
        relative = source.relative_to(TEMPLATE_ROOT)
        target = OUTPUT_ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            render_template(source.read_text(encoding="utf-8")), encoding="utf-8"
        )
    return len(templates)


def main():
    count = render_all_templates()
    print(f"Rendered {count} Jinja template(s) for CodeQL JavaScript analysis.")


if __name__ == "__main__":
    main()
