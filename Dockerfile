FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CONVERTER_HOST=0.0.0.0 CONVERTER_PORT=8766 CONVERTER_DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-impress fonts-noto-cjk fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd --create-home --uid 10001 converter && mkdir /data && chown converter:converter /data
COPY --chown=converter:converter converter ./converter
COPY --chown=converter:converter static ./static
COPY --chown=converter:converter run.py .
USER converter
EXPOSE 8766
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/api/ready', timeout=4)"
CMD ["python", "run.py"]
