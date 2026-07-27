#!/bin/sh
# Derive the site's created/modified timestamps from git history into
# page-dates.json, read by the server at boot.
#
# Runs on the CHECKOUT (make deploy / make verify), never in the container —
# the image has no .git, and the box has no Node/Python outside containers, so
# this is pure git + POSIX sh. The file is gitignored and rides into the image
# via the Dockerfile's optional COPY.
#
# created  = author date of the oldest commit in the repo
# modified = author date of the newest commit
#
# Box-wide convention: naustet-server ADR 0015 / msge-no ADR 0004. NOTE: a
# shallow clone collapses both onto the newest commit; keep the box checkout a
# full clone (and use fetch-depth: 0 in any CI).
set -eu
cd "$(dirname "$0")/.."

modified=$(git log -1 --format=%aI 2>/dev/null || true)
created=$(git log --format=%aI 2>/dev/null | tail -n 1 || true)
if [ -z "$modified" ] || [ -z "$created" ]; then
  echo "WARN: no git history — skipping (pages will stamp boot time)" >&2
  exit 0
fi
printf '{\n  "generated": "%s",\n  "created": "%s",\n  "modified": "%s"\n}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$created" "$modified" > page-dates.json
echo "Wrote page-dates.json"
