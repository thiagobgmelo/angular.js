FROM python:3.11-slim

# usuário não-root (boas práticas de container)
RUN useradd --create-home --shell /usr/sbin/nologin trader

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY dashboard ./dashboard
COPY config.yaml .

# diretório de dados (SQLite) montado como volume
RUN mkdir -p /data && chown trader:trader /data /app
ENV CRYPTO_TRADER_DB_DIR=/data \
    HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

USER trader
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import os,urllib.request;t=os.environ.get('API_TOKEN','');u='http://127.0.0.1:8000/api/health'+(f'?token={t}' if t else '');urllib.request.urlopen(u,timeout=4)"

# dentro do container o bind precisa ser 0.0.0.0 (a rede do docker isola);
# a exposição pública é decisão do compose/reverse proxy
CMD ["python", "-m", "uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
