import json


def load_module(load_quality_script, tmp_path, name):
    return load_quality_script(name, tmp_path)


def test_builds_one_stable_comment_per_failure(load_quality_script, tmp_path):
    script = load_module(load_quality_script, tmp_path, "test_failure_comments.py")
    results = {
        "run_url": "https://example.test/run/1",
        "suites": [
            {
                "key": "workflow_script_tests",
                "name": "Workflow script tests",
                "summary": "1 failed, 50 passed in 2.10s",
                "failures": [
                    {
                        "kind": "FAILED",
                        "nodeid": "tests/workflow_scripts/test_x.py::test_a",
                        "message": "AssertionError: bad",
                    }
                ],
            },
            {
                "key": "unit_tests",
                "name": "Unit (in-process)",
                "summary": "12 passed in 1.50s",
                "failures": [],
            },
        ],
    }

    payload = script["build_payload"](results)

    assert len(payload["comments"]) == 1
    comment = payload["comments"][0]
    assert comment["marker"].startswith("<!-- test-failure:")
    assert "## ❌ Test failure: Workflow script tests" in comment["body"]
    assert "`tests/workflow_scripts/test_x.py::test_a` failed." in comment["body"]
    assert "- **Message:** AssertionError: bad" in comment["body"]
    assert "[Full workflow run](https://example.test/run/1)" in comment["body"]


def test_sync_creates_updates_and_resolves_comments(load_quality_script, tmp_path):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker_a = "<!-- test-failure:aaaaaaaaaaaa -->"
    marker_b = "<!-- test-failure:bbbbbbbbbbbb -->"

    class FakeClient:
        def __init__(self):
            self.comments = [
                {"id": 1, "body": f"{marker_a}\n## ❌ Test failure: Old"},
                {"id": 2, "body": f"{marker_b}\n## ❌ Test failure: Fixed"},
            ]
            self.created = []
            self.updated = []

        def list_comments(self, pr_number):
            return self.comments

        def create_comment(self, pr_number, body):
            self.created.append(body)

        def update_comment(self, comment_id, body):
            self.updated.append((comment_id, body))

    client = FakeClient()
    payload = {
        "comments": [
            {"marker": marker_a, "body": f"{marker_a}\n## ❌ Test failure: New\n\n`test_a` failed."},
            {"marker": "<!-- test-failure:cccccccccccc -->", "body": "new failure"},
        ]
    }

    actions = script["sync"](payload, client, 5)

    assert ("updated", marker_a) in actions
    assert ("resolved", marker_b) in actions
    assert ("created", "<!-- test-failure:cccccccccccc -->") in actions
    assert client.created == ["new failure"]
    assert "## ✅ Resolved test failure:" in dict(client.updated)[2]
    assert "Resolved in the latest passing run." in dict(client.updated)[2]


def test_writes_payload_from_artifact(tmp_path, run_quality_script, write_json):
    results = tmp_path / "test-results.json"
    write_json(
        results,
        {
            "run_url": "https://example.test/run/2",
            "suites": [
                {
                    "key": "workflow_script_tests",
                    "name": "Workflow script tests",
                    "summary": "1 failed in 0.1s",
                    "failures": [{"kind": "FAILED", "nodeid": "tests/x.py::test_y", "message": "boom"}],
                }
            ],
        },
    )

    proc = run_quality_script(
        "test_failure_comments.py",
        tmp_path,
        env={"TEST_RESULTS_JSON": str(results)},
    )

    assert proc.returncode == 0
    payload = json.loads((tmp_path / "test-failure-comments.json").read_text(encoding="utf-8"))
    assert len(payload["comments"]) == 1
