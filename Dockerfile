FROM python:3.11-slim

# Semgrep uses git (ignore rules, repo metadata) when scanning a checkout.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/src/entrypoint.py

# GitHub Actions Docker runner executes this entrypoint with the workspace mounted.
ENTRYPOINT ["python", "/app/src/entrypoint.py"]
