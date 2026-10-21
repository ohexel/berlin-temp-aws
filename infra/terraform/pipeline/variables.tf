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
