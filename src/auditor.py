"""Run Semgrep, ask OpenAI for remediations, then hand results to the reporter."""

import json
import os
import subprocess

import requests

from reporter import build_summary_markdown, publish_pr_comment, write_step_summary


def run_secgenie_audit(api_key, github_token=None):
    """Scan the workspace, remediate the top findings, and publish a summary."""
    print("🔍 SecGenie running Semgrep SAST scan...")

    # Docker actions use WORKDIR /app (this image). GITHUB_WORKSPACE is the
    # checked-out consumer repo mounted by the runner; scan that when present.
    scan_cwd = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    # An explicit ruleset is required: with no --config Semgrep falls back to
    # "auto", which refuses to run when metrics are off and returns 0 results.
    semgrep_config = (
        os.environ.get("INPUT_SEMGREP-CONFIG")
        or os.environ.get("INPUT_SEMGREP_CONFIG")
        or "p/default"
    )
    # --json is parsed below; --metrics=off avoids sending telemetry from CI.
    scan_command = [
        "semgrep", "scan", "--config", semgrep_config, "--json", "--metrics=off",
    ]
    print(f"⚙️ Semgrep ruleset: {semgrep_config}")
    result = subprocess.run(scan_command, capture_output=True, text=True, cwd=scan_cwd)

    findings = []
    data = {}
    if result.stdout:
        try:
            data = json.loads(result.stdout)
            # Semgrep puts match objects under "results"; ignore timing metadata.
            findings = data.get("results", [])
        except json.JSONDecodeError:
            print("⚠️ SecGenie Warning: Could not parse Semgrep output.")

    # Semgrep reports config/parse problems in "errors" and on stderr, often
    # while still emitting valid JSON. Surface them so "0 findings" is never
    # silently the result of a broken scan.
    for error in data.get("errors", []):
        level = error.get("level", "error")
        print(f"⚠️ Semgrep {level}: {error.get('message') or error.get('type')}")
    # Exit 0 = clean scan, 1 = findings with --error; anything else is a failure.
    if result.returncode not in (0, 1):
        print(f"⚠️ SecGenie Warning: Semgrep exited with code {result.returncode}.")
        if result.stderr:
            print(result.stderr.strip()[-2000:])

    print(f"📌 SecGenie detected {len(findings)} security findings.")
    write_output("findings-count", len(findings))

    # Most critical first, so the three AI slots go to the worst issues rather
    # than whichever path sorts first alphabetically (Semgrep's order).
    findings.sort(key=_priority_key)

    # Several rules often flag the same line (e.g. two subprocess rules). Keep
    # only the highest-priority finding per location for the AI slots so all
    # three remediations are for different code.
    distinct = []
    seen_locations = set()
    for finding in findings:
        location = (finding.get("path"), finding.get("start", {}).get("line"))
        if location not in seen_locations:
            seen_locations.add(location)
            distinct.append(finding)

    reports = []
    # Cap at three findings so CI time and OpenAI token usage stay bounded.
    for finding in distinct[:3]:
        # Semgrep JSON: path, start.line, extra.lines (snippet), extra.message.
        file_path = finding.get("path")
        line = finding.get("start", {}).get("line")
        code_snippet = finding.get("extra", {}).get("lines", "")
        message = finding.get("extra", {}).get("message", "")
        severity = _severity_label(finding)

        print(f"\n🧞 SecGenie analyzing [{severity}] issue in {file_path}:{line}...")
        ai_fix = get_secgenie_remediation(api_key, message, code_snippet)

        print("\n--- 🧞 SecGenie Remediation Suggestion ---")
        print(ai_fix)
        print("-------------------------------------------\n")
        reports.append(
            {
                "path": file_path,
                "line": line,
                "message": message,
                "severity": severity,
                "remediation": ai_fix,
            }
        )

    # Same Markdown goes to the job summary (Checks) and the PR conversation.
    markdown = build_summary_markdown(len(findings), reports)
    write_step_summary(markdown)
    publish_pr_comment(markdown, github_token)



def get_secgenie_remediation(api_key, vulnerability_description, code_snippet):
    """Ask gpt-4o-mini for a short explanation and a fixed snippet.

    Returns model text on success, or an error string that can still be shown
    in logs and on the PR so the scan does not abort.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are SecGenie, an expert application security AI assistant. "
                    "Provide a concise explanation of the vulnerability and the exact "
                    "remediated code snippet."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Vulnerability: {vulnerability_description}\n"
                    f"Code Snippet:\n{code_snippet}"
                ),
            },
        ],
        # Low temperature keeps remediations more deterministic across CI runs.
        "temperature": 0.2,
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        # Surface the API body so CI logs show why a suggestion was not generated.
        return f"Error from SecGenie LLM API: {response.text}"
    except Exception as e:
        return f"Failed to contact AI service: {str(e)}"


def write_output(name, value):
    """Expose a step output via GITHUB_OUTPUT so workflows can assert on it."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    try:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    except OSError as exc:
        print(f"⚠️ SecGenie Warning: Could not write output {name}: {exc}")


# Semgrep rule severity. Registry rules mostly use ERROR/WARNING/INFO; newer
# rules may use CRITICAL/HIGH/MEDIUM/LOW. Lower rank = more critical.
_SEVERITY_RANK = {
    "CRITICAL": 0,
    "ERROR": 1,
    "HIGH": 1,
    "WARNING": 2,
    "MEDIUM": 2,
    "INFO": 3,
    "LOW": 3,
}
# Registry rule metadata (impact, likelihood, confidence) uses these levels.
_LEVEL_SCORE = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _severity_label(finding):
    """Return the rule severity in upper case, or UNKNOWN if missing."""
    return str(finding.get("extra", {}).get("severity") or "UNKNOWN").upper()


def _priority_key(finding):
    """Sort key: severity, then risk (impact x likelihood), then confidence.

    Path and line break ties so the order is stable across CI runs.
    """
    metadata = finding.get("extra", {}).get("metadata") or {}

    def level(name):
        return _LEVEL_SCORE.get(str(metadata.get(name, "")).upper(), 0)

    return (
        _SEVERITY_RANK.get(_severity_label(finding), len(_SEVERITY_RANK)),
        -(level("impact") * level("likelihood")),
        -level("confidence"),
        finding.get("path") or "",
        finding.get("start", {}).get("line") or 0,
    )
