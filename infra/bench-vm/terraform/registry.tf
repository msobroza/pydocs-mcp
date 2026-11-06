# bench-cpu:<sha> and bench-cuda:<sha>, one tag per commit (ADR 0026,
# Decision 4). Immutable tags make two launches racing on one commit safe: the
# losing push is refused and the launcher treats the tag's existence as success.

locals {
  seconds_per_day = 86400
}

resource "google_artifact_registry_repository" "bench" {
  repository_id = "bench"
  location      = var.region
  format        = "DOCKER"
  description   = "Bench VM images: bench-cpu and bench-cuda, tagged with the commit SHA."

  docker_config {
    immutable_tags = true
  }

  # Images are rebuilt on demand, so old ones are deleted; KEEP wins over
  # DELETE, so the most recent versions of each image survive an idle month.
  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "delete-old-images"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "${var.image_retention_days * local.seconds_per_day}s"
    }
  }

  cleanup_policies {
    id     = "keep-most-recent-images"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.images_kept_per_flavour
    }
  }

  depends_on = [google_project_service.enabled]
}
