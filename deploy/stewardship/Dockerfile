# syntax=docker/dockerfile:1
FROM python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# The backup profile dumps the PostgreSQL 18 service with the matching client,
# which Debian trixie does not carry: take it from the PostgreSQL project's
# repository, pinned by version, with the signing key checked by digest. That
# repository keeps only the newest build per major version, so this pin and
# the postgres:18.6 server digest in runtime_topology.py move together when a
# new build is published; until then the build fails with "version not found".
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/keyrings/pgdg.asc \
    && echo "0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76  /usr/share/keyrings/pgdg.asc" | sha256sum -c - \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18=18.6-1.pgdg13+2 \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements/stewardship.txt requirements/stewardship-build.txt requirements/
# Phase 0 keeps the locked test tools in the same image for local/CI parity.
# Release size/scanning and the artifact build pipeline are owned by OPS-09.
RUN python -m pip install --no-cache-dir -r requirements/stewardship-build.txt \
    && python -m pip install --no-cache-dir -r requirements/stewardship.txt
COPY pyproject.toml README.md ./
# Immutable freshness evidence is separate from every live test fixture mount.
COPY pyproject.toml README.md .dockerignore baked-build-inputs/
COPY requirements/stewardship.txt requirements/stewardship-build.txt baked-build-inputs/requirements/
COPY deploy/stewardship/Dockerfile deploy/stewardship/Dockerfile.dockerignore baked-build-inputs/deploy/stewardship/
COPY src/ src/
# Editable metadata points at /app/src, the only live-code development mount.
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation -e . \
    && groupadd --gid 10001 parishkit \
    && useradd --uid 10001 --gid 10001 --no-create-home parishkit
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["pk-stewardship"]
CMD ["service", "--profile", "production", "--service-role", "web"]
