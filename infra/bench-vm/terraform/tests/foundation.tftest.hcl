# Credential-free checks of the foundation: `terraform test` with a mock
# google provider plans and "applies" against fakes only. Nothing reaches GCP.
#   terraform init -backend=false && terraform test

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }
  mock_data "google_compute_image" {
    defaults = {
      self_link = "https://www.googleapis.com/compute/v1/projects/mock/global/images/mock-image"
    }
  }
  mock_resource "google_service_account" {
    defaults = {
      email  = "bench-vm@my-bench-project.iam.gserviceaccount.com"
      member = "serviceAccount:bench-vm@my-bench-project.iam.gserviceaccount.com"
    }
  }
}

variables {
  project_id          = "my-bench-project"
  billing_account     = "012345-6789AB-CDEF01"
  notification_emails = ["me@example.com", "ops@example.com"]
}

run "defaults_match_the_spec" {
  command = apply

  assert {
    condition     = var.region == "europe-west4" && var.budget_amount == 50 && var.budget_currency == "EUR"
    error_message = "region, budget amount and currency must default to europe-west4, 50 and EUR."
  }

  assert {
    condition     = google_compute_subnetwork.bench.region == "europe-west4" && google_compute_subnetwork.bench.private_ip_google_access
    error_message = "the subnet must be in the region with Private Google Access."
  }

  assert {
    condition     = google_compute_router_nat.bench.region == "europe-west4"
    error_message = "Cloud NAT must give the region's subnet egress."
  }

  assert {
    condition     = google_compute_firewall.bench_iap_ssh.source_ranges == toset(["35.235.240.0/20"])
    error_message = "SSH must be admitted only from IAP's range."
  }

  assert {
    condition = alltrue([
      for bucket in [google_storage_bucket.runs, google_storage_bucket.cache, google_storage_bucket.build_sources] :
      bucket.location == "europe-west4" && bucket.uniform_bucket_level_access && bucket.public_access_prevention == "enforced"
    ])
    error_message = "every bucket must be regional, with uniform access and public access prevention."
  }

  assert {
    condition     = length(google_storage_bucket.runs.lifecycle_rule) == 0
    error_message = "the runs bucket keeps results: no lifecycle rule."
  }

  assert {
    condition     = one(google_storage_bucket.cache.lifecycle_rule[0].condition).age == 60 && one(google_storage_bucket.cache.lifecycle_rule[0].action).type == "Delete"
    error_message = "the cache bucket must delete objects by age."
  }

  assert {
    condition     = google_artifact_registry_repository.bench.docker_config[0].immutable_tags && google_artifact_registry_repository.bench.location == "europe-west4"
    error_message = "the image repository must be regional with immutable tags."
  }

  assert {
    condition     = contains([for policy in google_artifact_registry_repository.bench.cleanup_policies : policy.action], "DELETE")
    error_message = "the image repository must carry a cleanup policy that deletes old images."
  }

  assert {
    condition     = toset(keys(google_secret_manager_secret.bench)) == toset(["anthropic", "openrouter"])
    error_message = "the Anthropic and OpenRouter secrets must be declared."
  }

  assert {
    condition     = output.instance_name_prefix == "bench-" && strcontains(google_project_iam_member.bench_self_delete.condition[0].expression, "startsWith(\"bench-\")")
    error_message = "instance delete must be conditioned on the bench- name prefix."
  }

  assert {
    condition     = output.image_repository == "europe-west4-docker.pkg.dev/my-bench-project/bench"
    error_message = "the repository path must be <region>-docker.pkg.dev/<project>/bench."
  }

  assert {
    condition     = output.zones == tolist(["europe-west4-a", "europe-west4-b", "europe-west4-c"])
    error_message = "the zones output must default to the region's three zones."
  }
}

run "budget_alerts_at_50_90_100_actual_and_100_forecast" {
  command = apply

  assert {
    condition     = google_billing_budget.bench.amount[0].specified_amount[0].units == "50" && google_billing_budget.bench.amount[0].specified_amount[0].currency_code == "EUR"
    error_message = "the budget must be 50 EUR."
  }

  assert {
    condition = [for rule in google_billing_budget.bench.threshold_rules : "${rule.spend_basis}:${rule.threshold_percent}"] == [
      "CURRENT_SPEND:0.5", "CURRENT_SPEND:0.9", "CURRENT_SPEND:1", "FORECASTED_SPEND:1",
    ]
    error_message = "the budget must alert at 50/90/100% of actual spend and 100% of forecast."
  }

  assert {
    condition     = google_billing_budget.bench.budget_filter[0].projects == toset(["projects/123456789012"])
    error_message = "the budget must watch the bench project only."
  }

  assert {
    condition     = length(google_billing_budget.bench.all_updates_rule[0].monitoring_notification_channels) == 2 && google_billing_budget.bench.all_updates_rule[0].pubsub_topic == null
    error_message = "the budget must email every notification address and publish nowhere that could act on it."
  }
}

run "rejects_zones_outside_the_region" {
  command = plan

  variables {
    zones = ["europe-west4-a", "us-central1-a"]
  }

  expect_failures = [var.zones]
}

run "rejects_a_fractional_budget" {
  command = plan

  variables {
    budget_amount = 49.5
  }

  expect_failures = [var.budget_amount]
}

run "rejects_no_notification_address" {
  command = plan

  variables {
    notification_emails = []
  }

  expect_failures = [var.notification_emails]
}

run "rejects_a_malformed_billing_account" {
  command = plan

  variables {
    billing_account = "not-a-billing-account"
  }

  expect_failures = [var.billing_account]
}
