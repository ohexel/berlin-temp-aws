output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "state_table_name" {
  value = aws_dynamodb_table.state.name
}

output "data_bucket_name" {
  value = aws_s3_bucket.data.bucket
}

output "ecr_repository_urls" {
  value = { for name, repo in aws_ecr_repository.images : name => repo.repository_url }
}

output "queue_urls" {
  value = { for name, queue in aws_sqs_queue.main : name => queue.url }
}

output "lambda_function_names" {
  value = [for f in aws_lambda_function.function : f.function_name]
}
