import os
import sys
from auditor import run_secgenie_audit

def main():
    print("🧞 SecGenie starting security audit...")

    # Fetch inputs passed from GitHub Action environment
    api_key = os.environ.get("INPUT_OPENAI-API-KEY") or os.environ.get("INPUT_OPENAI_API_KEY")
    
    if not api_key:
        print("❌ SecGenie Error: Missing 'openai-api-key' input.")
        sys.exit(1)

    run_secgenie_audit(api_key)

if __name__ == "__main__":
    main()
