output "images_role_arn" {
  value = aws_iam_role.images.arn
}

output "plan_role_arn" {
  value = aws_iam_role.plan.arn
}
