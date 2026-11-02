import subprocess
import json
import requests

def run_secgenie_audit(api_key):
    print("🔍 SecGenie running Semgrep SAST scan...")
    
    # Run Semgrep locally against repository root
    scan_command = ["semgrep", "scan", "--json", "--metrics=off"]
    result = subprocess.run(scan_command, capture_output=True, text=True)
    
    findings = []
    if result.stdout:
        try:
            data = json.loads(result.stdout)
            findings = data.get("results", [])
        except json.JSONDecodeError:
            print("⚠️ SecGenie Warning: Could not parse Semgrep output.")

    print(f"📌 SecGenie detected {len(findings)} security findings.")

    # Process findings with LLM
    for finding in findings[:3]:  # Top 3 findings
        file_path = finding.get("path")
        line = finding.get("start", {}).get("line")
        code_snippet = finding.get("extra", {}).get("lines", "")
        message = finding.get("extra", {}).get("message", "")

        print(f"\n🧞 SecGenie analyzing issue in {file_path}:{line}...")
        ai_fix = get_secgenie_remediation(api_key, message, code_snippet)
        
        print("\n--- 🧞 SecGenie Remediation Suggestion ---")
        print(ai_fix)
        print("-------------------------------------------\n")

def get_secgenie_remediation(api_key, vulnerability_description, code_snippet):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
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
                )
            },
            {
                "role": "user", 
                "content": f"Vulnerability: {vulnerability_description}\nCode Snippet:\n{code_snippet}"
            }
        ],
        "temperature": 0.2
    }
    
    try:
        response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            return f"Error from SecGenie LLM API: {response.text}"
    except Exception as e:
        return f"Failed to contact AI service: {str(e)}"
