# Documented learning: SecGenie Action

This note records what I learned while building **secgenie-action**, and *how* I learned it. Each section is tied to files that still exist in this repository. The project is a Docker GitHub Action that runs Semgrep, asks OpenAI for remediations, and reports those findings on a pull request.

I am Jorge Cavazos (`action.yml` author). The path through the repo is roughly: metadata and container (`action.yml`, `Dockerfile`, `requirements.txt`) → scan and LLM (`src/entrypoint.py`, `src/auditor.py`) → publishing (`src/reporter.py`, `.github/workflows/secgenie.yml`) → explaining it for others (`README.md`) → testing it against known-vulnerable code (`fixtures/vulnerable/`, `.github/workflows/self-test.yml`).

---

## 1. A GitHub Action is a contract plus a runner

**What I learned**

An Action is not just a Python script. GitHub needs a machine-readable contract: name, description, branding, inputs, and *how* to run. This repo uses `runs.using: docker` and `image: Dockerfile`, so every job builds the image and executes the container `ENTRYPOINT`.

Inputs are part of that contract. `openai-api-key` is required. `github-token` is optional and defaults to `${{ github.token }}` so consumers do not have to invent a token unless they want a PAT.

**How I learned it**

By writing `action.yml` and then wiring a consumer in `.github/workflows/secgenie.yml`. The workflow’s `with:` block only works because those input names exist in `action.yml`. Branding (`icon: shield`, `color: purple`) showed up as Action marketplace metadata, not as runtime behavior.

---

## 2. Action inputs arrive as environment variables, not function arguments

**What I learned**

GitHub does not call `main(openai_api_key=...)`. It injects `INPUT_<NAME>` into the process environment. Hyphens in the input name can stay as hyphens or be rewritten as underscores depending on the runner, so the entrypoint has to accept both:

- `INPUT_OPENAI-API-KEY` and `INPUT_OPENAI_API_KEY`
- `INPUT_GITHUB-TOKEN` and `INPUT_GITHUB_TOKEN`, plus a fallback to `GITHUB_TOKEN`

If the OpenAI key is missing, the process should exit with code 1. The GitHub token is optional because job summaries still work without commenting.

**How I learned it**

`src/entrypoint.py` is the first code that runs (`Dockerfile` `ENTRYPOINT`). Debugging “why is the key empty?” led to reading those `INPUT_*` names instead of looking for CLI flags. The comments in `action.yml` and `entrypoint.py` exist because that mapping is easy to miss.

---

## 3. Docker Actions have two directories, and they are not the same

**What I learned**

The image’s `WORKDIR` is `/app` — that is *this* Action’s source, copied in with `COPY . .`. The repository being audited is mounted by the runner as `GITHUB_WORKSPACE` (usually `/github/workspace`).

If Semgrep runs with no `cwd`, it scans the Action image, not the pull request. The auditor therefore prefers `os.environ["GITHUB_WORKSPACE"]` and only falls back to `os.getcwd()` for local runs.

The image also needs `git`. Semgrep uses git metadata and ignore rules on a checkout. `python:3.11-slim` does not include git unless you install it.

**How I learned it**

From `Dockerfile` (`WORKDIR /app`, `apt-get install git`, `ENTRYPOINT`) together with the `scan_cwd` logic in `src/auditor.py`. Building a Docker Action forces you to notice the difference between “code that implements the tool” and “code the tool is scanning.”

---

## 4. Pinning dependencies and keeping the image small is part of the Action

**What I learned**

CI should be reproducible. `requirements.txt` pins `requests==2.31.0` and `semgrep==1.183.0` (it was `1.60.0` until testing showed that version was too old; see section 14). The Dockerfile copies that file first, then `pip install --no-cache-dir`, then copies the rest of the repo. That layering is the usual Docker lesson: dependency install changes less often than source.

Slim images still need `--no-install-recommends` and `rm -rf /var/lib/apt/lists/*` so the Action does not ship extra apt metadata.

**How I learned it**

`Dockerfile` + `requirements.txt`. There is no extra package manager or lockfile in this repo; those two files *are* the runtime bill of materials.

---

## 5. SAST in CI is a subprocess plus JSON, not a library call

**What I learned**

Semgrep is a CLI. The auditor runs `semgrep scan --config p/default --json --metrics=off`, captures stdout, and parses JSON. (The original command had no `--config`; section 13 explains why that silently found nothing.) Matches live under `results`. Each match has:

- `path`
- `start.line`
- `extra.lines` (snippet)
- `extra.message`

`--metrics=off` keeps a CI job from sending Semgrep telemetry. If stdout is not valid JSON, the scan should warn and continue with zero findings, not crash the whole Action. Semgrep's `errors` array and exit code also have to be checked; section 15 covers why.

**How I learned it**

`src/auditor.py`: `subprocess.run(..., capture_output=True, text=True)` and `data.get("results", [])`. I learned the field names by looking at what the loop actually reads, not from a separate schema file (this repo does not vendor Semgrep’s schema).

---

## 6. Calling an LLM from CI is a product decision, not just an API call

**What I learned**

The OpenAI Chat Completions endpoint is a normal HTTPS POST (`requests.post` to `https://api.openai.com/v1/chat/completions`). The interesting parts are the *constraints*:

- Model: `gpt-4o-mini` (cost and latency for CI)
- `temperature: 0.2` so remediations do not swing wildly between pushes
- Timeout 30 seconds
- Only the **first three** findings are sent, so a noisy scan cannot burn tokens or time
- HTTP and network errors become *strings* that still get printed and posted, instead of uncaught exceptions that fail the job

A system prompt defines SecGenie as an appsec assistant that should return an explanation plus a remediated snippet. The Action still does **not** apply that snippet. Logging and PR comments are suggestions only.

**How I learned it**

`get_secgenie_remediation` in `src/auditor.py`. The three-finding cap sits in the `for finding in findings[:3]` loop. `README.md` states the same limits in prose, which is how I confirmed the behavior was intentional rather than leftover debug code.

---

## 7. Job logs are not enough; PRs need a summary people will actually see

**What I learned**

Printing to stdout only helps people who open the Actions log. GitHub gives two better surfaces:

1. **Job summary** — append Markdown to the file path in `GITHUB_STEP_SUMMARY`. It shows on the workflow run and the PR Checks tab. The env var is missing locally, so skip instead of failing.
2. **PR comment** — conversation tab. Pull request comments use the *issues* comments API: `POST /repos/{owner}/{repo}/issues/{number}/comments`. Updating uses `PATCH .../issues/comments/{id}`.

The same Markdown is reused for both. That keeps Checks and Conversation in sync.

**How I learned it**

`src/reporter.py` (`write_step_summary`, `publish_pr_comment`) and the last lines of `run_secgenie_audit` in `src/auditor.py`. `README.md` documents that the Action does not open fix PRs or fail on finding count — reporting is the whole user-facing output.

---

## 8. Permissions, tokens, and event payloads are required to talk to GitHub

**What I learned**

Default `GITHUB_TOKEN` cannot comment unless the job declares `permissions: pull-requests: write` (and `contents: read` for checkout). The Action receives the token as the `github-token` input.

Context for “which PR?” does not come from `git`. The runner writes the webhook JSON to `GITHUB_EVENT_PATH`. The PR number is `pull_request.number` (or `number`). `GITHUB_REPOSITORY` is `owner/repo`. `GITHUB_EVENT_NAME` must be `pull_request` or we skip the comment.

`GITHUB_API_URL` exists so the same code can point at GitHub Enterprise instead of hard-coding `api.github.com`.

Fork PRs are a separate lesson: the default token often cannot write comments on the base repo. Same-repository PRs can.

**How I learned it**

`.github/workflows/secgenie.yml` (`permissions` + `github-token: ${{ github.token }}`) plus `_pull_request_number()` and `publish_pr_comment()` in `src/reporter.py`. The README’s usage section is where the fork-token limitation is written down.

---

## 9. Idempotent PR comments need a marker, not hope

**What I learned**

Every push to a PR re-runs the workflow. If you only `POST`, you spam the thread. The durable pattern is:

1. Put a hidden HTML comment in the body, e.g. `<!-- secgenie-action-summary -->`
2. List existing issue comments
3. If a body contains that marker, `PATCH` that comment; otherwise `POST`

GitHub still stores HTML comments in the markdown body even though they are invisible in the UI.

**How I learned it**

`COMMENT_MARKER` and `_existing_comment_id` in `src/reporter.py`. The marker is the first line of `build_summary_markdown` so matching stays simple.

---

## 10. Consume your own Action with `uses: ./`

**What I learned**

Pinning `jactheruler/secgenie-action@v1.0.0` is correct for *other* repositories. Inside *this* repository, that tag would run yesterday’s code. `uses: ./` builds the Dockerfile from the current checkout so CI actually tests the branch.

**How I learned it**

The comment above the Run SecGenie step in `.github/workflows/secgenie.yml`. The README still shows the tagged form so consumers copy the public interface.

---

## 11. Documentation has to match the code, including gaps

**What I learned**

`action.yml` still describes “security and dependency” auditing. The only scanner in the tree is Semgrep SAST. There is no `pip-audit`, npm audit, or Dependabot config. Documenting that gap in `README.md` is more honest than letting the Action description oversell.

I also learned that a useful README for an Action includes: how it works, inputs, a copy-paste workflow, layout, runtime limits, failure behavior, and how to run the Python entrypoint locally with `INPUT_OPENAI_API_KEY`.

**How I learned it**

By comparing `action.yml`’s description with `src/auditor.py` (Semgrep only) and writing `README.md` from the files rather than from the original one-line stub.

---

## 12. Split scan logic from publish logic

**What I learned**

`src/auditor.py` owns Semgrep and OpenAI. `src/reporter.py` owns Markdown, the step summary file, and the GitHub API. `src/entrypoint.py` only validates env and starts the audit. `src/__init__.py` is empty; this is a script package launched as `python /app/src/entrypoint.py`, so imports resolve from `src/` because that directory is on `sys.path`.

Keeping reporter separate made it possible to send the *same* markdown to two GitHub surfaces without stuffing HTTP into the scan loop.

**How I learned it**

The import in `auditor.py`: `from reporter import build_summary_markdown, publish_pr_comment, write_step_summary`. The Dockerfile entrypoint path explains why those imports work without installing the package.

---

## 13. A scanner that finds nothing might just be broken

**What I learned**

The original scan command was `semgrep scan --json --metrics=off` with no `--config`. With no config, Semgrep falls back to `--config auto`, and `auto` refuses to run when metrics are off. Semgrep still prints valid JSON, but `results` is empty and the reason sits in `errors`. The auditor only read `results`, so every run reported "0 security findings" and looked like a clean repo.

The fix is an explicit ruleset. The action now has a `semgrep-config` input that defaults to `p/default`, which works with metrics off.

**How I learned it**

By asking how to test the action at all. Scanning a clean repo can't tell "no vulnerabilities" apart from "the scanner never ran." You need a target where you already know the right answer is "many findings," and that one question exposed the bug.

---

## 14. Pinned tool versions can rot against a live rule registry

**What I learned**

Pinning `semgrep==1.60.0` made the *binary* reproducible, but `p/default` is downloaded from semgrep.dev at scan time and keeps changing. The current rules use syntax 1.60.0 cannot parse, so the scan failed with `InvalidRuleSchemaError` and exit code 7. Bumping to `semgrep==1.183.0` fixed it.

So pinning only freezes half the system. When rules come from a remote registry, the pinned version has to be bumped regularly, or the rules need to be vendored into the repo.

**How I learned it**

The first Docker run after the `--config` fix still reported 0 findings. This time the new error logging printed `⚠️ Semgrep error: InvalidRuleSchemaError` and the exit code, and Semgrep's stderr said rules "use functionality introduced in later versions of semgrep." Without that logging this would have looked exactly like the bug in section 13.

---

## 15. Make failures loud: check `errors` and exit codes, not just results

**What I learned**

Semgrep's exit codes mean different things: `0` is a clean run, `1` means findings (with `--error`), and anything else (for example `7`) means the scan itself failed. Config and parse problems show up in the JSON `errors` array and on stderr, often alongside an empty but valid `results`. The auditor now prints each entry in `errors`, warns on unexpected exit codes, and prints the tail of stderr.

The scan still doesn't fail the job, but the log now explains a 0.

**How I learned it**

Adding this logging was meant to guard against the section 13 bug, and it caught the section 14 bug on its very next run. Some warnings are harmless, though: Semgrep's Dockerfile parser logs a syntax warning on the `git \` line continuation in `Dockerfile`, and the scan still completes.

---

## 16. Testing an Action means giving it a known answer

**What I learned**

- **Fixtures:** `fixtures/vulnerable/app.py` and `server.js` hold deliberately insecure code: command injection, SQL injection, `pickle.loads`, MD5 for passwords, Flask debug mode, `eval`, and `child_process.exec`. They live outside `tests/` because Semgrep's default ignore list skips `test/` and `tests/` directories, so fixtures there would never be scanned.
- **Outputs make Actions testable:** the action writes `findings-count` to the file in `GITHUB_OUTPUT`. A later workflow step reads `steps.secgenie.outputs.findings-count` and fails if it is 0. Without an output there is nothing to assert on except log text.
- **Dummy keys keep CI free:** because LLM errors become strings instead of crashes, `self-test.yml` can run with `openai-api-key: dummy-key-for-self-test`. It tests the build, the scan, the summary, and the output without a secret or API cost.
- **Don't let the self-test overwrite the real comment:** both workflows run on PRs and would update the same marker comment, so the self-test passes an empty `github-token` to skip commenting.
- **Mirror the runner locally:** `docker run` with the repo mounted at `/github/workspace` and `GITHUB_WORKSPACE`, `GITHUB_OUTPUT`, and `GITHUB_STEP_SUMMARY` pointed at local files reproduces what GitHub does. In Git Bash on Windows, `MSYS_NO_PATHCONV=1` and `pwd -W` stop the volume paths from being rewritten.
- **`.dockerignore` keeps fixtures out of the image:** the fixtures only need to be in the scanned workspace, not in `/app`.
- **Results show what you didn't plant:** the scan found 20 issues: 18 in the fixtures, plus 2 real `github-actions-mutable-action-tag` findings because the workflows use `actions/checkout@v4` instead of a commit SHA. It did *not* flag the planted `yaml.load`, so a ruleset doesn't catch everything you expect. Results come back sorted by path, so the `.github/` findings take two of the three AI-remediation slots ahead of the fixtures.

**How I learned it**

By building the image and running it locally with a dummy key, reading `findings-count` from the output file, and then listing findings by rule with `semgrep scan --json` inside the same image. The README's **Testing** section has the exact commands.

---

## How the pieces fit (learning map)

```text
.github/workflows/secgenie.yml
  checkout + uses: ./ + secrets + permissions
        ↓
action.yml  →  Docker image (Dockerfile + requirements.txt)
        ↓
src/entrypoint.py     INPUT_* keys
        ↓
src/auditor.py        Semgrep JSON → OpenAI (max 3) → reports[]
        ↓
src/reporter.py       Markdown → GITHUB_STEP_SUMMARY
                                 → PR comment (create or update)

.github/workflows/self-test.yml
  uses: ./ on fixtures/vulnerable/ with a dummy key
        ↓
findings-count output (GITHUB_OUTPUT)  →  fail if 0
```

What I would still need to learn next, based on what the files *do not* contain: applying patches automatically, failing the job on severity, a real dependency scanner, handling fork-PR tokens without `pull_request_target`, unit tests for `reporter.py` with mocked GitHub API calls, keeping the Semgrep pin current (or vendoring rules), and confirming on a real PR that an empty `github-token` actually skips the comment.
