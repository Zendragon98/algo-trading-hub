variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "name_prefix" {
  type    = string
  default = "algo-trading"
}

variable "machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "boot_disk_gb" {
  type    = number
  default = 30
}

variable "data_disk_gb" {
  type    = number
  default = 100
}

variable "subnet_cidr" {
  type    = string
  default = "10.20.0.0/24"
}

variable "install_dir" {
  type    = string
  default = "/opt/algo-trading-hub"
}

variable "admin_cidr_blocks" {
  type        = list(string)
  description = "CIDRs allowed to SSH (use IAP tunnel CIDR 35.235.240.0/20 if using OS Login + IAP)"
  default     = ["35.235.240.0/20"]
}

variable "api_allowed_cidr_blocks" {
  type        = list(string)
  description = "CIDRs allowed to reach HTTPS on the VM (office/VPN only recommended)"
  default     = []
}

variable "expose_https" {
  type    = bool
  default = false
}

variable "create_runs_bucket" {
  type    = bool
  default = true
}

variable "runs_retention_days" {
  type    = number
  default = 90
}

variable "grant_secret_accessor" {
  type    = bool
  default = true
}
