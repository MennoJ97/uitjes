#!/bin/sh
# Build once on first boot (so the site isn't empty before the first cron tick),
# then hand off to supercronic for the recurring schedule.
set -eu

mkdir -p /site/data /work/data/cache

if [ ! -f /site/data/events.json ]; then
  echo "[entrypoint] no events.json found — running an initial build in the background"
  (
    python build.py --out /site/data/events.json \
      || echo "[entrypoint] initial build failed — cron will retry on schedule"
    echo "[entrypoint] initial build done"
  ) &
fi

echo "[entrypoint] starting supercronic"
# Absolute path is required: as PID 1, supercronic re-execs itself via argv[0] to
# reap zombies, and a bare "supercronic" fails that re-exec (no PATH lookup in execve).
exec /usr/local/bin/supercronic /work/crontab
