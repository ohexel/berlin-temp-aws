variable "region" {
  description = "AWS region."
  type        = string
  default     = "eu-central-1"
}

variable "github_subject_repository" {
  description = <<-EOT
    Repository as it appears in the `sub` claim of GitHub's OIDC tokens: owner and repository
    name, each followed by its immutable numeric ID (owner@ID/name@ID). The workflows may
    assume the CI roles only from this repository.
  EOT
  type        = string
  default     = "<owner>@<owner-id>/berlin-temp-aws@<repo-id>"
}

variable "ecr_repositories" {
  description = "ECR repositories the image workflow may push to."
  type        = list(string)
  default     = ["berlin-temp-pipeline", "berlin-temp-reference"]
}
