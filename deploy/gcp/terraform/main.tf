# Minimal GCP footprint for the trading backend (Compute Engine + firewall + optional GCS).
#
# Usage:
#   cd deploy/gcp/terraform
#   cp terraform.tfvars.example terraform.tfvars   # edit project, zone, allowed CIDRs
#   terraform init && terraform apply
#
# The VM still needs: clone repo, .env, docker compose, and optionally nginx TLS.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_compute_network" "vpc" {
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.name_prefix}-subnet"
  ip_cidr_range = var.subnet_cidr
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_firewall" "ssh" {
  name    = "${var.name_prefix}-allow-ssh"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.admin_cidr_blocks
  target_tags   = ["algo-trading"]
}

resource "google_compute_firewall" "https" {
  count   = var.expose_https ? 1 : 0
  name    = "${var.name_prefix}-allow-https"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  source_ranges = var.api_allowed_cidr_blocks
  target_tags   = ["algo-trading"]
}

resource "google_storage_bucket" "runs" {
  count                       = var.create_runs_bucket ? 1 : 0
  name                        = "${var.project_id}-${var.name_prefix}-runs"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = var.runs_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_service_account" "vm" {
  account_id   = "${var.name_prefix}-vm"
  display_name = "Algo trading engine VM"
}

resource "google_project_iam_member" "vm_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_project_iam_member" "vm_secret_accessor" {
  count   = var.grant_secret_accessor ? 1 : 0
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_compute_instance" "engine" {
  name         = "${var.name_prefix}-engine"
  machine_type = var.machine_type
  zone         = var.zone
  tags         = ["algo-trading"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.boot_disk_gb
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "algo-data"
  }

  network_interface {
    network    = google_compute_network.vpc.name
    subnetwork = google_compute_subnetwork.subnet.name
    access_config {}
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = templatefile("${path.module}/startup.sh.tpl", {
    install_dir = var.install_dir
    data_mount  = "/mnt/disks/algo-data"
  })

  allow_stopping_for_update = true
}

resource "google_compute_disk" "data" {
  name = "${var.name_prefix}-data"
  type = "pd-balanced"
  zone = var.zone
  size = var.data_disk_gb
}
