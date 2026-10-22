# The state bucket name is account-specific and passed at init time:
#   terraform init -backend-config=../backend.hcl   (see ../backend.hcl.example)
# Separate state from the pipeline module, so the pipeline can be destroyed on its own.
terraform {
  backend "s3" {
    key          = "dashboard/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
    encrypt      = true
  }
}
