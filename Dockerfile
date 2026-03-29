FROM python:3.12-slim

ARG APP_VERSION=dev
ARG APP_COMMIT=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION} \
    APP_COMMIT=${APP_COMMIT}

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn requests yt-dlp jinja2 python-multipart pycryptodome

WORKDIR /app
COPY app.py core.py wecom.py download.py entrypoint.sh /app/
COPY static /app/static
COPY templates /app/templates
RUN chmod +x /app/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["serve"]
