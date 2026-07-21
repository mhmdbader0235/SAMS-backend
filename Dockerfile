FROM python:3.12-slim

# Create non-root user for security
RUN addgroup --system app && adduser --system --group app

WORKDIR /workspace

# Install dependencies first (layer cache optimization)
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Switch to non-root user
USER app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
