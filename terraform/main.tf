terraform {
  required_version = ">= 1.6"
  required_providers {
    juju = {
      source  = "juju/juju"
      version = ">= 0.14.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2"
    }
  }
}

# Uses the active Juju controller from your local config
# (~/.local/share/juju). Run `juju switch ck8s-controller` first.
provider "juju" {}

resource "juju_model" "standup" {
  name = var.model_name
}

# --- PostgreSQL (state) -----------------------------------------------------
resource "juju_application" "postgresql" {
  name       = "postgresql-k8s"
  model_uuid = juju_model.standup.uuid
  trust      = true
  units      = 1
  charm {
    name    = "postgresql-k8s"
    channel = var.postgresql_channel
  }
}

# --- Traefik (ingress) ------------------------------------------------------
resource "juju_application" "traefik" {
  name       = "traefik-k8s"
  model_uuid = juju_model.standup.uuid
  trust      = true
  units      = 1
  charm {
    name    = "traefik-k8s"
    channel = var.traefik_channel
  }
}

# --- API credentials as a Juju secret --------------------------------------
resource "juju_secret" "creds" {
  model_uuid = juju_model.standup.uuid
  name       = "standup-creds"
  value = {
    jira-token         = var.jira_token
    pagerduty-token    = var.pagerduty_token
    pagerduty-ical-url = var.pagerduty_ical_url
    github-token       = var.github_token
  }
}

# --- The dashboard (LOCAL charm) -------------------------------------------
# The Juju Terraform provider can't deploy a local .charm, so drive the juju CLI
# for this one app (deploy + grant secret + integrate). Everything it depends on
# is a native resource above, so ordering and re-creates are still managed by TF.
resource "null_resource" "dashboard" {
  depends_on = [juju_application.postgresql, juju_application.traefik, juju_secret.creds]

  triggers = {
    model            = var.model_name
    charm_path       = var.charm_path
    app_image        = var.app_image
    secret_id        = juju_secret.creds.secret_id
    refresh_interval = var.refresh_interval
    window_days      = var.window_days
    github_org       = var.github_org
    jira_base_url    = var.jira_base_url
    jira_email       = var.jira_account_email
    http_proxy       = var.http_proxy
    https_proxy      = var.https_proxy
    no_proxy         = var.no_proxy
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -euo pipefail
      M=${var.model_name}
      juju deploy ${var.charm_path} standup-dashboard -m "$M" \
        --resource app-image='${var.app_image}' \
        --config secrets='${juju_secret.creds.secret_id}' \
        --config refresh-interval=${var.refresh_interval} \
        --config window-days=${var.window_days} \
        --config github-org='${var.github_org}' \
        --config jira-base-url='${var.jira_base_url}' \
        --config jira-account-email='${var.jira_account_email}' \
        --config http-proxy='${var.http_proxy}' \
        --config https-proxy='${var.https_proxy}' \
        --config no-proxy='${var.no_proxy}'
      juju grant-secret standup-creds standup-dashboard -m "$M"
      juju integrate -m "$M" standup-dashboard postgresql-k8s:database
      juju integrate -m "$M" standup-dashboard traefik-k8s
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "juju remove-application standup-dashboard -m ${self.triggers.model} --destroy-storage --no-prompt || true"
  }
}
