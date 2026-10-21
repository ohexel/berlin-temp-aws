# Settings are the provisional values from the orchestration plan (decision D9: revisit after the run).
# The handler is selected per function with the image command override (see Dockerfile).
locals {
  pipeline_image  = "${aws_ecr_repository.images["berlin-temp-pipeline"].repository_url}:${var.pipeline_image_tag}"
  reference_image = "${aws_ecr_repository.images["berlin-temp-reference"].repository_url}:${var.reference_image_tag}"

  lambdas = {
    "discover" = {
      image   = local.pipeline_image
      handler = "berlin_temp.pipeline.discover_handler.handler"
      timeout = 60
      memory  = 512
      disk    = 512
      env = {
        STATE_TABLE = aws_dynamodb_table.state.name
        QUEUE_URL   = aws_sqs_queue.main["ingest"].url
      }
    }
    "ingest-lead" = {
      image   = local.pipeline_image
      handler = "berlin_temp.pipeline.handler.handler"
      timeout = 120
      memory  = 1024
      disk    = 512
      env = {
        DATA_BUCKET = aws_s3_bucket.data.bucket
        STATE_TABLE = aws_dynamodb_table.state.name
      }
    }
    "checker" = {
      image   = local.pipeline_image
      handler = "berlin_temp.pipeline.checker_handler.handler"
      timeout = 60
      memory  = 512
      disk    = 512
      env = {
        STATE_TABLE        = aws_dynamodb_table.state.name
        FINALIZE_QUEUE_URL = aws_sqs_queue.main["finalize"].url
      }
    }
    "finalize" = {
      image   = local.pipeline_image
      handler = "berlin_temp.pipeline.finalize_handler.handler"
      timeout = 300
      memory  = 1024
      disk    = 512
      env = {
        DATA_BUCKET      = aws_s3_bucket.data.bucket
        DASHBOARD_BUCKET = var.dashboard_bucket_name
        STATE_TABLE      = aws_dynamodb_table.state.name
      }
    }
    "reference-build" = {
      image   = local.reference_image
      handler = "berlin_temp.reference.handler.handler"
      timeout = 900
      memory  = 3008
      disk    = 2048
      env = {
        DATA_BUCKET = aws_s3_bucket.data.bucket
      }
    }
  }
}

resource "aws_lambda_function" "function" {
  for_each = local.lambdas

  function_name = "berlin-temp-${each.key}"
  role          = aws_iam_role.function[each.key].arn
  package_type  = "Image"
  image_uri     = each.value.image
  architectures = ["x86_64"]
  timeout       = each.value.timeout
  memory_size   = each.value.memory

  image_config {
    command = [each.value.handler]
  }

  ephemeral_storage {
    size = each.value.disk
  }

  environment {
    variables = each.value.env
  }

  # The log groups (with retention) must exist before the first invocation.
  depends_on = [aws_cloudwatch_log_group.lambda, aws_iam_role_policy.function]

  tags = { component = each.key }
}
