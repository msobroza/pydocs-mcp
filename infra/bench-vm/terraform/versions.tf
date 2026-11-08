# The bench VM's cloud project, declared in one Terraform root (ADR 0026).
# Applied once by the owner with ADC (`gcloud auth application-default
# login`); no service-account key is created, downloaded or referenced.

terraform {
  # 1.9+: a variable's validation may reference other variables (zones vs region).
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.10"
    }
  }

  # Partial configuration: the state bucket is created once by the documented
  # bootstrap command and passed at init time:
  #   terraform init -backend-config="bucket=<state bucket>"
  # Checking the code needs no bucket: `terraform init -backend=false`.
  backend "gcs" {
    prefix = "bench-vm"
  }
}

# user_project_override + billing_project: the Billing Budgets API refuses
# end-user (ADC) credentials unless each request names a quota project.
provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}
