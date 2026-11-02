"""Publish SecGenie results to the Actions job summary and the pull request."""

import json
import os

import requests

# Hidden HTML marker GitHub still stores in the comment body. Later runs search
# for this string so they update the existing SecGenie comment instead of
# posting a new one on every push.
COMMENT_MARKER = "<!-- secgenie-action-summary -->"


def build_summary_markdown(total_findings, reports):
    """Build Markdown used for both the job summary and the PR comment.

    `total_findings` is the full Semgrep count. `reports` is only the subset
    that received an AI remediation (currently capped in auditor.py).
    """
    shown = len(reports)
    # Marker must stay on the first line so comment matching stays reliable.
    lines = [
        COMMENT_MARKER,
        "## 🧞 SecGenie security summary",
        "",
        f"Detected **{total_findings}** Semgrep finding(s).",
    ]

    if total_findings == 0:
        lines.append("No remediations to report.")
        lines.append("")
        return "\n".join(lines)

    if shown < total_findings:
        lines.append(
            f"Showing AI remediations for the first **{shown}** "
            "(capped to limit CI time and API usage)."
        )
    else:
        lines.append(f"Showing AI remediations for **{shown}** finding(s).")

    lines.append("")

    for index, report in enumerate(reports, start=1):
        location = f"`{report['path']}`"
        if report.get("line"):
            location = f"{location}:{report['line']}"
        lines.extend(
            [
                f"### {index}. {location}",
                "",
                f"**Finding:** {report.get('message') or '(no message)'}",
                "",
                "**Remediation**",
                "",
                report.get("remediation") or "(no remediation generated)",
                "",
            ]
        )

    return "\n".join(lines)


def write_step_summary(markdown):
    """Append Markdown to GITHUB_STEP_SUMMARY (Checks tab / workflow run).

    GitHub Actions creates this file and injects its path. It is absent when
    the auditor is run locally, so skip rather than fail.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        print("ℹ️ GITHUB_STEP_SUMMARY is not set; skipping job summary.")
        return

    try:
        # Append so we do not wipe summaries written by earlier steps.
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown)
            if not markdown.endswith("\n"):
                handle.write("\n")
        print("📝 Wrote SecGenie summary to the GitHub Actions job summary.")
    except OSError as exc:
        print(f"⚠️ SecGenie Warning: Could not write job summary: {exc}")


def publish_pr_comment(markdown, github_token):
    """Create or update the SecGenie comment on the current pull request.

    Requires pull-requests: write and a token (usually github.token). Failures
    are logged and ignored so a comment problem does not fail the scan.
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        print("ℹ️ Not a pull_request event; skipping PR comment.")
        return

    if not github_token:
        print("⚠️ SecGenie Warning: Missing GitHub token; skipping PR comment.")
        return

    repository = os.environ.get("GITHUB_REPOSITORY")
    pr_number = _pull_request_number()
    if not repository or not pr_number:
        print("⚠️ SecGenie Warning: Could not determine repository or PR number.")
        return

    # GITHUB_API_URL supports GitHub Enterprise; default is github.com.
    api_root = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    # PR conversation comments use the issues comments API.
    comments_url = f"{api_root}/repos/{repository}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        existing_id = _existing_comment_id(comments_url, headers)
        if existing_id:
            response = requests.patch(
                f"{api_root}/repos/{repository}/issues/comments/{existing_id}",
                headers=headers,
                json={"body": markdown},
                timeout=30,
            )
        else:
            response = requests.post(
                comments_url,
                headers=headers,
                json={"body": markdown},
                timeout=30,
            )

        if response.status_code in (200, 201):
            action = "Updated" if existing_id else "Posted"
            print(f"💬 {action} SecGenie summary comment on PR #{pr_number}.")
        else:
            print(
                f"⚠️ SecGenie Warning: GitHub comment API returned "
                f"{response.status_code}: {response.text}"
            )
    except requests.RequestException as exc:
        print(f"⚠️ SecGenie Warning: Failed to publish PR comment: {exc}")


def _pull_request_number():
    """Read the PR number from the Actions event payload on disk."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None

    try:
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    pull_request = event.get("pull_request") or {}
    return pull_request.get("number") or event.get("number")


def _existing_comment_id(comments_url, headers):
    """Return the id of the prior SecGenie comment, if one exists."""
    response = requests.get(
        comments_url,
        headers=headers,
        params={"per_page": 100},
        timeout=30,
    )
    if response.status_code != 200:
        print(
            f"⚠️ SecGenie Warning: Could not list PR comments "
            f"({response.status_code}); will try to create a new one."
        )
        return None

    for comment in response.json():
        body = comment.get("body") or ""
        if COMMENT_MARKER in body:
            return comment.get("id")
    return None
