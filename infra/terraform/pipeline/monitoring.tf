# All alarms notify the SNS topic (e-mail). Periods and evaluation counts are not fixed by the
# plan; see the comments per alarm.
locals {
  alarm_actions = [aws_sns_topic.alerts.arn]
}

# A message in a DLQ: checked every 5 minutes. SQS publishes no datapoints for idle queues,
# so missing data counts as not breaching.
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  for_each = local.queues

  alarm_name          = "berlin-temp-${each.key}-dlq-not-empty"
  alarm_description   = "Messages in berlin-temp-${each.key}-dlq"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq[each.key].name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  tags                = { component = each.key }
}

# Any Lambda error in discover or finalize (the checker is deliberately not covered).
resource "aws_cloudwatch_metric_alarm" "function_errors" {
  for_each = toset(["discover", "finalize"])

  alarm_name          = "berlin-temp-${each.key}-errors"
  alarm_description   = "Errors in berlin-temp-${each.key}"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.function[each.key].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  tags                = { component = each.key }
}

# No finalize invocation for 3 hours (3 x 1 h). Missing data counts as breaching.
# Notifications stay off outside the continuous run (the alarm would fire all the time).
resource "aws_cloudwatch_metric_alarm" "no_publication" {
  alarm_name          = "berlin-temp-no-publication"
  alarm_description   = "No finalize invocation for 3 hours"
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.function["finalize"].function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  actions_enabled     = var.no_publication_alarm_enabled
  alarm_actions       = local.alarm_actions
  tags                = { component = "finalize" }
}
