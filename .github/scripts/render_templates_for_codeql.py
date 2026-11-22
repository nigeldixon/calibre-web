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

from jinja2 import ChainableUndefined, Environment

ROOT = Path.cwd()
TEMPLATE_ROOT = ROOT / "cps" / "themes"
OUTPUT_BASE = ROOT / "codeql-rendered"
OUTPUT_ROOT = OUTPUT_BASE / "templates"

# Lexing needs no loader and executes no template code. Parsing first rejects
# malformed templates instead of silently masking their syntax.
ENV = Environment(undefined=ChainableUndefined, keep_trailing_newline=True)

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


def render_template(text):
    """Return a JavaScript-parseable rendering of one template's text."""
    source = disable_whitespace_control(text)
    ENV.parse(source)

    output = []
    mode = "data"
    pending_newlines = 0
    for token in ENV.lex(source):
        if isinstance(token, tuple):  # Jinja 3.x
            _lineno, kind, value = token
        else:  # older JinjaToken-based API
            kind, value = token.type, token.value
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

    if pending_newlines:
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
