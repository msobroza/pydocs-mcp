# Boot images (ADR 0026, Decision 8). The FAMILY is pinned in code
# (variables.tf); the concrete image is resolved at apply and frozen in state,
# so every launch between two applies boots the same image, and a re-apply is
# the deliberate step that moves it forward.
#
# GPU: the pinned Deep Learning VM family ships the NVIDIA driver (580, which
# supports CUDA 13) and the container toolkit, so no driver install runs at
# boot. If it stops qualifying, the fallback is a custom image baked once from
# such a base, pinned here the same way, with an amendment to ADR 0026.

data "google_compute_image" "cpu_boot" {
  project = var.cpu_boot_image.project
  family  = var.cpu_boot_image.family
}

data "google_compute_image" "gpu_boot" {
  project = var.gpu_boot_image.project
  family  = var.gpu_boot_image.family
}
