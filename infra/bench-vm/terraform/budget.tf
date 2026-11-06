# The monthly budget alert (ADR 0026, Decision 6). It only notifies: no Pub/Sub
# topic, no function, nothing that stops a VM. Billing data lags by hours, so
# the per-run caps (self-delete, max run duration, spot) bound a single run.

locals {
  # Fractions of the budget; actual spend alerts at each, forecast at the last.
  budget_actual_spend_thresholds   = [0.5, 0.9, 1.0]
  budget_forecast_spend_thresholds = [1.0]
}

resource "google_monitoring_notification_channel" "budget_email" {
  for_each     = toset(var.notification_emails)
  display_name = "bench-vm budget: ${each.value}"
  type         = "email"
  labels = {
    email_address = each.value
  }
  depends_on = [google_project_service.enabled]
}

resource "google_billing_budget" "bench" {
  billing_account = var.billing_account
  display_name    = "bench-vm ${var.project_id}"

  budget_filter {
    projects = ["projects/${data.google_project.bench.number}"]
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = local.budget_actual_spend_thresholds
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  dynamic "threshold_rules" {
    for_each = local.budget_forecast_spend_thresholds
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "FORECASTED_SPEND"
    }
  }

  all_updates_rule {
    monitoring_notification_channels = [for channel in google_monitoring_notification_channel.budget_email : channel.id]
    # Billing account admins are emailed too, as a second path.
    disable_default_iam_recipients = false
  }

  depends_on = [google_project_service.enabled]
}
