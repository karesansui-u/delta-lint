FROM python:3.11-slim

WORKDIR /app

# Install git (needed for repo cloning)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy delta-lint scripts + app
COPY scripts/ ./scripts/
COPY app/ ./app/

# Anthropic SDK (for LLM calls)
RUN pip install --no-cache-dir anthropic

EXPOSE 3000

CMD ["uvicorn", "app.webhook:app", "--host", "0.0.0.0", "--port", "3000"]
