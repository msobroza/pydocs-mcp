# A bench VM has no external IP: it reaches the internet (image pulls, dataset
# downloads, model providers) through Cloud NAT, Google APIs through Private
# Google Access, and accepts SSH only through IAP.

locals {
  # IAP's TCP-forwarding source range (Google-documented, fixed).
  iap_tcp_forwarding_range = "35.235.240.0/20"
}

resource "google_compute_network" "bench" {
  name                    = "bench-vm"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "bench" {
  name                     = "bench-vm-${var.region}"
  network                  = google_compute_network.bench.id
  region                   = var.region
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_router" "bench" {
  name    = "bench-vm-router"
  network = google_compute_network.bench.id
  region  = var.region
}

resource "google_compute_router_nat" "bench" {
  name                               = "bench-vm-nat"
  router                             = google_compute_router.bench.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.bench.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Targets the bench service account rather than a network tag, so the
# launcher has no tag to keep in sync: every VM running as it is covered.
resource "google_compute_firewall" "bench_iap_ssh" {
  name                    = "bench-vm-allow-ssh-from-iap"
  network                 = google_compute_network.bench.id
  direction               = "INGRESS"
  source_ranges           = [local.iap_tcp_forwarding_range]
  target_service_accounts = [google_service_account.bench.email]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
