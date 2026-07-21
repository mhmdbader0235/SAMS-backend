FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api_gateway.py .

EXPOSE 8000

CMD ["uvicorn", "api_gateway:app", "--host", "0.0.0.0", "--port", "8000"]
