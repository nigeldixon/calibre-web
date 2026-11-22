import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


JS_TYPES = {"", "text/javascript", "application/javascript", "module"}


class ScriptSnippetParser(HTMLParser):
    """Collect script bodies and event handlers using a real HTML parser.

    Inline content of external scripts is included too: CodeQL parses it even
    though browsers ignore it, so invalid JS there can break extraction.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.snippets = []
        self._script_parts = None

    def _record_handlers(self, attrs):
        for name, value in attrs:
            if name and name.lower().startswith("on") and value:
                self.snippets.append(value)

    def handle_starttag(self, tag, attrs):
        self._record_handlers(attrs)
        if tag != "script":
            return
        attrs = dict(attrs)
        script_type = str(attrs.get("type") or "").lower()
        if script_type in JS_TYPES:
            self._script_parts = []

    def handle_startendtag(self, tag, attrs):
        self._record_handlers(attrs)

    def handle_data(self, data):
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script_parts is not None:
            self.snippets.append("".join(self._script_parts))
            self._script_parts = None


def write_template(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def check_js_syntax(node, code, tmp_path, index):
    path = tmp_path / f"snippet-{index}.js"
    path.write_text(code, encoding="utf-8")
    proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_render_templates_for_codeql_masks_template_syntax(tmp_path, run_quality_script):
    source = tmp_path / "cps/themes/alpha/templates/page.html"
    text = (
        "<button onclick=\"sendData('{{ url_for('shelf.order_shelf', shelf_id=shelf.id) }}')\">\n"
        "{% if flag %}\n"
        "var value = {{ value|tojson }};\n"
        "{# note #}\n"
        'var label = "<%= label %>";\n'
        "<% if (ready) { %>\n"
        "</button>\n"
        "{% endif %}\n"
    )
    write_template(source, text)
    multiline = tmp_path / "cps/themes/alpha/templates/multiline.html"
    multiline_text = "var x = {{\n  value|tojson\n}};\n"
    write_template(multiline, multiline_text)
    ignored = tmp_path / "cps/themes/alpha/templates/notes.txt"
    write_template(ignored, "{{ keep }}")

    proc = run_quality_script("render_templates_for_codeql.py", tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "Rendered 2 Jinja template(s)" in proc.stdout
    rendered = tmp_path / "codeql-rendered/templates/alpha/templates/page.html"
    output = rendered.read_text(encoding="utf-8")
    assert "sendData('0')" in output
    assert "var value = 0;" in output
    assert 'var label = "0";' in output
    for token in ("{{", "{%", "{#", "<%"):
        assert token not in output
    assert output.count("\n") == text.count("\n")
    assert source.read_text(encoding="utf-8") == text

    multiline_output = (
        tmp_path / "codeql-rendered/templates/alpha/templates/multiline.html"
    ).read_text(encoding="utf-8")
    assert "{{" not in multiline_output
    assert multiline_output.count("\n") == multiline_text.count("\n")
    assert not (tmp_path / "codeql-rendered/templates/alpha/templates/notes.txt").exists()


def test_render_templates_for_codeql_uses_jinja_lexer_for_delimiters_and_raw(
    tmp_path, run_quality_script
):
    source = tmp_path / "cps/themes/alpha/templates/edges.html"
    text = (
        "<script>\n"
        "var a = {{ '}}' }};\n"
        "var b = 1; {# comment\ncontinued #}\n"
        "{% raw %}\n<%= literal %>\n{% endraw %}\n"
        "{% if '%;' == flag %}\nvar c = 2;\n{% endif %}\n"
        "</script>\n"
    )
    write_template(source, text)

    proc = run_quality_script("render_templates_for_codeql.py", tmp_path)

    assert proc.returncode == 0, proc.stderr
    output = (
        tmp_path / "codeql-rendered/templates/alpha/templates/edges.html"
    ).read_text(encoding="utf-8")
    assert output.count("\n") == text.count("\n")
    assert "var a = 0;" in output
    assert "var c = 2;" in output
    for token in ("{{", "{%", "{#", "<%"):
        assert token not in output


def test_render_templates_for_codeql_preserves_lines_with_whitespace_control(
    tmp_path, run_quality_script
):
    source = tmp_path / "cps/themes/alpha/templates/trim.html"
    text = (
        "<script>\n"
        "var a = 1;\n"
        "{%- if flag -%}\n"
        "var b = 2;\n"
        "{%- endif %}\n"
        "</script>\n"
    )
    write_template(source, text)

    proc = run_quality_script("render_templates_for_codeql.py", tmp_path)

    assert proc.returncode == 0, proc.stderr
    output = (
        tmp_path / "codeql-rendered/templates/alpha/templates/trim.html"
    ).read_text(encoding="utf-8")
    assert output.count("\n") == text.count("\n")
    assert "var a = 1;" in output
    assert "var b = 2;" in output
    for token in ("{{", "{%", "{#", "<%"):
        assert token not in output


def test_render_templates_for_codeql_rejects_malformed_template(
    tmp_path, run_quality_script
):
    write_template(
        tmp_path / "cps/themes/alpha/templates/broken.html",
        "{% if flag %}\nvar value = 1;\n",
    )

    proc = run_quality_script("render_templates_for_codeql.py", tmp_path)

    assert proc.returncode != 0
    assert "Unexpected end of template" in proc.stderr


def test_render_templates_for_codeql_clears_stale_output_and_accepts_empty_tree(
    tmp_path, run_quality_script
):
    stale = tmp_path / "codeql-rendered/templates/old.html"
    write_template(stale, "stale")

    proc = run_quality_script("render_templates_for_codeql.py", tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "Rendered 0 Jinja template(s)" in proc.stdout
    assert not stale.exists()


def test_rendered_template_javascript_is_parseable(tmp_path, run_quality_script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    source = tmp_path / "cps/themes/alpha/templates/widget.html"
    write_template(
        source,
        '<button onclick="sendData(\'{{ url_for(\'shelf.order_shelf\', shelf_id=shelf.id) }}\')">Save</button>\n'
        "<script>\n"
        "  var loading = {{_('Loading...')|safe|tojson}};\n"
        "</script>\n"
        "<script src=\"viewer.js\" type=\"module\">\n"
        "  var external = 0;\n"
        "</script>\n"
        "<script type=\"text/template\">\n<%= html %>\n</script>\n",
    )

    proc = run_quality_script("render_templates_for_codeql.py", tmp_path)
    assert proc.returncode == 0, proc.stderr
    output = (
        tmp_path / "codeql-rendered/templates/alpha/templates/widget.html"
    ).read_text(encoding="utf-8")

    parser = ScriptSnippetParser()
    parser.feed(output)
    parser.close()
    snippets = parser.snippets

    assert len(snippets) == 3
    assert any("var external" in snippet for snippet in snippets)
    for index, code in enumerate(snippets):
        check_js_syntax(node, code, tmp_path, index)


def test_real_rendered_templates_have_parseable_javascript(tmp_path, run_quality_script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    themes = REPO_ROOT / "cps" / "themes"
    if not themes.exists():
        pytest.skip("no theme templates in this checkout")

    shutil.copytree(themes, tmp_path / "cps" / "themes")
    proc = run_quality_script("render_templates_for_codeql.py", tmp_path)
    assert proc.returncode == 0, proc.stderr

    parser = ScriptSnippetParser()
    for path in sorted((tmp_path / "codeql-rendered").rglob("*.html")):
        parser.feed(path.read_text(encoding="utf-8"))
    parser.close()

    assert parser.snippets
    for index, code in enumerate(parser.snippets):
        check_js_syntax(node, code, tmp_path, index)
