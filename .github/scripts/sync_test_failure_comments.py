#!/usr/bin/env python3
"""Create, update, minimize, and reopen grouped test-failure comments."""
import json
import os
import re

import requests


MARKER_RE = re.compile(r"<!-- test-failure:[0-9a-f]{12} -->")
STATUS_LINE = "Resolved: this failure is no longer present in the latest run."
DETAILS_MARKER = "<details><summary>Last reported failures</summary>"
MINIMIZE_MUTATION = """
mutation($id: ID!) {
  minimizeComment(input: {subjectId: $id, classifier: RESOLVED}) {
    minimizedComment {
      ... on IssueComment { id isMinimized minimizedReason }
    }
  }
}
"""
UNMINIMIZE_MUTATION = """
mutation($id: ID!) {
  unminimizeComment(input: {subjectId: $id}) {
    unminimizedComment {
      ... on IssueComment { id isMinimized minimizedReason }
    }
  }
}
"""
MINIMIZED_STATES_QUERY = """
query($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on IssueComment { id isMinimized minimizedReason }
  }
}
"""


class GitHubClient:
    def __init__(self, token, api_url, owner, repo):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.owner = owner
        self.repo = repo

    def _headers(self):
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method, path, body=None):
        url = f"{self.api_url}/repos/{self.owner}/{self.repo}{path}"
        response = requests.request(
            method, url, headers=self._headers(), json=body, timeout=30
        )
        if not response.ok:
            raise RuntimeError(
                f"GitHub {method} {path} failed: {response.status_code} {response.text}"
            )
        return response.json() if response.content else None

    @property
    def graphql_url(self):
        if self.api_url.endswith("/api/v3"):
            return f"{self.api_url[:-3]}/graphql"
        return f"{self.api_url}/graphql"

    def graphql(self, query, variables=None):
        body = {"query": query}
        if variables:
            body["variables"] = variables
        response = requests.post(
            self.graphql_url, headers=self._headers(), json=body, timeout=30
        )
        if not response.ok:
            raise RuntimeError(
                f"GitHub GraphQL failed: {response.status_code} {response.text}"
            )
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"GitHub GraphQL failed: {payload['errors']}")
        return payload.get("data") or {}

    def minimized_states(self, node_ids):
        if not node_ids:
            return {}
        data = self.graphql(MINIMIZED_STATES_QUERY, {"ids": list(node_ids)})
        states = {}
        for node in data.get("nodes") or []:
            if not node or not node.get("id"):
                continue
            states[node["id"]] = bool(node.get("isMinimized"))
        return states

    def minimize_comment(self, node_id):
        return self.graphql(MINIMIZE_MUTATION, {"id": node_id})

    def unminimize_comment(self, node_id):
        return self.graphql(UNMINIMIZE_MUTATION, {"id": node_id})

    def list_comments(self, pr_number):
        comments = []
        page = 1
        while True:
            batch = self._request(
                "GET", f"/issues/{pr_number}/comments?per_page=100&page={page}"
            )
            if not batch:
                break
            comments.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return comments

    def create_comment(self, pr_number, body):
        return self._request("POST", f"/issues/{pr_number}/comments", {"body": body})

    def update_comment(self, comment_id, body):
        return self._request("PATCH", f"/issues/comments/{comment_id}", {"body": body})

    def delete_comment(self, comment_id):
        self._request("DELETE", f"/issues/comments/{comment_id}")


def marker_of(body):
    match = MARKER_RE.search(body or "")
    return match.group(0) if match else None


def suite_name_from_body(body):
    for line in (body or "").splitlines():
        for prefix in (
            "## ❌ Test failures:",
            "## ✅ Test failures resolved:",
            "## ❌ Test failure:",
            "## ✅ Resolved test failure:",
        ):
            if line.startswith(prefix):
                name = line[len(prefix):].strip()
                return re.sub(r"\s*\(\d+\)$", "", name)
    return ""


def resolved_body(body, name=None):
    lines = (body or "").splitlines()
    marker = marker_of(body) or ""
    suite_name = name or suite_name_from_body(body) or "Tests"
    details = []
    for line in lines:
        if line == marker or line.startswith("## ") or line == STATUS_LINE:
            continue
        if line.startswith("<details><summary>"):
            continue
        if line.strip() == "</details>":
            continue
        details.append(line)
    while details and not details[0].strip():
        details.pop(0)
    while details and not details[-1].strip():
        details.pop()

    result = [
        marker,
        f"## ✅ Test failures resolved: {suite_name}",
        "",
        STATUS_LINE,
    ]
    if details:
        result += ["", DETAILS_MARKER, ""] + details + ["", "</details>"]
    return "\n".join(result).rstrip()


def payload_index(payload):
    suites = {}
    legacy_to_primary = {}
    name_to_markers = {}
    for suite in payload.get("suites", []) or []:
        if not isinstance(suite, dict):
            continue
        marker = suite.get("marker")
        if not marker:
            continue
        suites[marker] = suite
        name = suite.get("name") or ""
        if name:
            name_to_markers.setdefault(name, set()).add(marker)
        for legacy in suite.get("legacy_markers", []) or []:
            legacy_to_primary[legacy] = marker

    for comment in payload.get("comments", []) or []:
        if not isinstance(comment, dict):
            continue
        marker = comment.get("marker")
        if not marker or marker in suites:
            continue
        name = suite_name_from_body(comment.get("body", ""))
        suites[marker] = {
            "marker": marker,
            "name": name,
            "failure_count": 1,
        }
        if name:
            name_to_markers.setdefault(name, set()).add(marker)

    return suites, legacy_to_primary, name_to_markers


def primary_for(marker, suites, legacy_to_primary):
    if marker in suites:
        return marker
    return legacy_to_primary.get(marker)


def sync(payload, client, pr_number, resolve_missing=True):
    suites, legacy_to_primary, name_to_markers = payload_index(payload)
    desired = {}
    for comment in payload.get("comments", []) or []:
        if not isinstance(comment, dict) or not comment.get("marker"):
            continue
        desired[comment["marker"]] = comment.get("body", "").strip()

    passed = {
        marker
        for marker, suite in suites.items()
        if suite.get("failure_count") == 0
    }

    existing = list(client.list_comments(pr_number) or [])
    existing.sort(
        key=lambda comment: 0 if marker_of(comment.get("body")) in suites else 1
    )
    node_ids = [
        comment.get("node_id") for comment in existing if comment.get("node_id")
    ]
    states = {}
    if hasattr(client, "minimized_states"):
        states = client.minimized_states(node_ids) or {}

    actions = []
    claimed = set()
    for comment in existing:
        body = comment.get("body") or ""
        marker = marker_of(body)
        if not marker:
            continue
        primary = primary_for(marker, suites, legacy_to_primary)
        if primary is not None:
            if primary in claimed:
                client.delete_comment(comment["id"])
                actions.append(("deleted-duplicate", marker))
                continue
            claimed.add(primary)

        node_id = comment.get("node_id")
        minimized = states.get(node_id) if node_id else None

        if primary is not None and primary in desired:
            target = desired[primary]
            if body.strip() != target:
                client.update_comment(comment["id"], target)
                actions.append(("updated", primary))
            if minimized is True:
                client.unminimize_comment(node_id)
                actions.append(("unminimized", primary))
            continue

        suite_name = suite_name_from_body(body)
        if primary is None:
            # A comment for a different failure signature of a suite we still
            # report on is stale even when this run failed elsewhere.
            should_resolve = resolve_missing or suite_name in name_to_markers
            action_key = marker
            resolved_name = suite_name or None
        else:
            suite = suites.get(primary) or {}
            should_resolve = resolve_missing or primary in passed
            action_key = primary
            resolved_name = suite.get("name")
        if not should_resolve:
            continue
        target = resolved_body(body, resolved_name)
        if body.strip() != target.strip():
            client.update_comment(comment["id"], target)
            actions.append(("resolved", action_key))
        if minimized is False:
            client.minimize_comment(node_id)
            actions.append(("minimized", action_key))

    for marker, body in desired.items():
        if marker in claimed:
            continue
        client.create_comment(pr_number, body)
        actions.append(("created", marker))
    return actions


def main():
    payload_path = os.environ.get("TEST_FAILURE_COMMENTS_JSON", "test-failure-comments.json").strip() or "test-failure-comments.json"
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    owner, _, repo = repository.partition("/")
    pr_number = os.environ.get("PR_NUMBER", "").strip()

    if not owner or not repo or not pr_number:
        raise SystemExit("GITHUB_REPOSITORY and PR_NUMBER are required.")
    if not os.environ.get("GITHUB_TOKEN", "").strip():
        raise SystemExit("GITHUB_TOKEN is required.")

    with open(payload_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    client = GitHubClient(
        os.environ["GITHUB_TOKEN"],
        os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        owner,
        repo,
    )
    resolve_missing = os.environ.get("RESOLVE_MISSING", "true").strip().lower() in (
        "1", "true", "yes",
    )
    actions = sync(payload, client, int(pr_number), resolve_missing=resolve_missing)
    print(json.dumps(actions))


if __name__ == "__main__":
    main()
