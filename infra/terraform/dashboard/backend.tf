# Separate state from the pipeline module, so the pipeline can be destroyed on its own.
terraform {
  backend "s3" {
    bucket       = "berlin-temp-tfstate-<suffix>"
    key          = "dashboard/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
    encrypt      = true
  }
}
