# Artifacts live only in these private buckets in the region (ADR 0026,
# Decision 2). Uniform access + enforced public access prevention on all.

# Runs: gs://<runs>/<run-id>/{spec, run.json, state/}. Results are kept, so no
# lifecycle rule; a non-empty bucket also refuses `terraform destroy`.
resource "google_storage_bucket" "runs" {
  name                        = "${var.project_id}-bench-runs"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  depends_on                  = [google_project_service.enabled]
}

# Cache: reusable index entries, downloads, embedder weights, judge cache.
# Entries are uploaded as one archive object each and never overwritten, so the
# age rule deletes whole entries; a deleted entry is rebuilt on demand.
resource "google_storage_bucket" "cache" {
  name                        = "${var.project_id}-bench-cache"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = var.cache_retention_days
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.enabled]
}

# Cloud Build's staged sources (a `git archive` of the run commit). A build
# with a user-specified service account reads its source from a bucket that
# account can read; the default `<project>_cloudbuild` bucket is multi-region.
locals {
  build_source_retention_days = 7
}

resource "google_storage_bucket" "build_sources" {
  name                        = "${var.project_id}-bench-build-sources"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = local.build_source_retention_days
    }
    action {
      type = "Delete"
    }
  }
  depends_on = [google_project_service.enabled]
}
