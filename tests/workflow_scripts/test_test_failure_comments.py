import json
import re

import pytest


def load_module(load_quality_script, tmp_path, name):
    return load_quality_script(name, tmp_path)


class FakeResponse:
    def __init__(self, ok=True, status_code=200, text="", payload=None, content=b"{}"):
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, comments=None, states=None):
        self.comments = list(comments or [])
        self.states = dict(states or {})
        self.created = []
        self.updated = []
        self.deleted = []
        self.minimized = []
        self.unminimized = []

    def list_comments(self, pr_number):
        return self.comments

    def minimized_states(self, node_ids):
        return {node_id: self.states.get(node_id, False) for node_id in node_ids}

    def create_comment(self, pr_number, body):
        self.created.append(body)

    def update_comment(self, comment_id, body):
        self.updated.append((comment_id, body))

    def delete_comment(self, comment_id):
        self.deleted.append(comment_id)

    def minimize_comment(self, node_id):
        self.minimized.append(node_id)
        self.states[node_id] = True

    def unminimize_comment(self, node_id):
        self.unminimized.append(node_id)
        self.states[node_id] = False


def suite(marker, name, failure_count, legacy_markers=None):
    return {
        "key": marker,
        "name": name,
        "marker": marker,
        "legacy_markers": legacy_markers or [],
        "failure_count": failure_count,
    }


def comment_body(marker, name, bullets, minimized=False):
    heading = (
        f"## ✅ Test failures resolved: {name}"
        if minimized
        else f"## ❌ Test failures: {name} ({len(bullets)})"
    )
    lines = [marker, heading, ""] + [f"- `{bullet}`" for bullet in bullets]
    if minimized:
        lines += ["", "Resolved in the latest passing run."]
    return "\n".join(lines).strip()


def test_builds_one_grouped_comment_per_suite(load_quality_script, tmp_path):
    script = load_module(load_quality_script, tmp_path, "test_failure_comments.py")
    results = {
        "run_url": "https://example.test/run/1",
        "suites": [
            {
                "key": "workflow_script_tests",
                "name": "Workflow script tests",
                "summary": "2 failed, 50 passed in 2.10s",
                "failures": [
                    {
                        "kind": "FAILED",
                        "nodeid": "tests/workflow_scripts/test_x.py::test_a",
                        "message": "AssertionError: bad",
                    },
                    {
                        "kind": "ERROR",
                        "nodeid": "tests/workflow_scripts/test_x.py::test_b",
                        "message": "setup failed",
                    },
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
    assert comment["marker"] == script["marker_for"]("workflow_script_tests")
    assert "## ❌ Test failures: Workflow script tests (2)" in comment["body"]
    assert "`tests/workflow_scripts/test_x.py::test_a` — `FAILED` — AssertionError: bad" in comment["body"]
    assert "`tests/workflow_scripts/test_x.py::test_b` — `ERROR` — setup failed" in comment["body"]
    assert "**Full analysis:** [Workflow run](https://example.test/run/1)" in comment["body"]

    assert [suite["key"] for suite in payload["suites"]] == [
        "workflow_script_tests",
        "unit_tests",
    ]
    failing_suite = payload["suites"][0]
    assert failing_suite["marker"] == comment["marker"]
    assert len(failing_suite["legacy_markers"]) == 2
    assert failing_suite["failure_count"] == 2
    passing_suite = payload["suites"][1]
    assert passing_suite["failure_count"] == 0
    assert passing_suite["legacy_markers"] == []


def test_build_payload_skips_invalid_entries_and_empty_run_url(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "test_failure_comments.py")

    payload = script["build_payload"](
        {
            "suites": [
                "not-a-suite",
                {
                    "key": "unit_tests",
                    "name": "Unit (in-process)",
                    "failures": [
                        {"nodeid": "tests/x.py::test_a"},
                        None,
                        {"message": "missing nodeid"},
                    ],
                },
            ]
        }
    )

    assert len(payload["suites"]) == 1
    assert payload["suites"][0]["failure_count"] == 1
    assert len(payload["comments"]) == 1
    assert "**Full analysis:**" not in payload["comments"][0]["body"]


def test_group_marker_is_stable_and_distinct_from_legacy_markers(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "test_failure_comments.py")

    grouped = script["marker_for"]("workflow_script_tests")
    legacy = script["legacy_marker_for"]("workflow_script_tests", "tests/x.py::test_a")

    assert grouped == script["marker_for"]("workflow_script_tests")
    assert re.fullmatch(r"<!-- test-failure:[0-9a-f]{12} -->", grouped)
    assert legacy != grouped
    assert script["marker_for"]("unit_tests") != grouped


def test_resolved_body_keeps_failure_details_collapsed_and_is_idempotent(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker = "<!-- test-failure:aaaaaaaaaaaa -->"
    body = (
        f"{marker}\n"
        "## ❌ Test failures: Workflow script tests (2)\n\n"
        "- `tests/x.py::test_a` — `FAILED` — boom\n"
        "- `tests/x.py::test_b` — `ERROR` — timeout\n\n"
        "**Full analysis:** [Workflow run](https://example.test/run/1)\n"
    )

    resolved = script["resolved_body"](body)

    assert resolved.startswith(
        f"{marker}\n## ✅ Test failures resolved: Workflow script tests\n\n"
        "Resolved in the latest passing run.\n\n"
        "<details><summary>Last reported failures</summary>\n"
    )
    assert "`tests/x.py::test_a` — `FAILED` — boom" in resolved
    assert "[Workflow run](https://example.test/run/1)" in resolved
    assert resolved.rstrip().endswith("</details>")
    assert script["resolved_body"](resolved) == resolved


def test_sync_updates_active_comment_and_minimizes_resolved_comment(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker_a = "<!-- test-failure:aaaaaaaaaaaa -->"
    marker_b = "<!-- test-failure:bbbbbbbbbbbb -->"
    client = FakeClient(
        comments=[
            {
                "id": 1,
                "node_id": "n1",
                "body": comment_body(marker_a, "Workflow script tests", ["old_a"]),
            },
            {
                "id": 2,
                "node_id": "n2",
                "body": comment_body(marker_b, "Unit (in-process)", ["old_b"]),
            },
        ]
    )
    active_body = comment_body(marker_a, "Workflow script tests", ["new_a"])
    payload = {
        "suites": [
            suite(marker_a, "Workflow script tests", 1),
            suite(marker_b, "Unit (in-process)", 0),
        ],
        "comments": [{"marker": marker_a, "body": active_body}],
    }

    actions = script["sync"](payload, client, 5, resolve_missing=False)

    assert ("updated", marker_a) in actions
    assert ("resolved", marker_b) in actions
    assert ("minimized", marker_b) in actions
    assert dict(client.updated)[1] == active_body
    resolved = dict(client.updated)[2]
    assert "## ✅ Test failures resolved: Unit (in-process)" in resolved
    assert resolved.index("Resolved in the latest passing run.") < resolved.index("<details>")
    assert client.minimized == ["n2"]
    assert client.unminimized == []
    assert client.created == []


def test_sync_reopens_returning_failure(load_quality_script, tmp_path):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker = "<!-- test-failure:aaaaaaaaaaaa -->"
    client = FakeClient(
        comments=[
            {
                "id": 1,
                "node_id": "n1",
                "body": comment_body(marker, "Workflow script tests", ["old"], minimized=True),
            }
        ],
        states={"n1": True},
    )
    active_body = comment_body(marker, "Workflow script tests", ["new"])
    payload = {
        "suites": [suite(marker, "Workflow script tests", 1)],
        "comments": [{"marker": marker, "body": active_body}],
    }

    actions = script["sync"](payload, client, 5, resolve_missing=False)

    assert ("updated", marker) in actions
    assert ("unminimized", marker) in actions
    assert dict(client.updated)[1] == active_body
    assert client.unminimized == ["n1"]
    assert client.minimized == []


def test_sync_resolves_passing_suite_in_partially_failing_run(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker_a = "<!-- test-failure:aaaaaaaaaaaa -->"
    marker_b = "<!-- test-failure:bbbbbbbbbbbb -->"
    client = FakeClient(
        comments=[
            {
                "id": 1,
                "node_id": "n1",
                "body": comment_body(marker_a, "Workflow script tests", ["old_a"]),
            },
            {
                "id": 2,
                "node_id": "n2",
                "body": comment_body(marker_b, "Unit (in-process)", ["old_b"]),
            },
        ]
    )
    active_body = comment_body(marker_b, "Unit (in-process)", ["new_b"])
    payload = {
        "suites": [
            suite(marker_a, "Workflow script tests", 0),
            suite(marker_b, "Unit (in-process)", 1),
        ],
        "comments": [{"marker": marker_b, "body": active_body}],
    }

    actions = script["sync"](payload, client, 5, resolve_missing=False)

    assert ("resolved", marker_a) in actions
    assert ("minimized", marker_a) in actions
    assert ("updated", marker_b) in actions
    assert client.minimized == ["n1"]
    assert [comment_id for comment_id, _ in client.updated] == [1, 2]


def test_sync_deletes_duplicate_legacy_comments_for_same_suite(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    legacy_a = "<!-- test-failure:aaaaaaaaaaaa -->"
    legacy_b = "<!-- test-failure:bbbbbbbbbbbb -->"
    marker = "<!-- test-failure:cccccccccccc -->"
    client = FakeClient(
        comments=[
            {
                "id": 1,
                "node_id": "n1",
                "body": comment_body(legacy_a, "Workflow script tests", ["old_a"]),
            },
            {
                "id": 2,
                "node_id": "n2",
                "body": comment_body(legacy_b, "Workflow script tests", ["old_b"]),
            },
        ]
    )
    payload = {
        "suites": [
            suite(marker, "Workflow script tests", 0, [legacy_a, legacy_b]),
        ],
        "comments": [],
    }

    actions = script["sync"](payload, client, 5, resolve_missing=False)

    assert sum(1 for action in actions if action[0] == "resolved") == 1
    assert ("deleted-duplicate", legacy_b) in actions
    assert client.deleted == [2]
    assert client.minimized == ["n1"]


def test_sync_creates_missing_active_comment(load_quality_script, tmp_path):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker = "<!-- test-failure:aaaaaaaaaaaa -->"
    client = FakeClient()
    active_body = comment_body(marker, "Workflow script tests", ["new"])
    payload = {
        "suites": [suite(marker, "Workflow script tests", 1)],
        "comments": [{"marker": marker, "body": active_body}],
    }

    actions = script["sync"](payload, client, 5)

    assert actions == [("created", marker)]
    assert client.created == [active_body]


def test_graphql_helpers_query_and_mutate_minimized_state(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    client = script["GitHubClient"]("token", "https://api.github.com", "owner", "repo")
    assert client.graphql_url == "https://api.github.com/graphql"
    assert script["GitHubClient"](
        "token", "https://ghe.test/api/v3", "owner", "repo"
    ).graphql_url == "https://ghe.test/api/graphql"

    calls = []

    def graphql(query, variables=None):
        calls.append((query, variables))
        return {
            "nodes": [{"id": "n1", "isMinimized": True}],
            "minimizeComment": {"minimizedComment": {"id": "n1"}},
            "unminimizeComment": {"unminimizedComment": {"id": "n1"}},
        }

    client.graphql = graphql

    assert client.minimized_states(["n1"]) == {"n1": True}
    client.minimize_comment("n1")
    client.unminimize_comment("n1")

    assert all(variables == {"ids": ["n1"]} or variables == {"id": "n1"} for _, variables in calls)
    assert "RESOLVED" in calls[1][0]
    assert "minimizedComment" in calls[1][0]
    assert "unminimizeComment" in calls[2][0]
    assert "unminimizedComment" in calls[2][0]


def test_github_client_wrappers_call_rest(load_quality_script, tmp_path, monkeypatch):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    client = script["GitHubClient"]("token", "https://api.github.com", "owner", "repo")
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append((method, url, json))
        return FakeResponse(
            payload=None if method == "DELETE" else {"ok": True},
            content=b"" if method == "DELETE" else b"{}",
        )

    monkeypatch.setattr(script["requests"], "request", fake_request)

    assert client.list_comments(5) == {"ok": True}
    assert client.create_comment(5, "body") == {"ok": True}
    assert client.update_comment(7, "body") == {"ok": True}
    assert client.delete_comment(7) is None
    assert calls == [
        ("GET", "https://api.github.com/repos/owner/repo/issues/5/comments?per_page=100", None),
        ("POST", "https://api.github.com/repos/owner/repo/issues/5/comments", {"body": "body"}),
        ("PATCH", "https://api.github.com/repos/owner/repo/issues/comments/7", {"body": "body"}),
        ("DELETE", "https://api.github.com/repos/owner/repo/issues/comments/7", None),
    ]


def test_graphql_variables_empty_state_and_http_errors(
    load_quality_script, tmp_path, monkeypatch
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    client = script["GitHubClient"]("token", "https://api.github.com", "owner", "repo")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return FakeResponse(
            payload={"data": {"nodes": [None, {"isMinimized": False}]}}
        )

    monkeypatch.setattr(script["requests"], "post", fake_post)

    assert client.minimized_states([]) == {}
    assert calls == []
    assert client.minimized_states(["n1"]) == {}
    assert calls[-1]["variables"] == {"ids": ["n1"]}
    assert client.graphql("query", {"x": 1}) == {"nodes": [None, {"isMinimized": False}]}
    assert calls[-1]["variables"] == {"x": 1}

    def fake_failed_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(ok=False, status_code=403, text="forbidden")

    monkeypatch.setattr(script["requests"], "post", fake_failed_post)
    with pytest.raises(RuntimeError, match="403 forbidden"):
        client.graphql("query")


def test_helpers_handle_missing_metadata(load_quality_script, tmp_path):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")

    assert script["suite_name_from_body"]("no heading") == ""
    assert script["resolved_body"]("<!-- test-failure:aaaaaaaaaaaa -->") == (
        "<!-- test-failure:aaaaaaaaaaaa -->\n"
        "## ✅ Test failures resolved: Tests\n\n"
        "Resolved in the latest passing run."
    )

    suites, legacy, names = script["payload_index"](
        {
            "suites": [
                "not-a-suite",
                {"name": "Missing marker"},
                {"marker": "<!-- test-failure:bbbbbbbbbbbb -->"},
            ],
            "comments": [
                "not-a-comment",
                {"body": "missing marker"},
                {
                    "marker": "<!-- test-failure:cccccccccccc -->",
                    "body": "## ❌ Test failures: Derived suite",
                },
                {"marker": "<!-- test-failure:eeeeeeeeeeee -->", "body": "body"},
            ],
        }
    )

    assert set(suites) == {
        "<!-- test-failure:bbbbbbbbbbbb -->",
        "<!-- test-failure:cccccccccccc -->",
        "<!-- test-failure:eeeeeeeeeeee -->",
    }
    assert suites["<!-- test-failure:cccccccccccc -->"]["name"] == "Derived suite"
    assert legacy == {}
    assert names == {}
    assert (
        script["primary_for"](
            "unknown",
            "## ❌ Test failures: Derived suite",
            {},
            {},
            {"Derived suite": "<!-- test-failure:dddddddddddd -->"},
        )
        == "<!-- test-failure:dddddddddddd -->"
    )


def test_sync_skips_invalid_entries_and_unchanged_bodies(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker = "<!-- test-failure:aaaaaaaaaaaa -->"
    body = comment_body(marker, "Workflow script tests", ["test_a"])
    client = FakeClient(
        comments=[
            {"id": 1, "body": "not a bot comment"},
            {"id": 2, "node_id": "n2", "body": body},
        ]
    )
    payload = {
        "suites": [suite(marker, "Workflow script tests", 1)],
        "comments": ["bad", {"body": "missing marker"}, {"marker": marker, "body": body}],
    }

    actions = script["sync"](payload, client, 5, resolve_missing=False)

    assert actions == []
    assert client.updated == []
    assert client.created == []


def test_sync_handles_clients_without_minimized_state_support(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker = "<!-- test-failure:aaaaaaaaaaaa -->"

    class BareClient:
        def __init__(self, comments):
            self.comments = comments
            self.updated = []
            self.minimized = []

        def list_comments(self, pr_number):
            return self.comments

        def update_comment(self, comment_id, body):
            self.updated.append((comment_id, body))

        def minimize_comment(self, node_id):
            self.minimized.append(node_id)

    client = BareClient(
        comments=[
            {
                "id": 1,
                "node_id": "n1",
                "body": comment_body(marker, "Workflow script tests", ["old"]),
            }
        ]
    )
    payload = {
        "suites": [suite(marker, "Workflow script tests", 0)],
        "comments": [],
    }

    actions = script["sync"](payload, client, 5, resolve_missing=False)

    assert ("resolved", marker) in actions
    assert client.minimized == []


def test_sync_leaves_unresolved_suite_when_run_still_fails(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker = "<!-- test-failure:aaaaaaaaaaaa -->"
    body = comment_body(marker, "Workflow script tests", ["old"])
    client = FakeClient(comments=[{"id": 1, "node_id": "n1", "body": body}])
    payload = {
        "suites": [suite(marker, "Workflow script tests", 1)],
        "comments": [],
    }

    actions = script["sync"](payload, client, 5, resolve_missing=False)

    assert actions == []
    assert client.updated == []


def test_sync_keeps_already_resolved_comment_body_when_minimizing(
    load_quality_script, tmp_path
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    marker = "<!-- test-failure:aaaaaaaaaaaa -->"
    body = script["resolved_body"](
        comment_body(marker, "Workflow script tests", ["old"]),
        "Workflow script tests",
    )
    client = FakeClient(comments=[{"id": 1, "node_id": "n1", "body": body}])
    payload = {
        "suites": [suite(marker, "Workflow script tests", 0)],
        "comments": [],
    }

    actions = script["sync"](payload, client, 5, resolve_missing=False)

    assert ("minimized", marker) in actions
    assert all(action[0] != "resolved" for action in actions)
    assert client.updated == []


def test_main_reads_environment_and_prints_actions(
    load_quality_script, tmp_path, monkeypatch, capsys, write_json
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    payload_path = write_json(tmp_path / "payload.json", {"suites": [], "comments": []})
    calls = {}

    class FakeClient:
        def __init__(self, token, api_url, owner, repo):
            calls["client"] = (token, api_url, owner, repo)

    def fake_sync(payload, client, pr_number, resolve_missing=True):
        calls["sync"] = (payload, pr_number, resolve_missing)
        return [("created", "<!-- test-failure:aaaaaaaaaaaa -->")]

    monkeypatch.setenv("TEST_FAILURE_COMMENTS_JSON", str(payload_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "7")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("RESOLVE_MISSING", "false")
    monkeypatch.setitem(script, "GitHubClient", FakeClient)
    monkeypatch.setitem(script, "sync", fake_sync)

    script["main"]()

    assert calls["client"] == ("token", "https://api.github.com", "owner", "repo")
    assert calls["sync"][1:] == (7, False)
    assert json.loads(capsys.readouterr().out) == [
        ["created", "<!-- test-failure:aaaaaaaaaaaa -->"]
    ]


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"GITHUB_REPOSITORY": "owner/repo"}, "PR_NUMBER are required"),
        (
            {"GITHUB_REPOSITORY": "owner/repo", "PR_NUMBER": "7"},
            "GITHUB_TOKEN is required",
        ),
    ],
)
def test_main_validates_required_environment(
    load_quality_script, tmp_path, monkeypatch, env, message
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    for key in ("GITHUB_REPOSITORY", "PR_NUMBER", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(SystemExit, match=message):
        script["main"]()


def test_rest_and_graphql_requests_use_requests_with_errors(
    load_quality_script, tmp_path, monkeypatch
):
    script = load_module(load_quality_script, tmp_path, "sync_test_failure_comments.py")
    client = script["GitHubClient"]("token", "https://api.github.com", "owner", "repo")
    calls = []

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls.append((method, url, json))
        return FakeResponse(payload={"id": 7})

    monkeypatch.setattr(script["requests"], "request", fake_request)
    assert client._request("POST", "/issues/5/comments", {"body": "x"}) == {"id": 7}
    assert calls == [
        (
            "POST",
            "https://api.github.com/repos/owner/repo/issues/5/comments",
            {"body": "x"},
        )
    ]

    def fake_failed_request(method, url, headers=None, json=None, timeout=None):
        return FakeResponse(ok=False, status_code=422, text="bad request")

    monkeypatch.setattr(script["requests"], "request", fake_failed_request)
    with pytest.raises(RuntimeError, match="422 bad request"):
        client._request("GET", "/issues/5/comments")

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(("POST", url, json))
        return FakeResponse(payload={"data": {"nodes": []}, "errors": None})

    monkeypatch.setattr(script["requests"], "post", fake_post)
    assert client.graphql("query { viewer { login } }") == {"nodes": []}
    assert calls[-1] == (
        "POST",
        "https://api.github.com/graphql",
        {"query": "query { viewer { login } }"},
    )

    def fake_graphql_error(url, headers=None, json=None, timeout=None):
        return FakeResponse(payload={"errors": [{"message": "nope"}]})

    monkeypatch.setattr(script["requests"], "post", fake_graphql_error)
    with pytest.raises(RuntimeError, match="nope"):
        client.graphql("query { viewer { login } }")


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
    assert "## ❌ Test failures: Workflow script tests (1)" in payload["comments"][0]["body"]
    assert len(payload["suites"]) == 1
    assert payload["suites"][0]["failure_count"] == 1
