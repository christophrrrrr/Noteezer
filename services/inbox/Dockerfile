# The inbox service. Only the code and the migrations go into the image; the
# tests, .env, and the virtualenv stay out (see .dockerignore).
# Pinned to one exact build of the image; the tag is kept for people reading it.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /srv
COPY requirements.txt .
# A network that re-signs HTTPS (an antivirus, a company proxy) can lend its
# root certificate as the optional build secret "extra_ca". It is only used
# for this pip install and never lands in the image. CI and Azure pass none.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem; \
        export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --no-cache-dir -r requirements.txt && rm -f /tmp/ca.pem

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
