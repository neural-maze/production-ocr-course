FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV MCP_TRANSPORT=sse
ENV PORT=8000
ENV HOST=0.0.0.0
ENV OCR_API_URL=http://ocr-api-service:80

EXPOSE 8000

CMD ["python", "server.py"]
