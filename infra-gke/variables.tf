variable "project_id" {
  description = "Google Cloud project ID that will own the OCR infrastructure."
  type        = string
}

variable "region" {
  description = "GCP region for the subnet, Artifact Registry, and regional GPU quota."
  type        = string
  default     = "europe-west4"
}

variable "zone" {
  description = "Single GCP zone offering both NVIDIA T4 and A100 80 GB GPUs."
  type        = string
  default     = "europe-west4-a"
}

variable "cluster_name" {
  description = "Name of the zonal GKE Standard cluster."
  type        = string
  default     = "gke-ocr-cluster"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,21}[a-z0-9]$", var.cluster_name))
    error_message = "cluster_name must be 2-23 lowercase letters, digits, or hyphens, start with a letter, and end with a letter or digit."
  }
}

variable "artifact_registry_repository" {
  description = "Artifact Registry Docker repository used by the OCR images."
  type        = string
  default     = "ocr-repository"
}

variable "gpu_max_nodes" {
  description = "Maximum nodes in each GPU pool. Keep this aligned with both T4 and A100 regional quota."
  type        = number
  default     = 4

  validation {
    condition     = var.gpu_max_nodes >= 1 && var.gpu_max_nodes <= 10
    error_message = "gpu_max_nodes must be between 1 and 10."
  }
}
