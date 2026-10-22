# GitHub Actions authenticates with short-lived OIDC tokens; no access keys are stored in GitHub.
data "aws_caller_identity" "current" {}

locals {
  github_oidc_host = "token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.github_oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

# Trust policy: only this repository, and only the given subject (branch or pull requests).
data "aws_iam_policy_document" "trust" {
  for_each = {
    images = "repo:${var.github_subject_repository}:ref:refs/heads/main"
    plan   = "repo:${var.github_subject_repository}:pull_request"
  }

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:sub"
      values   = [each.value]
    }
  }
}

# Image workflow (pushes to main): push to the two ECR repositories, nothing else.
resource "aws_iam_role" "images" {
  name               = "berlin-temp-ci-images"
  assume_role_policy = data.aws_iam_policy_document.trust["images"].json
}

data "aws_iam_policy_document" "images" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:DescribeImages",
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [
      for name in var.ecr_repositories :
      "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${name}"
    ]
  }
}

resource "aws_iam_role_policy" "images" {
  name   = "push-berlin-temp-images"
  role   = aws_iam_role.images.id
  policy = data.aws_iam_policy_document.images.json
}

# Plan workflow (pull requests): read-only. It runs terraform plan with -lock=false, so it needs
# no write access to the state bucket. ReadOnlyAccess is broad (it can read S3 objects), which is
# acceptable for a private single-owner repository.
resource "aws_iam_role" "plan" {
  name               = "berlin-temp-ci-plan"
  assume_role_policy = data.aws_iam_policy_document.trust["plan"].json
}

resource "aws_iam_role_policy_attachment" "plan_read_only" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
