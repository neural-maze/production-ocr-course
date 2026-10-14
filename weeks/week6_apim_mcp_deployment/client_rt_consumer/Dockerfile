FROM nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 1. Install System Dependencies
RUN apt-get update && apt-get install -y \
    python3-pip python3-dev \
    libgl1-mesa-glx libglib2.0-0 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# 2. Install SDK and Redis Orchestration Libraries
RUN pip3 install --no-cache-dir \
    "glmocr[selfhosted,layout]==0.1.4" \
    redis \
    httpx \
    loguru \
    opencv-python-headless \
    pdf2image PyMuPDF transformers==5.3.0

# 4. Copy configuration and Worker source code
COPY config.yaml /app/config.yaml
COPY worker.py /app/worker.py

ENV GLMOCR_CONFIG_PATH=/app/config.yaml

# Pull-based worker (Redis)
CMD ["python3", "worker.py"]
