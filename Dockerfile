FROM python:3.12-slim

WORKDIR /app

# Install system deps for subprocess/terminal execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make curl git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium
RUN playwright install chromium --with-deps || true

COPY . .

# Create workspace directory
RUN mkdir -p ~/yubilab-workspaces /tmp/yubilab_uploads

EXPOSE 8000

CMD ["uvicorn", "worker.app:app", "--host", "0.0.0.0", "--port", "8000"]
