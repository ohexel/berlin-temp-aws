output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "state_table_name" {
  value = aws_dynamodb_table.state.name
}
