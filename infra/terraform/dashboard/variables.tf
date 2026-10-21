variable "region" {
  description = "AWS region of the dashboard bucket."
  type        = string
  default     = "eu-central-1"
}

variable "bucket_name" {
  description = "Existing dashboard bucket."
  type        = string
  default     = "berlin-temp-dashboard-<suffix>"
}
