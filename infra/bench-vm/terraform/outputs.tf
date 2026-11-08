# Read by the launcher through `terraform output -json`, the single source of
# these names. Renaming an output is a change to the launcher too.

output "project_id" {
  description = "Project that hosts the bench VMs."
  value       = var.project_id
}

output "region" {
  description = "Region of every bench resource."
  value       = var.region
}

output "zones" {
  description = "Zones the launcher tries in order on a capacity error."
  value       = var.zones
}

output "runs_bucket" {
  description = "Bucket holding gs://<bucket>/<run-id>/ for every run."
  value       = google_storage_bucket.runs.name
}

output "cache_bucket" {
  description = "Bucket holding the reusable caches, partitioned by dataset."
  value       = google_storage_bucket.cache.name
}

output "build_sources_bucket" {
  description = "Bucket Cloud Build stages a run commit's `git archive` in."
  value       = google_storage_bucket.build_sources.name
}

output "image_repository" {
  description = "Docker path prefix of bench-cpu:<sha> and bench-cuda:<sha>."
  value       = "${google_artifact_registry_repository.bench.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.bench.repository_id}"
}

output "bench_service_account" {
  description = "Email of the service account attached to every bench VM."
  value       = google_service_account.bench.email
}

output "build_service_account" {
  description = "Full resource name of the Cloud Build account, as `gcloud builds submit --service-account` takes it."
  value       = google_service_account.build.id
}

output "subnetwork" {
  description = "Self link of the subnet bench VMs attach to (no external IP)."
  value       = google_compute_subnetwork.bench.self_link
}

output "cpu_boot_image" {
  description = "Self link of the Container-Optimized OS image CPU VMs boot."
  value       = data.google_compute_image.cpu_boot.self_link
}

output "gpu_boot_image" {
  description = "Self link of the Deep Learning VM image GPU VMs boot."
  value       = data.google_compute_image.gpu_boot.self_link
}

output "instance_name_prefix" {
  description = "Prefix every bench VM name starts with; the self-delete grant admits only it."
  value       = local.bench_instance_name_prefix
}

output "secret_ids" {
  description = "Secret Manager secret ids by provider; their values are added out of band."
  value       = { for provider, secret in google_secret_manager_secret.bench : provider => secret.secret_id }
}
