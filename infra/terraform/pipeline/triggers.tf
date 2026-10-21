# Queue -> Lambda triggers: one message per invocation, bounded concurrency.
locals {
  triggers = {
    "ingest-lead" = { queue = "ingest", max_concurrency = 5 }
    "finalize"    = { queue = "finalize", max_concurrency = 2 }
  }
}

resource "aws_lambda_event_source_mapping" "queue" {
  for_each = local.triggers

  event_source_arn = aws_sqs_queue.main[each.value.queue].arn
  function_name    = aws_lambda_function.function[each.key].arn
  batch_size       = 1
  enabled          = true

  scaling_config {
    maximum_concurrency = each.value.max_concurrency
  }
}
