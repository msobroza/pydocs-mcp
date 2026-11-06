# Two service accounts, no keys (ADR 0026, Decision 7): the bench account is
# attached to every bench VM and gets credentials from the metadata server
# only; the build account is the one Cloud Build pushes images as. Nothing here
# creates, outputs or references a service-account key.

locals {
  # Every bench VM is named bench-<run-id>-<attempt>; its boot disk takes the
  # instance's name. The self-delete grant is scoped to this prefix.
  bench_instance_name_prefix = "bench-"
}

resource "google_service_account" "bench" {
  account_id   = "bench-vm"
  display_name = "Bench VM (attached to every ephemeral bench VM)"
  depends_on   = [google_project_service.enabled]
}

resource "google_service_account" "build" {
  account_id   = "bench-build"
  display_name = "Bench image build (Cloud Build)"
  depends_on   = [google_project_service.enabled]
}

# ── Bench account: only what a run needs ───────────────────────────────────

# Object read and write on the two buckets (run state syncs, cache restores and
# pushes, generation-preconditioned run-record writes).
resource "google_storage_bucket_iam_member" "bench_objects" {
  for_each = {
    runs  = google_storage_bucket.runs.name
    cache = google_storage_bucket.cache.name
  }
  bucket = each.value
  role   = "roles/storage.objectUser"
  member = google_service_account.bench.member
}

resource "google_artifact_registry_repository_iam_member" "bench_pull" {
  location   = google_artifact_registry_repository.bench.location
  repository = google_artifact_registry_repository.bench.name
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.bench.member
}

resource "google_secret_manager_secret_iam_member" "bench_secret_access" {
  for_each  = google_secret_manager_secret.bench
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.bench.member
}

resource "google_project_iam_member" "bench_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.bench.member
}

# Self-delete after succeeded / failed / timed_out. A custom role, granted under
# an IAM condition that admits only instances (and their boot disks, deleted
# with them) whose name starts with the bench prefix — nothing else in the
# project. instances.get lets the supervisor confirm what it is deleting.
resource "google_project_iam_custom_role" "bench_self_delete" {
  role_id     = "benchVmSelfDelete"
  title       = "Bench VM self-delete"
  description = "Delete a bench VM instance and its boot disk; granted only under a name-prefix IAM condition."
  permissions = [
    "compute.disks.delete",
    "compute.instances.delete",
    "compute.instances.get",
  ]
  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "bench_self_delete" {
  project = var.project_id
  role    = google_project_iam_custom_role.bench_self_delete.id
  member  = google_service_account.bench.member

  condition {
    title       = "bench-instances-only"
    description = "Only instances and disks named ${local.bench_instance_name_prefix}*"
    expression  = <<-CEL
      (resource.type == "compute.googleapis.com/Instance" && resource.name.extract("/instances/{name}").startsWith("${local.bench_instance_name_prefix}")) ||
      (resource.type == "compute.googleapis.com/Disk" && resource.name.extract("/disks/{name}").startsWith("${local.bench_instance_name_prefix}"))
    CEL
  }
}

# ── Build account: push images, read staged sources, log ───────────────────

resource "google_artifact_registry_repository_iam_member" "build_push" {
  location   = google_artifact_registry_repository.bench.location
  repository = google_artifact_registry_repository.bench.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.build.member
}

resource "google_storage_bucket_iam_member" "build_read_sources" {
  bucket = google_storage_bucket.build_sources.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.build.member
}

# A build with a user-specified service account must log to Cloud Logging only
# (build option CLOUD_LOGGING_ONLY) or to a bucket it owns; this covers the first.
resource "google_project_iam_member" "build_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.build.member
}
