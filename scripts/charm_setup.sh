#!/usr/bin/env bash
# One-time privileged setup for building + deploying the standup-dashboard charm
# on Canonical Kubernetes. Run as your normal user (it uses sudo internally):
#     bash scripts/charm_setup.sh
# Idempotent-ish: safe to re-run; steps that already exist are skipped.
set -uo pipefail

say() { printf '\n========== %s ==========\n' "$*"; }

say "1/6 install build + cluster snaps (rockcraft, charmcraft, k8s, skopeo)"
sudo snap install rockcraft --classic
sudo snap install charmcraft --classic
sudo snap install skopeo
sudo snap install k8s --classic

say "2/6 LXD (rockcraft/charmcraft build backend)"
# init a minimal LXD if it isn't already, and put this user in the lxd group so
# rockcraft/charmcraft can drive it without sudo (use `sg lxd -c ...` this session).
sudo lxd init --minimal || true
sudo usermod -aG lxd "$USER" || true

say "3/6 bootstrap Canonical Kubernetes"
if sudo k8s status >/dev/null 2>&1; then
  echo "k8s already bootstrapped"
else
  sudo k8s bootstrap
fi
sudo k8s status --wait-ready --timeout 5m || true
say "3b/6 enable cluster features (network/dns/storage/ingress/lb)"
sudo k8s enable network dns local-storage ingress load-balancer || true
sudo k8s status

say "4/6 register the cluster with Juju"
mkdir -p "$HOME/.kube"
sudo k8s config | tee "$HOME/.kube/config" >/dev/null
sudo chown "$USER":"$USER" "$HOME/.kube/config"
if juju clouds --client 2>/dev/null | grep -q '^ck8s'; then
  echo "juju cloud ck8s already registered"
else
  sudo k8s config | juju add-k8s ck8s --client
fi

say "5/6 bootstrap a Juju controller on the cluster (several minutes)"
if juju controllers 2>/dev/null | grep -q ck8s-controller; then
  echo "controller already exists"
else
  juju bootstrap ck8s ck8s-controller
fi
juju add-model standup 2>/dev/null || juju switch standup || true

say "6/6 versions + status"
echo "rockcraft:  $(rockcraft version 2>/dev/null)"
echo "charmcraft: $(charmcraft version 2>/dev/null)"
echo "skopeo:     $(skopeo --version 2>/dev/null)"
echo "k8s:        $(sudo k8s version 2>/dev/null | head -1)"
juju status 2>/dev/null || true
echo "groups now: $(id -nG "$USER")"
say "DONE — setup script finished"
