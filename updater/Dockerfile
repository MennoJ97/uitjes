# uitjes-updater — rebuilds events.json from every enabled source, on a schedule.
# Runs supercronic (cron-for-containers: logs to stdout, respects env, no root).
# Build context is this directory; the site it feeds is written to /site/data,
# a volume the nginx container serves as data/ (see the README).
FROM python:3.12-slim

# TARGETARCH is set automatically by BuildKit (amd64 / arm64) — picks the right binary.
ARG TARGETARCH=amd64
ARG SUPERCRONIC_VERSION=v0.2.51
# sha256 of the release assets (GitHub's asset digest; matches the SHA1SUM in the
# release notes). Bump together with the version: the build fails on a mismatch.
ARG SUPERCRONIC_SHA256_amd64=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ARG SUPERCRONIC_SHA256_arm64=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
 && case "$TARGETARCH" in amd64) sum=$SUPERCRONIC_SHA256_amd64 ;; arm64) sum=$SUPERCRONIC_SHA256_arm64 ;; esac \
 && echo "${sum:?no checksum for $TARGETARCH}  /usr/local/bin/supercronic" | sha256sum -c - \
 && chmod +x /usr/local/bin/supercronic

WORKDIR /work
# No pip install: the builder is Python 3.12 standard library only.
COPY build.py sources.toml crontab entrypoint.sh ./
COPY uitradar ./uitradar
# The mail-alerts service (uitjes-alerts in compose) runs from this same image:
# it matches watches with uitradar's category labels, so one build keeps the
# two in step.
COPY alerts.py ./
COPY uitalerts ./uitalerts
COPY data ./data
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
