FROM python:3.12-slim

# Evitar buffers en logs y archivos temporales .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Bogota

WORKDIR /app

# Dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código modular del bot, datos iniciales y pruebas
COPY app/ ./app/
COPY data/ ./data/
COPY tests/ ./tests/
COPY bot.py .

EXPOSE 8002

# Healthcheck nativo contra el servidor FastAPI integrado
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8002/health || exit 1

CMD ["python3", "bot.py"]
