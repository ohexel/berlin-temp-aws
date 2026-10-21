resource "aws_sns_topic" "alerts" {
  name = "berlin-temp-alerts"
  tags = { component = "alerts" }
}

# E-mail subscriptions stay "pending confirmation" until the recipient clicks the link.
resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
