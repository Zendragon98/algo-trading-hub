output "engine_instance_name" {
  value = google_compute_instance.engine.name
}

output "engine_external_ip" {
  value = google_compute_instance.engine.network_interface[0].access_config[0].nat_ip
}

output "runs_bucket" {
  value = var.create_runs_bucket ? google_storage_bucket.runs[0].url : null
}

output "vm_service_account" {
  value = google_service_account.vm.email
}
