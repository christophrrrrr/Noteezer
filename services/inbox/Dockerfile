# The inbox service. Only the code and the migrations go into the image; the
# tests, .env, and the virtualenv stay out (see .dockerignore).
# Pinned to one exact build of the image; the tag is kept for people reading it.
# Moved to a newer build when Trivy in CI finds a fixed vulnerability in it
# (2026-11-24: zlib, Debian 13.8; 2026-11-26: zstd, the 2026-11-24 build;
# still Python 3.13.18).
FROM python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /srv
COPY requirements.txt .
# A network that re-signs HTTPS (an antivirus, a company proxy) can lend its
# root certificate as the optional build secret "extra_ca". It is only used
# for this pip install and never lands in the image. CI and Azure pass none.
# pip itself is removed afterwards: it is not needed at runtime, it carries its
# own copies of urllib3, msgpack, and setuptools code that the image scan
# flags, and without it nothing in the running container can install packages.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem; \
        export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --no-cache-dir -r requirements.txt && rm -f /tmp/ca.pem \
    && pip uninstall --yes pip

COPY app ./app
COPY migrations ./migrations

# The commit the image was built from, shown by /health. CI passes the SHA.
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

# Run as a plain user, not root.
RUN useradd --system --uid 10001 --no-create-home noteezer
USER noteezer

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=2)"
CMD ["python", "-m", "app"]
