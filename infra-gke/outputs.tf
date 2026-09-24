output "project_id" {
  description = "Google Cloud project that owns the infrastructure."
  value       = var.project_id
}

output "cluster_name" {
  description = "GKE cluster name."
  value       = google_container_cluster.ocr.name
}

output "zone" {
  description = "Zonal cluster location."
  value       = google_container_cluster.ocr.location
}

output "artifact_registry" {
  description = "Docker image prefix for this deployment."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.ocr.repository_id}"
}

output "get_credentials_command" {
  description = "Command that configures kubectl for this cluster."
  value       = "gcloud container clusters get-credentials ${google_container_cluster.ocr.name} --zone ${google_container_cluster.ocr.location} --project ${var.project_id}"
}
