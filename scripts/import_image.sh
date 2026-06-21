#!/usr/bin/env bash
# Import the packed rock image into the Canonical Kubernetes cluster's containerd
# so the charm's pod can run it without an external registry. Run once after the
# rock is built+converted (the conversion step is non-privileged and may already
# be done): needs sudo for the containerd socket.
#
#   bash scripts/import_image.sh
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TAR=/tmp/standup-image.tar
TAG="standup-dashboard:0.1"
CTR=/snap/k8s/current/bin/ctr

# (Re)build the tagged docker-archive from the rock if it's not already present.
if [ ! -f "$TAR" ]; then
  ROCK="$(ls -t "$PROJECT_DIR"/*.rock 2>/dev/null | head -1)"
  [ -n "$ROCK" ] || { echo "No .rock found — run 'rockcraft pack' first."; exit 1; }
  echo "Converting $ROCK -> $TAR ($TAG)"
  rockcraft.skopeo --insecure-policy copy "oci-archive:${ROCK}" "docker-archive:${TAR}:${TAG}"
fi

# Find the containerd socket Kubernetes actually uses (the one already holding
# k8s images like pause/coredns).
SOCK=""
for s in /run/containerd/containerd.sock \
         /var/snap/k8s/common/run/containerd.sock \
         /var/snap/k8s/common/run/containerd/containerd.sock; do
  [ -S "$s" ] || continue
  if sudo "$CTR" --address "$s" -n k8s.io images ls -q 2>/dev/null | grep -q .; then
    SOCK="$s"; break
  fi
done
[ -n "$SOCK" ] || { echo "Could not locate the k8s containerd socket."; exit 1; }

echo "Importing $TAR into containerd ($SOCK, namespace k8s.io)..."
sudo "$CTR" --address "$SOCK" -n k8s.io images import "$TAR"

echo "=== standup-dashboard image now in containerd ==="
sudo "$CTR" --address "$SOCK" -n k8s.io images ls 2>/dev/null | grep -i standup || echo "NOT FOUND (import may have failed)"
