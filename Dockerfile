# Innexar Workspace API (backend FastAPI) - api.innexar.com.br
# Requer backend/build/lib no repositório (código da aplicação). Se estiver em .gitignore, faça: git add -f backend/build
FROM python:3.12-slim

WORKDIR /app

# Dependências de sistema (opcional, para builds nativos)
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Dependências Python
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Código da aplicação (backend/build/lib contém o pacote "app")
COPY backend/ backend/

# Executar a partir de backend/build/lib (app.main). Se build não existir, o build falhará.
ENV PYTHONPATH=/app/backend/build/lib
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:${PORT}/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
