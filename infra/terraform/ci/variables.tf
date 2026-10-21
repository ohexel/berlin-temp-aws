variable "region" {
  description = "AWS region."
  type        = string
  default     = "eu-central-1"
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose workflows may assume the CI roles."
  type        = string
  default     = "<old-owner>/berlin-temp-aws"
}

variable "ecr_repositories" {
  description = "ECR repositories the image workflow may push to."
  type        = list(string)
  default     = ["berlin-temp-pipeline", "berlin-temp-reference"]
}
