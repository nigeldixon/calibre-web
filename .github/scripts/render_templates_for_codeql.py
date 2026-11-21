#!/usr/bin/env python3
"""Create JavaScript-parseable copies of Jinja templates for CodeQL.

Raw Jinja/Underscore template syntax is not valid JavaScript. CodeQL's
JavaScript extractor parses inline scripts and event-handler attributes, so
handing it unrendered templates produces extraction errors even when the
templates themselves are valid. This script materializes a copy of every theme
template with template expressions replaced by inert values and template
control syntax removed. CodeQL analyzes the copies, while the original
templates stay untouched.
"""

import re
import shutil
from pathlib import Path

ROOT = Path.cwd()
TEMPLATE_ROOT = ROOT / "cps" / "themes"
OUTPUT_BASE = ROOT / "codeql-rendered"
OUTPUT_ROOT = OUTPUT_BASE / "templates"

# Replace each template expression with an inert JavaScript value, and remove
# template control tags and comments. Replacement keeps the matched text's
# newline count so alert line numbers still point at the original template.
PATTERNS = (
    (re.compile(r"\{\{-?.*?-?\}\}", re.S), "0"),
    (re.compile(r"\{%-?.*?-?%\}", re.S), ""),
    (re.compile(r"\{#-?.*?-?#\}", re.S), ""),
    (re.compile(r"<%=\s*.*?\s*%>", re.S), "0"),
    (re.compile(r"<%[-]?.*?[-]?%>", re.S), ""),
)


def render_template(text):
    """Return a JavaScript-parseable rendering of one template's text."""
    for pattern, replacement in PATTERNS:
        text = pattern.sub(
            lambda match, value=replacement: value + "\n" * match.group(0).count("\n"),
            text,
        )
    return text


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
