# One role per function, least privilege, derived from the AWS calls in src/berlin_temp.
# The two scheduler roles live in schedules.tf: their policies need the function ARNs.
locals {
  table_arn          = aws_dynamodb_table.state.arn
  data_bucket_arn    = aws_s3_bucket.data.arn
  dashboard_bucket   = "arn:aws:s3:::${var.dashboard_bucket_name}"
  ingest_queue_arn   = aws_sqs_queue.main["ingest"].arn
  finalize_queue_arn = aws_sqs_queue.main["finalize"].arn
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "discover" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda["discover"].arn}:*"]
  }
  statement {
    sid       = "State"
    actions   = ["dynamodb:Query", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [local.table_arn]
  }
  statement {
    sid       = "QueueLeads"
    actions   = ["sqs:SendMessage"]
    resources = [local.ingest_queue_arn]
  }
}

data "aws_iam_policy_document" "ingest_lead" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda["ingest-lead"].arn}:*"]
  }
  statement {
    sid     = "WriteBronzeAndSilver"
    actions = ["s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [
      "${local.data_bucket_arn}/bronze/forecast/*",
      "${local.data_bucket_arn}/silver/forecast_cell/*",
    ]
  }
  statement {
    sid       = "ReadMask"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_bucket_arn}/silver/mask/*"]
  }
  statement {
    sid       = "State"
    actions   = ["dynamodb:UpdateItem"]
    resources = [local.table_arn]
  }
  statement {
    sid       = "ConsumeIngestQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [local.ingest_queue_arn]
  }
}

data "aws_iam_policy_document" "checker" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda["checker"].arn}:*"]
  }
  statement {
    sid       = "State"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [local.table_arn]
  }
  statement {
    sid       = "QueueFinalize"
    actions   = ["sqs:SendMessage"]
    resources = [local.finalize_queue_arn]
  }
}

data "aws_iam_policy_document" "finalize" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda["finalize"].arn}:*"]
  }
  statement {
    sid       = "ReadSilver"
    actions   = ["s3:GetObject"]
    resources = ["${local.data_bucket_arn}/silver/*"]
  }
  statement {
    sid       = "WriteGold"
    actions   = ["s3:PutObject"]
    resources = ["${local.data_bucket_arn}/gold/forecast_plr*"]
  }
  statement {
    sid       = "DashboardData"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${local.dashboard_bucket}/data/*"]
  }
  statement {
    # Listing makes a missing pointer read as "not found" instead of "access denied".
    sid       = "DashboardList"
    actions   = ["s3:ListBucket"]
    resources = [local.dashboard_bucket]
  }
  statement {
    sid       = "State"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [local.table_arn]
  }
  statement {
    sid       = "ConsumeFinalizeQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [local.finalize_queue_arn]
  }
}

data "aws_iam_policy_document" "reference_build" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda["reference-build"].arn}:*"]
  }
  statement {
    sid     = "ReadWriteReferenceData"
    actions = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [
      "${local.data_bucket_arn}/bronze/static/*",
      "${local.data_bucket_arn}/silver/*",
      "${local.data_bucket_arn}/gold/static/*",
    ]
  }
}

locals {
  function_policies = {
    "discover"        = data.aws_iam_policy_document.discover.json
    "ingest-lead"     = data.aws_iam_policy_document.ingest_lead.json
    "checker"         = data.aws_iam_policy_document.checker.json
    "finalize"        = data.aws_iam_policy_document.finalize.json
    "reference-build" = data.aws_iam_policy_document.reference_build.json
  }
}

resource "aws_iam_role" "function" {
  for_each = local.function_policies

  name               = "berlin-temp-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = { component = each.key }
}

resource "aws_iam_role_policy" "function" {
  for_each = local.function_policies

  name   = "berlin-temp-${each.key}"
  role   = aws_iam_role.function[each.key].id
  policy = each.value
}
