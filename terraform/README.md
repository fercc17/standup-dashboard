# Terraform deploy (Juju provider)

Deploys the whole stack — the `standup-dashboard` charm, `postgresql-k8s`,
`traefik-k8s`, their integrations, the credentials secret, and (optional) proxy
config — onto an existing Juju controller using the
[`juju/juju`](https://registry.terraform.io/providers/juju/juju/latest) provider.

## Prerequisites

- A bootstrapped Juju controller on Canonical Kubernetes (`scripts/charm_setup.sh`).
- The charm packed: `charmcraft pack` (repo root) → `standup-dashboard_amd64.charm`.
- The rock image reachable by the cluster: either pushed to a registry, or
  imported into the cluster's containerd (see `../CHARM.md`).

## Usage

```sh
juju switch ck8s-controller          # provider uses the active controller
cd terraform
cp terraform.tfvars.example terraform.tfvars   # then fill in tokens + app_image
terraform init
terraform apply
```

`terraform apply` creates the model, deploys all three apps, wires the
PostgreSQL and ingress integrations, creates + grants the credentials secret,
and applies config. Watch it settle with `juju status --watch 5s`; get the URL
with `juju run traefik-k8s/0 show-proxied-endpoints`.

## Notes

- The provider reads your local Juju client config (`~/.local/share/juju`); no
  credentials live in Terraform state beyond the secret values you supply.
- `terraform.tfvars` (your tokens) and `.terraform/` are gitignored.
- Outbound proxy: set `http_proxy` / `https_proxy` / `no_proxy` — they reach the
  app as `APP_*` config and are mapped onto the standard env vars httpx honours.
- To tear down: `terraform destroy`.
