# Immagine per il deploy su Northflank (o qualsiasi container).
# L'agente gira dentro il container: SQLite su volume persistente.
#
# L'immagine base e' fissata per digest: un tag mobile puo' cambiare
# contenuto da un giorno all'altro (catena di fornitura).
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CARPOOL_DATA_DIR=/app/data \
    HOST=0.0.0.0 \
    PORT=8787

WORKDIR /app

# Dipendenze di sistema: bcrypt e cryptography (dietro pywebpush) compilano
# solo se manca la ruota precompilata.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y

# Codice e frontend.
COPY app ./app
COPY static ./static
COPY scripts ./scripts

# Il volume persistente viene montato qui: contiene carpool.db e gtfs.db.
RUN mkdir -p /app/data && useradd --uid 1000 --create-home carpool \
    && chown -R carpool:carpool /app
USER carpool

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8787/api/auth/status || exit 1

# L'agente parte subito. Gli orari GTFS si importano a parte
# (python -m app.cli gtfs-update): importarli qui bloccherebbe l'avvio
# per qualche minuto e ripeterebbe il download a ogni riavvio.
#
# --proxy-headers: dietro un reverse proxy (tunnel, Nginx) uvicorn deve
# leggere X-Forwarded-Proto, altrimenti il cookie di sessione perderebbe
# il flag Secure e il login dal frontend su un altro dominio fallirebbe.
# --forwarded-allow-ips: si fidano solo gli header inoltrati che arrivano
# dal proxy locale, non da chiunque.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
