FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --system --uid 1001 appuser
USER appuser

EXPOSE 8000
ENV PYTHONUNBUFFERED=1

# --preload loads the app once (so the schema is created once)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--preload", "apiv1.app:app"]
