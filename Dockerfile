# ─── Build: imagen ligera, dependencias pinneadas ──────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias del sistema solo lo necesario para compilar wheels nativos
# (chardet pure-python, pero algunas wheels piden gcc).
# rclone se usa para el backup nocturno de /app/data/adjuntos a Backblaze B2
# (ver correos/management/commands/backup_adjuntos_b2.py).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        wget \
        rclone \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Instala deps en una capa cacheable
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia la app
COPY . .

# Genera estáticos (manifest hashed, comprimido vía whitenoise)
# Forzamos SQLite efimera durante el build para que collectstatic no
# intente conectarse al Postgres de runtime (no resoluble desde la red de build).
RUN SECRET_KEY=build-only-not-used DEBUG=True \
    DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    python manage.py collectstatic --noinput

# Carpetas persistentes — los volúmenes se montan acá
RUN mkdir -p /app/data/mbox /app/data/adjuntos /app/staticfiles

EXPOSE 8000

# Healthcheck Docker-nativo para Coolify
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Comando: gunicorn con 3 workers (ajusta según RAM del server)
# --max-requests + jitter para reciclar workers (evita memory leaks)
# --limit-request-* anti-DoS por requests gigantes:
#   - line: largo total del request line (URL incluida)
#   - field-size: tamaño de cada header
#   - fields: cantidad de headers
CMD ["python", "-m", "gunicorn", "archivo_pietramonte.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "60", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "50", \
     "--limit-request-line", "8190", \
     "--limit-request-field_size", "8190", \
     "--limit-request-fields", "100", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
