FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
ARG INSTALL_OAUTH=false
RUN if [ "$INSTALL_OAUTH" = "true" ]; then pip install '.[oauth]'; else pip install .; fi \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp \
    && if [ "$INSTALL_OAUTH" = "true" ]; then \
         install -d -m 0700 -o mcp -g mcp /var/lib/teslamate-mcp; \
       fi
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]
CMD ["python", "-m", "teslamate_mcp"]
