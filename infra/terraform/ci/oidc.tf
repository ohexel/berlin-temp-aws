# GitHub Actions authenticates with short-lived OIDC tokens; no access keys are stored in GitHub.
data "aws_caller_identity" "current" {}

locals {
  github_oidc_host = "token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.github_oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

# Image workflow (pushes to main): push to the two ECR repositories, nothing else.
# Trust: only this repository, and only its main branch.
data "aws_iam_policy_document" "images_trust" {
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
      values   = ["repo:${var.github_subject_repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "images" {
  name               = "berlin-temp-ci-images"
  assume_role_policy = data.aws_iam_policy_document.images_trust.json
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

# No plan role: terraform plan and apply run by hand (decision D7). A team project would add
# a read-only role for `terraform plan` on pull requests.
