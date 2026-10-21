# The state bucket is created once with the CLI (see README); it is not managed here.
terraform {
  backend "s3" {
    bucket       = "berlin-temp-tfstate-<suffix>"
    key          = "pipeline/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true # S3-native locking, no DynamoDB table
    encrypt      = true
  }
}
