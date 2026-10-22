# The state bucket name is account-specific and passed at init time:
#   terraform init -backend-config=../backend.hcl   (see ../backend.hcl.example)
# The state bucket is created once with the CLI (see README); it is not managed here.
terraform {
  backend "s3" {
    key          = "pipeline/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true # S3-native locking, no DynamoDB table
    encrypt      = true
  }
}
