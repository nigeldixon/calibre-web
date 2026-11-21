#!/usr/bin/env python3
"""Create, update, and resolve per-test failure comments on a pull request."""
import json
import os
import re
import urllib.error
import urllib.request


MARKER_RE = re.compile(r"<!-- test-failure:[0-9a-f]{12} -->")


class GitHubClient:
    def __init__(self, token, api_url, owner, repo):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.owner = owner
        self.repo = repo

    def _request(self, method, path, body=None):
        url = f"{self.api_url}/repos/{self.owner}/{self.repo}{path}"
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub {method} {path} failed: {exc.code} {detail}") from exc

    def list_comments(self, pr_number):
        return self._request("GET", f"/issues/{pr_number}/comments?per_page=100")

    def create_comment(self, pr_number, body):
        return self._request("POST", f"/issues/{pr_number}/comments", {"body": body})

    def update_comment(self, comment_id, body):
        return self._request("PATCH", f"/issues/comments/{comment_id}", {"body": body})

    def delete_comment(self, comment_id):
        self._request("DELETE", f"/issues/comments/{comment_id}")


def marker_of(body):
    match = MARKER_RE.search(body or "")
    return match.group(0) if match else None


def resolved_body(body):
    lines = (body or "").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## ❌ Test failure:"):
            lines[index] = line.replace("## ❌ Test failure:", "## ✅ Resolved test failure:", 1)
            lines[index + 1:index + 1] = ["", "Resolved in the latest passing run."]
            detail_start = index + 3
        elif line.startswith("## ✅ Resolved test failure:"):
            status_line = "Resolved in the latest passing run."
            details_marker = "<details><summary>Original test failure</summary>"
            details_at = lines.index(details_marker) if details_marker in lines else -1
            status_at = lines.index(status_line) if status_line in lines else -1
            if details_at != -1 and status_at > details_at:
                lines.pop(status_at)
                lines[index + 1:index + 1] = ["", status_line]
            elif status_at == -1:
                lines[index + 1:index + 1] = ["", status_line]
            if details_at != -1:
                return "\n".join(lines).rstrip()
            detail_start = index + 1
            while detail_start < len(lines) and (
                not lines[detail_start].strip()
                or lines[detail_start].startswith("Resolved in the latest passing run.")
            ):
                detail_start += 1
        else:
            continue
        while detail_start < len(lines) and not lines[detail_start].strip():
            detail_start += 1
        if detail_start < len(lines):
            lines[detail_start:detail_start] = [
                "<details><summary>Original test failure</summary>",
                "",
            ]
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", "</details>"]
        return "\n".join(lines).rstrip()
    return "\n".join(lines).rstrip()


def sync(payload, client, pr_number, resolve_missing=True):
    desired = {}
    for comment in payload.get("comments", []) or []:
        marker = comment.get("marker")
        if marker:
            desired[marker] = comment.get("body", "").strip()

    actions = []
    existing = client.list_comments(pr_number) or []
    for comment in existing:
        body = comment.get("body") or ""
        marker = marker_of(body)
        if not marker:
            continue
        if marker in desired:
            target = desired.pop(marker)
            if body.strip() != target:
                client.update_comment(comment["id"], target)
                actions.append(("updated", marker))
            continue
        if not resolve_missing:
            continue
        target = resolved_body(body)
        if body.strip() != target.strip():
            client.update_comment(comment["id"], target)
            actions.append(("resolved", marker))

    for marker, body in desired.items():
        client.create_comment(pr_number, body)
        actions.append(("created", marker))
    return actions


if __name__ == "__main__":
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
