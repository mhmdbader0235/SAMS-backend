FROM python:3.12-slim

# Create non-root user for security
RUN addgroup --system app && adduser --system --group app

WORKDIR /workspace

# Install dependencies first (layer cache optimization)
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code, plus Alembic (kept as siblings of app/ here, exactly
# as they are in back/, so alembic.ini's %(here)s-relative script_location
# and env.py's own path math resolve the same way as they do outside Docker).
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Switch to non-root user
USER app

EXPOSE 8001

ENTRYPOINT ["./entrypoint.sh"]
