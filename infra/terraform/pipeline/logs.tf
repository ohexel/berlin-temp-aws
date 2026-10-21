# Log groups are created here, before the functions, so that they always have a retention.
locals {
  lambda_functions = ["checker", "discover", "finalize", "ingest-lead", "reference-build"]
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = toset(local.lambda_functions)

  name              = "/aws/lambda/berlin-temp-${each.key}"
  retention_in_days = 14

  tags = { component = each.key }
}
