variable "model_name" {
  description = "Juju model to deploy into."
  type        = string
  default     = "standup"
}

variable "charm_path" {
  description = "Path to the locally-packed charm (charmcraft pack)."
  type        = string
  default     = "../charm/standup-dashboard_amd64.charm"
}

variable "app_image" {
  description = "OCI image ref for the rock (registry path, or a ref already imported into the cluster's containerd)."
  type        = string
}

variable "postgresql_channel" {
  description = "Charmhub channel for postgresql-k8s."
  type        = string
  default     = "14/stable"
}

variable "traefik_channel" {
  description = "Charmhub channel for traefik-k8s."
  type        = string
  default     = "latest/stable"
}

variable "refresh_interval" {
  description = "Seconds between scheduled refreshes."
  type        = number
  default     = 1800
}

variable "window_days" {
  description = "Days a full refresh looks back."
  type        = number
  default     = 7
}

variable "github_org" {
  type    = string
  default = "canonical"
}

variable "jira_base_url" {
  type    = string
  default = "https://warthogs.atlassian.net"
}

variable "jira_account_email" {
  type    = string
  default = "fernando.carrillo.castro@canonical.com"
}

# --- Outbound proxy (optional) ---------------------------------------------
variable "http_proxy" {
  type    = string
  default = ""
}

variable "https_proxy" {
  type    = string
  default = ""
}

variable "no_proxy" {
  type    = string
  default = ""
}

# --- Secrets ----------------------------------------------------------------
variable "jira_token" {
  type      = string
  sensitive = true
}

variable "pagerduty_token" {
  type      = string
  sensitive = true
}

variable "pagerduty_ical_url" {
  type      = string
  sensitive = true
}

variable "github_token" {
  type      = string
  sensitive = true
  default   = ""
}
