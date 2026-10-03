#!/usr/bin/env bash
# Start the registry a pull request's image rehearsal pushes to (ADR-021 section 4).
# Gate-defining file (ADR-0016 rule 7).
#
#   registry.sh
#
# The image is the `registry` pin of deployments/docker/gate-images.Dockerfile, pulled under
# PULL_TIMEOUT for the Docker host's own platform. It listens on 127.0.0.1:REGISTRY_PORT
# (default 5000) only, and this script returns once it answers, or exits 1 after 30 s.
# Nothing is pushed to GHCR from a pull request: this registry ends with its runner.
#
# Written for bash 3.2 (macOS) and later.
set -euo pipefail

here=$(dirname "$0")
port=${REGISTRY_PORT:-5000}
image=$("$here/image.sh" registry)
platform=$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}')
"$here/pull.sh" "$image" "$platform" "${PULL_TIMEOUT:-300}"
docker run -d --name "gora-registry-$port" -p "127.0.0.1:$port:5000" "$image" > /dev/null

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$port/v2/" > /dev/null 2>&1; then
    echo "registry.sh: $image on 127.0.0.1:$port"
    exit 0
  fi
  sleep 1
done
echo "registry.sh: the registry on 127.0.0.1:$port did not answer within 30 s" >&2
exit 1
