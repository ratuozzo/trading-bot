# Backend image. Paper trading only — there are no exchange credentials here.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY web/ ./web/
COPY config.yaml ./

# Loopback by default is useless in a container, so bind everywhere — which
# is exactly why the server refuses to start on 0.0.0.0 without TB_API_TOKEN.
ENV TB_HOST=0.0.0.0 TB_PORT=8787 TB_STATE_FILE=/data/state.json
VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz').status==200 else 1)"

CMD ["python", "-m", "tradingbot.server"]
