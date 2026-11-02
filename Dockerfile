FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Ensure entrypoint has execution permissions
RUN chmod +x /app/src/entrypoint.py

ENTRYPOINT ["python", "/app/src/entrypoint.py"]
