# SecGenie Action

GitHub Action that runs a Semgrep SAST scan on the repository checkout, then asks OpenAI (`gpt-4o-mini`) for a short explanation and a remediated snippet for the first few findings.

The action is Docker-based: GitHub builds the image from this repo’s `Dockerfile` and runs `src/entrypoint.py`.

## How it works

1. A workflow checks out the target repo and invokes this action with an OpenAI API key.
2. The container starts `src/entrypoint.py`, which reads the key from GitHub Actions inputs (`INPUT_OPENAI-API-KEY` / `INPUT_OPENAI_API_KEY`).
3. `src/auditor.py` runs `semgrep scan --config <semgrep-config> --json --metrics=off` against the working tree.
4. Up to **three** Semgrep results are sent to the OpenAI Chat Completions API.
5. Each suggestion is printed in the job log. The same summary is written to the workflow **job summary** (Checks tab). On `pull_request` events it is also posted as a PR comment, updating the previous SecGenie comment instead of creating a new one.
6. The action does not open PRs, apply patches, or fail the job based on finding count.

Semgrep is the only scanner in this repo. Dependency-specific audits (for example `pip-audit` or GitHub Dependabot) are not implemented yet, even though the action description mentions dependency flaws.

## Inputs

| Input | Required | Description |
| --- | --- | --- |
| `openai-api-key` | yes | OpenAI API key used to generate remediations. Store it as a repository secret (for example `OPENAI_API_KEY`). |
| `github-token` | no | Token used to create or update the PR comment. Defaults to `github.token`. |
| `semgrep-config` | no | Ruleset passed to `semgrep --config`. Defaults to `p/default`. Accepts any registry pack (e.g. `p/python`), file, or directory. |

## Outputs

| Output | Description |
| --- | --- |
| `findings-count` | Total Semgrep findings, before the three-finding AI cap. |

Defined in `action.yml`. GitHub maps `openai-api-key` to environment variables `INPUT_OPENAI-API-KEY` and, depending on the runner, `INPUT_OPENAI_API_KEY`.

## Usage

Example consumer workflow (same pattern as `.github/workflows/secgenie.yml` in this repo):

```yaml
name: SecGenie Security Audit

on:
  pull_request:
    branches: [ main ]

jobs:
  audit:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Run SecGenie
        uses: jactheruler/secgenie-action@v1.0.0
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
          github-token: ${{ github.token }}
```

Add `OPENAI_API_KEY` under the consuming repository’s **Settings → Secrets and variables → Actions**. The job needs `pull-requests: write` so the action can comment on the PR. Fork PRs often cannot write comments with the default `GITHUB_TOKEN`; same-repository PRs can.

The job summary is always written when `GITHUB_STEP_SUMMARY` is present, even if commenting fails.

When developing the action locally or on a fork, point `uses:` at your own repo and ref (for example `./` from this repository, or `your-org/secgenie-action@main`).

## Repository layout

| Path | Role |
| --- | --- |
| `action.yml` | Action metadata: name, branding, `openai-api-key` input, Docker runner. |
| `Dockerfile` | Python 3.11 image: installs `git`, Python deps, copies the repo, runs the entrypoint. |
| `requirements.txt` | `requests==2.31.0` and `semgrep==1.183.0`. |
| `src/entrypoint.py` | Validates the API key, then starts the audit. |
| `src/auditor.py` | Semgrep scan, JSON parse, OpenAI remediation loop, then publishes summaries. |
| `src/reporter.py` | Job summary Markdown and GitHub PR comment create/update. |
| `.github/workflows/secgenie.yml` | Sample workflow: on PRs to `main`, checkout then run the local action. |
| `.github/workflows/self-test.yml` | CI check: runs the action on `fixtures/` with a dummy key and fails if `findings-count` is 0. |
| `fixtures/vulnerable/` | Intentionally insecure Python and JS used as scan targets. Never copy this code. |

## Runtime details

- **Python:** 3.11 (`python:3.11-slim`).
- **Git:** installed in the image because Semgrep uses git metadata when scanning a checkout.
- **Scan command:** `semgrep scan --config p/default --json --metrics=off`. An explicit `--config` is required: without one Semgrep uses `auto`, which refuses to run with metrics off. Semgrep errors and unexpected exit codes are printed to the log.
- **LLM:** `POST https://api.openai.com/v1/chat/completions`, model `gpt-4o-mini`, `temperature` 0.2, 30s timeout.
- **Finding cap:** only the first three Semgrep results are sent to the model to keep runtime and API usage bounded.
- **PR reporting:** job summary via `GITHUB_STEP_SUMMARY`; PR comment via the GitHub Issues Comments API, identified by `<!-- secgenie-action-summary -->`.
- **Failure behavior:** missing API key exits with code 1. Unparseable Semgrep JSON is a warning and continues with zero findings. LLM HTTP or network errors are printed as the “remediation” text instead of aborting the job. Job-summary and PR-comment failures are warnings only.

## Local development

You can run the same Python path outside GitHub Actions:

```bash
pip install -r requirements.txt
export INPUT_OPENAI_API_KEY="sk-..."
python src/entrypoint.py
```

Semgrep will scan the current working directory. Run it from the repo root to scan `fixtures/vulnerable/`; any key value works if you only want to check the scan (remediation calls will just report an API error). Docker users can build the image with `docker build -t secgenie-action .` and pass the same environment variable.

## Testing

The repo ships intentionally vulnerable code in `fixtures/vulnerable/` (Python and JavaScript) so there is always something for Semgrep to find. A healthy scan of this repo reports roughly 20 findings: 18 in the fixtures plus 2 `github-actions-mutable-action-tag` findings in the workflow files. **If a run reports 0 findings, the scan is broken.** Check the log for `⚠️ Semgrep` lines.

### Run the action locally with Docker

This runs the same image GitHub builds, with the repo mounted where the runner would put it. A dummy OpenAI key is enough to test the scan; the remediation calls just print an API error.

```bash
docker build -t secgenie-action .

mkdir -p .gh-test && : > .gh-test/output.txt && : > .gh-test/summary.md
docker run --rm \
  -v "$(pwd):/github/workspace" \
  -e GITHUB_WORKSPACE=/github/workspace \
  -e INPUT_OPENAI_API_KEY=dummy \
  -e GITHUB_OUTPUT=/github/workspace/.gh-test/output.txt \
  -e GITHUB_STEP_SUMMARY=/github/workspace/.gh-test/summary.md \
  secgenie-action

cat .gh-test/output.txt   # expect findings-count=<number greater than 0>
```

PowerShell equivalent of the `docker run` step:

```powershell
docker run --rm `
  -v "${PWD}:/github/workspace" `
  -e GITHUB_WORKSPACE=/github/workspace `
  -e INPUT_OPENAI_API_KEY=dummy `
  -e GITHUB_OUTPUT=/github/workspace/.gh-test/output.txt `
  -e GITHUB_STEP_SUMMARY=/github/workspace/.gh-test/summary.md `
  secgenie-action
```

In Git Bash on Windows, prefix the command with `MSYS_NO_PATHCONV=1` and use `$(pwd -W)` so the paths are not rewritten. Use a real key in `INPUT_OPENAI_API_KEY` to see actual AI remediations. Delete `.gh-test/` when you are done.

To list exactly which rules fired, run Semgrep directly inside the image:

```bash
docker run --rm -v "$(pwd):/src" -w /src --entrypoint semgrep secgenie-action \
  scan --config p/default --metrics=off
```

### Run it in CI

- **`.github/workflows/self-test.yml`** runs on every PR, on pushes to `main`, and on manual runs (**Actions → SecGenie Self-Test → Run workflow**). It builds the action from the checkout, scans with a dummy key (no secret or API cost), and fails if `findings-count` is 0. It passes an empty `github-token` so it does not overwrite the real PR comment.
- **`.github/workflows/secgenie.yml`** runs on PRs to `main` with the real `OPENAI_API_KEY` secret and posts the SecGenie comment, so opening a same-repository PR tests the full flow end to end.

### Test as a consumer

To check the published action the way other people will use it, create a separate repository with some vulnerable code (or fork an intentionally vulnerable project such as OWASP PyGoat or Juice Shop), add the usage workflow above with `uses: jactheruler/secgenie-action@<tag>`, set `OPENAI_API_KEY`, and open a PR from a branch in that same repository.

## Author

Jorge Cavazos
