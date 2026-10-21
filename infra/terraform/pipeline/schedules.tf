# EventBridge Scheduler: one schedule and one invoke-only role per function.
# Schedules cannot be tagged; the roles carry the tags.
locals {
  schedules = {
    "discover" = "cron(34,39,44,49,54,59 * * * ? *)"
    "checker"  = "rate(5 minutes)"
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "scheduler_invoke" {
  for_each = local.schedules

  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.function[each.key].arn]
  }
}

resource "aws_iam_role" "scheduler" {
  for_each = local.schedules

  name               = "berlin-temp-${each.key}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
  tags               = { component = each.key }
}

resource "aws_iam_role_policy" "scheduler" {
  for_each = local.schedules

  name   = "invoke-berlin-temp-${each.key}"
  role   = aws_iam_role.scheduler[each.key].id
  policy = data.aws_iam_policy_document.scheduler_invoke[each.key].json
}

resource "aws_scheduler_schedule" "tick" {
  for_each = local.schedules

  name                = "berlin-temp-${each.key}"
  schedule_expression = each.value
  state               = var.schedules_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.function[each.key].arn
    role_arn = aws_iam_role.scheduler[each.key].arn

    # No retries by the scheduler: the next tick comes within minutes and every step is idempotent.
    retry_policy {
      maximum_retry_attempts = 0
    }
  }

  depends_on = [aws_iam_role_policy.scheduler]
}
