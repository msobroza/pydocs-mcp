locals {
  # Every API a declared resource calls. Beyond the seven ADR 0026 names:
  # cloudresourcemanager (the project lookup and project IAM), monitoring (the
  # budget's email notification channels), logging (log writing) and iap (SSH
  # through `gcloud compute ssh --tunnel-through-iap`).
  enabled_services = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ])
}

resource "google_project_service" "enabled" {
  for_each = local.enabled_services
  service  = each.value
  # Disabling an API on destroy would break anything else in the project using it.
  disable_on_destroy = false
}

# The budget filter needs the project NUMBER. Deferred until the APIs are on,
# so a fresh project's first apply does not read it before Resource Manager is.
data "google_project" "bench" {
  project_id = var.project_id
  depends_on = [google_project_service.enabled]
}
