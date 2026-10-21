# Own state: the CI roles must survive the teardown of the pipeline module.
terraform {
  backend "s3" {
    bucket       = "berlin-temp-tfstate-<suffix>"
    key          = "ci/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
    encrypt      = true
  }
}
