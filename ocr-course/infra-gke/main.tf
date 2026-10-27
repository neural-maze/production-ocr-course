provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

locals {
  required_apis = toset([
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "file.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "serviceusage.googleapis.com",
  ])

  node_roles = toset([
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/stackdriver.resourceMetadata.writer",
  ])
}

# API activation remains in place after destroy because it is project-level state
# and disabling shared APIs can disrupt unrelated resources in the project.
resource "google_project_service" "required" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "ocr" {
  name                    = "${var.cluster_name}-network"
  auto_create_subnetworks = false

  depends_on = [google_project_service.required["compute.googleapis.com"]]
}

resource "google_compute_subnetwork" "ocr" {
  name          = "${var.cluster_name}-subnet"
  region        = var.region
  network       = google_compute_network.ocr.id
  ip_cidr_range = "10.20.0.0/20"

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.24.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.25.0.0/20"
  }
}

resource "google_artifact_registry_repository" "ocr" {
  location      = var.region
  repository_id = var.artifact_registry_repository
  description   = "Container images for the SLM-powered OCR pipeline"
  format        = "DOCKER"

  depends_on = [google_project_service.required["artifactregistry.googleapis.com"]]
}

# Nodes receive only the roles needed for image pulls and operational telemetry.
# Workloads should use Workload Identity for any future access to GCP APIs.
resource "google_service_account" "nodes" {
  account_id   = "${var.cluster_name}-nodes"
  display_name = "GKE node identity for ${var.cluster_name}"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_project_iam_member" "node_roles" {
  for_each = local.node_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.nodes.email}"
}

# A zonal cluster avoids multiplying node counts across three zones and pins all
# scarce GPU pools to a zone verified to offer both T4 and A100 80 GB hardware.
resource "google_container_cluster" "ocr" {
  name     = var.cluster_name
  location = var.zone

  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode = "VPC_NATIVE"
  network         = google_compute_network.ocr.id
  subnetwork      = google_compute_subnetwork.ocr.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  addons_config {
    horizontal_pod_autoscaling {
      disabled = false
    }

    http_load_balancing {
      disabled = false
    }

    # Required by k8s/gke/infra/provisioning/pvc.yaml (standard-rwx).
    gcp_filestore_csi_driver_config {
      enabled = true
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  logging_service       = "logging.googleapis.com/kubernetes"
  monitoring_service    = "monitoring.googleapis.com/kubernetes"
  enable_shielded_nodes = true
  deletion_protection   = false

  lifecycle {
    precondition {
      condition     = startswith(var.zone, "${var.region}-")
      error_message = "zone must belong to region (for example, europe-west4-a belongs to europe-west4)."
    }
  }

  depends_on = [
    google_project_service.required["container.googleapis.com"],
    google_project_service.required["file.googleapis.com"],
    google_compute_subnetwork.ocr,
  ]
}

# Untainted capacity for GKE system pods, KEDA, Prometheus, and Grafana.
resource "google_container_node_pool" "system" {
  name               = "system"
  location           = var.zone
  cluster            = google_container_cluster.ocr.name
  initial_node_count = 1

  autoscaling {
    min_node_count = 1
    max_node_count = 3
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = "e2-standard-4"
    service_account = google_service_account.nodes.email
    image_type      = "COS_CONTAINERD"
    disk_type       = "pd-balanced"
    disk_size_gb    = 100
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = {
      workload = "system"
    }

    shielded_instance_config {
      enable_integrity_monitoring = true
      enable_secure_boot          = true
    }
  }

  depends_on = [google_project_iam_member.node_roles]
}

resource "google_container_node_pool" "redis" {
  name               = "redisnp"
  location           = var.zone
  cluster            = google_container_cluster.ocr.name
  initial_node_count = 1

  autoscaling {
    min_node_count = 1
    max_node_count = 3
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = "n2-highmem-4"
    service_account = google_service_account.nodes.email
    image_type      = "COS_CONTAINERD"
    disk_type       = "pd-balanced"
    disk_size_gb    = 50
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = {
      app = "redis-store"
    }

    taint {
      key    = "sku"
      value  = "redis"
      effect = "NO_SCHEDULE"
    }

    shielded_instance_config {
      enable_integrity_monitoring = true
      enable_secure_boot          = true
    }
  }

  depends_on = [google_project_iam_member.node_roles]
}

resource "google_container_node_pool" "api" {
  name               = "apinp"
  location           = var.zone
  cluster            = google_container_cluster.ocr.name
  initial_node_count = 1

  autoscaling {
    min_node_count = 1
    max_node_count = 5
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = "n2-standard-2"
    service_account = google_service_account.nodes.email
    image_type      = "COS_CONTAINERD"
    disk_type       = "pd-balanced"
    disk_size_gb    = 50
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = {
      app = "api-gateway"
    }

    taint {
      key    = "sku"
      value  = "api"
      effect = "NO_SCHEDULE"
    }

    shielded_instance_config {
      enable_integrity_monitoring = true
      enable_secure_boot          = true
    }
  }

  depends_on = [google_project_iam_member.node_roles]
}

# T4 pool for PP-DocLayoutV3 and the asynchronous OCR worker. It starts at zero;
# a pod requesting nvidia.com/gpu and selecting gpunpt4 triggers scale-up.
resource "google_container_node_pool" "t4" {
  name               = "gpunpt4"
  location           = var.zone
  cluster            = google_container_cluster.ocr.name
  initial_node_count = 0

  autoscaling {
    min_node_count = 0
    max_node_count = var.gpu_max_nodes
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = "n1-standard-4"
    service_account = google_service_account.nodes.email
    image_type      = "COS_CONTAINERD"
    disk_type       = "pd-balanced"
    disk_size_gb    = 100
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    guest_accelerator {
      type  = "nvidia-tesla-t4"
      count = 1

      gpu_driver_installation_config {
        gpu_driver_version = "DEFAULT"
      }
    }

    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }

    shielded_instance_config {
      enable_integrity_monitoring = true
      enable_secure_boot          = true
    }
  }

  depends_on = [google_project_iam_member.node_roles]
}

# A100 80 GB pool for Qwen 3.5 4B served by vLLM. A2 Ultra machine types include
# the accelerator; guest_accelerator also requests GKE's managed NVIDIA driver.
resource "google_container_node_pool" "a100" {
  name               = "gpunpa100"
  location           = var.zone
  cluster            = google_container_cluster.ocr.name
  initial_node_count = 0

  autoscaling {
    min_node_count = 0
    max_node_count = var.gpu_max_nodes
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = "a2-ultragpu-1g"
    service_account = google_service_account.nodes.email
    image_type      = "COS_CONTAINERD"
    disk_type       = "pd-balanced"
    disk_size_gb    = 200
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    guest_accelerator {
      type  = "nvidia-a100-80gb"
      count = 1

      gpu_driver_installation_config {
        gpu_driver_version = "DEFAULT"
      }
    }

    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }

    shielded_instance_config {
      enable_integrity_monitoring = true
      enable_secure_boot          = true
    }
  }

  depends_on = [google_project_iam_member.node_roles]
}
