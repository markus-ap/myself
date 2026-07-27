# python:3.12-slim, not alpine: cryptography ships prebuilt manylinux aarch64
# wheels but no musl equivalent for every transitive dep, and a Rust build on a
# 2 vCPU / 3.7 GB box is not something a deploy should risk.
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py accounts.py federation.py storage.py update_user.py ./
COPY templates/ ./templates/
COPY static/ ./static/
# The seed is the first-boot content of an empty data volume (see storage.py).
COPY seed/ ./seed/
# Git-derived site dates, written by scripts/generate-page-dates.sh on the
# checkout at `make deploy` — the image has no .git. The trailing glob makes
# this COPY a no-op when the file is absent, so a bare `docker build` still
# works; the app then falls back to boot time.
COPY page-dates.jso[n] ./

ENV MYSELF_DATA=/data
EXPOSE 8080
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8080", "app:app"]
