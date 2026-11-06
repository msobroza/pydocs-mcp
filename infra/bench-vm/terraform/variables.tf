variable "project_id" {
  description = "GCP project that hosts the bench VMs (a dedicated project is recommended)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a GCP project id (6-30 chars of [a-z0-9-], starting with a letter), got ${jsonencode(var.project_id)}."
  }
}

variable "billing_account" {
  description = "Billing account id (XXXXXX-XXXXXX-XXXXXX) the budget watches."
  type        = string

  validation {
    condition     = can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account))
    error_message = "billing_account must look like 012345-6789AB-CDEF01, got ${jsonencode(var.billing_account)}."
  }
}

variable "region" {
  description = "Region of the subnet, buckets, image repository, secrets, Cloud Build and the VMs."
  type        = string
  default     = "europe-west4"
}

variable "zones" {
  description = "Zones the launcher tries in order on a capacity error; each offers NVIDIA L4 GPUs."
  type        = list(string)
  default     = ["europe-west4-a", "europe-west4-b", "europe-west4-c"]

  validation {
    condition     = length(var.zones) > 0 && alltrue([for zone in var.zones : startswith(zone, "${var.region}-")])
    error_message = "zones must be a non-empty list of zones in ${var.region} (<region>-<letter>), got ${jsonencode(var.zones)}."
  }
}

variable "budget_amount" {
  description = "Monthly budget, in budget_currency, that the alerts fire against."
  type        = number
  default     = 50

  validation {
    condition     = var.budget_amount > 0 && floor(var.budget_amount) == var.budget_amount
    error_message = "budget_amount must be a positive whole number, got ${var.budget_amount}."
  }
}

variable "budget_currency" {
  description = "Currency of the budget; it must be the billing account's own currency."
  type        = string
  default     = "EUR"

  validation {
    condition     = can(regex("^[A-Z]{3}$", var.budget_currency))
    error_message = "budget_currency must be an ISO 4217 code such as EUR, got ${jsonencode(var.budget_currency)}."
  }
}

variable "notification_emails" {
  description = "Addresses the budget alerts email (1 to 5: a budget takes at most five channels)."
  type        = list(string)

  validation {
    condition     = length(var.notification_emails) >= 1 && length(var.notification_emails) <= 5
    error_message = "notification_emails must hold 1 to 5 addresses, got ${length(var.notification_emails)}."
  }

  validation {
    condition     = alltrue([for email in var.notification_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", email))])
    error_message = "notification_emails must be email addresses (name@domain.tld), got ${jsonencode(var.notification_emails)}."
  }
}

variable "subnet_cidr" {
  description = "Primary IPv4 range of the bench subnet."
  type        = string
  default     = "10.42.0.0/24"

  validation {
    condition     = can(cidrnetmask(var.subnet_cidr))
    error_message = "subnet_cidr must be an IPv4 CIDR block such as 10.42.0.0/24, got ${jsonencode(var.subnet_cidr)}."
  }
}

variable "cache_retention_days" {
  description = "Cache objects older than this are deleted; every cache entry is rebuilt on demand."
  type        = number
  default     = 60

  validation {
    condition     = var.cache_retention_days >= 1 && floor(var.cache_retention_days) == var.cache_retention_days
    error_message = "cache_retention_days must be a whole number of days >= 1, got ${var.cache_retention_days}."
  }
}

variable "image_retention_days" {
  description = "Image versions older than this are deleted (beyond the kept most recent); images are rebuilt on demand."
  type        = number
  default     = 30

  validation {
    condition     = var.image_retention_days >= 1 && floor(var.image_retention_days) == var.image_retention_days
    error_message = "image_retention_days must be a whole number of days >= 1, got ${var.image_retention_days}."
  }
}

variable "images_kept_per_flavour" {
  description = "Most recent versions of each image (bench-cpu, bench-cuda) the cleanup policy never deletes."
  type        = number
  default     = 3

  validation {
    condition     = var.images_kept_per_flavour >= 1 && floor(var.images_kept_per_flavour) == var.images_kept_per_flavour
    error_message = "images_kept_per_flavour must be a whole number >= 1, got ${var.images_kept_per_flavour}."
  }
}

variable "cpu_boot_image" {
  description = "Image family CPU VMs boot: Container-Optimized OS, which ships Docker."
  type        = object({ project = string, family = string })
  default     = { project = "cos-cloud", family = "cos-stable" }
}

variable "gpu_boot_image" {
  description = <<-EOT
    Image family GPU VMs boot: a public Deep Learning VM family with the NVIDIA
    driver and container toolkit preinstalled. Driver 580 supports CUDA 13, the
    CUDA major of the lockfile's Linux torch build (nvidia-cuda-runtime 13.0).
  EOT
  type        = object({ project = string, family = string })
  default     = { project = "deeplearning-platform-release", family = "common-cu129-ubuntu-2404-nvidia-580" }
}
