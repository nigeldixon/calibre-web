import re
import shutil
import subprocess

import pytest


SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
TYPE_RE = re.compile(r"\btype\s*=\s*[\"']?([^\"' >]+)", re.I)
SRC_RE = re.compile(r"\bsrc\s*=", re.I)
HANDLER_RE = re.compile(r"\bon[a-z]+\s*=\s*([\"'])(.*?)\1", re.S | re.I)


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
        "<script type=\"text/template\">\n<%= html %>\n</script>\n",
    )

    proc = run_quality_script("render_templates_for_codeql.py", tmp_path)
    assert proc.returncode == 0, proc.stderr
    output = (
        tmp_path / "codeql-rendered/templates/alpha/templates/widget.html"
    ).read_text(encoding="utf-8")

    snippets = []
    for match in SCRIPT_RE.finditer(output):
        attrs = match.group(1)
        type_match = TYPE_RE.search(attrs)
        if type_match and type_match.group(1).lower() not in (
            "text/javascript",
            "application/javascript",
            "module",
        ):
            continue
        if SRC_RE.search(attrs):
            continue
        snippets.append(match.group(2))
    snippets.extend(match.group(2) for match in HANDLER_RE.finditer(output))

    assert snippets
    for index, code in enumerate(snippets):
        check_js_syntax(node, code, tmp_path, index)
