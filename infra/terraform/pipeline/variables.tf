variable "region" {
  description = "AWS region for all resources."
  type        = string
  default     = "eu-central-1"
}

variable "alert_email" {
  description = "Address that receives the alarm e-mails. Set in terraform.tfvars (git-ignored)."
  type        = string
  sensitive   = true
}

variable "dashboard_bucket_name" {
  description = "Existing dashboard bucket (managed by the dashboard module after the import)."
  type        = string
  default     = "berlin-temp-dashboard-<suffix>"
}

variable "pipeline_image_tag" {
  description = "Git SHA tag of the berlin-temp-pipeline image (discover, ingest-lead, checker, finalize)."
  type        = string
}

variable "reference_image_tag" {
  description = "Git SHA tag of the berlin-temp-reference image (reference-build)."
  type        = string
}

variable "schedules_enabled" {
  description = "ENABLED/DISABLED state of both schedules. Keep false except during tests and the continuous run."
  type        = bool
  default     = false
}

variable "no_publication_alarm_enabled" {
  description = "Whether the no-publication alarm sends notifications. Keep false except during the continuous run."
  type        = bool
  default     = false
}
