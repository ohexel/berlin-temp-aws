# Images are tagged with the git commit (short SHA), so tags are immutable.
locals {
  ecr_repositories = ["berlin-temp-pipeline", "berlin-temp-reference"]
}

resource "aws_ecr_repository" "images" {
  for_each = toset(local.ecr_repositories)

  name                 = each.key
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true # lets `terraform destroy` remove repositories that still hold images

  tags = { component = "ecr" }
}

resource "aws_ecr_lifecycle_policy" "images" {
  for_each   = aws_ecr_repository.images
  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
