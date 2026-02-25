FROM python:3.11-slim

WORKDIR /app

# system deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# copy repo
COPY . /app

ENV PYTHONUNBUFFERED=1

# Expose expected ports: dashboard (8000), analytics (8090), websocket (8765)
EXPOSE 8000 8090 8765

# Default: run everything but disable BLE (more portable in containers)
CMD ["python", "run_all.py", "--no-ble"]
