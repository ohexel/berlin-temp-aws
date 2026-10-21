# Visibility timeout is at least 6 x the Lambda timeout (ingest 120 s, finalize 300 s).
# Failed messages go to the DLQ after 5 receives; there is no automatic redrive.
locals {
  queues = {
    ingest   = { visibility_timeout_seconds = 720 }
    finalize = { visibility_timeout_seconds = 1800 }
  }
}

resource "aws_sqs_queue" "dlq" {
  for_each = local.queues

  name                      = "berlin-temp-${each.key}-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  tags                      = { component = each.key }
}

resource "aws_sqs_queue" "main" {
  for_each = local.queues

  name                       = "berlin-temp-${each.key}-queue"
  visibility_timeout_seconds = each.value.visibility_timeout_seconds
  tags                       = { component = each.key }

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 5
  })
}
