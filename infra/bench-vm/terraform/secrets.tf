# Provider keys a run's kind declares (ADR 0026, Decision 7). Only the secrets
# are declared here: their values are added out of band, e.g.
#   gcloud secrets versions add bench-anthropic-api-key --data-file=-
# so no secret value ever enters Terraform state. Deliberately no
# google_secret_manager_secret_version resource.

locals {
  # Keyed by provider; the values are the Secret Manager secret ids.
  bench_secret_ids = {
    anthropic  = "bench-anthropic-api-key"  # agent track (metered API-key billing, ADR 0015)
    openrouter = "bench-openrouter-api-key" # before/after (the shipped serving and chat configs)
  }
}

resource "google_secret_manager_secret" "bench" {
  for_each  = local.bench_secret_ids
  secret_id = each.value

  # Keep the secret material in the region, like every other artifact.
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.enabled]
}
