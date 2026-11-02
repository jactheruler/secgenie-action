import os
import sys
from auditor import run_secgenie_audit


def main():
    print("🧞 SecGenie starting security audit...")

    # GitHub Actions injects action.yml inputs as INPUT_* environment variables.
    # The hyphenated name is the documented mapping; some environments also
    # expose an underscored alias.
    api_key = os.environ.get("INPUT_OPENAI-API-KEY") or os.environ.get("INPUT_OPENAI_API_KEY")

    if not api_key:
        print("❌ SecGenie Error: Missing 'openai-api-key' input.")
        sys.exit(1)

    github_token = (
        os.environ.get("INPUT_GITHUB-TOKEN")
        or os.environ.get("INPUT_GITHUB_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )

    run_secgenie_audit(api_key, github_token)


if __name__ == "__main__":
    main()
