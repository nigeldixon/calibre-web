from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "quality_reports.yml"
CODEQL_WORKFLOW = ROOT / ".github" / "workflows" / "codeql.yml"


def step_block(text, name):
    marker = f"- name: {name}\n"
    start = text.index(marker)
    end = text.find("\n      - name:", start + len(marker))
    return text[start:end if end != -1 else len(text)]


def test_fork_publish_requires_successful_resolver():
    text = WORKFLOW.read_text(encoding="utf-8")

    publish = step_block(text, "Publish quality report to PR body")
    assert "if: always() && steps.pr.outcome == 'success' && steps.pr.outputs.number != ''" in publish

    sync = step_block(text, "Sync per-test failure comments")
    assert "always()" in sync


def test_codeql_publishes_failure_panel_after_generator_fails():
    text = CODEQL_WORKFLOW.read_text(encoding="utf-8")

    generator = step_block(text, "Generate CodeQL PR comment")
    assert "continue-on-error: true" in generator

    publish = step_block(text, "Update CodeQL report in PR body")
    assert (
        "if: always() && github.event_name == 'push' && steps.find_pr.outputs.number != ''"
        in publish
    )
    assert "steps.codeql_comment.outcome" not in publish
    assert "unavailablePanel" in publish
    assert "const reportFile = 'codeql-comment.md';" in publish
