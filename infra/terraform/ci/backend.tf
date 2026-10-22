# The state bucket name is account-specific and passed at init time:
#   terraform init -backend-config=../backend.hcl   (see ../backend.hcl.example)
# Own state: the CI roles must survive the teardown of the pipeline module.
terraform {
  backend "s3" {
    key          = "ci/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
    encrypt      = true
  }
}
