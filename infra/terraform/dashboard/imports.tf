# One-time import of the stack that was built by hand. After a successful apply these blocks
# are no longer needed and can be deleted.
import {
  to = aws_s3_bucket.dashboard
  id = var.bucket_name
}

import {
  to = aws_s3_bucket_public_access_block.dashboard
  id = var.bucket_name
}

import {
  to = aws_s3_bucket_server_side_encryption_configuration.dashboard
  id = var.bucket_name
}

import {
  to = aws_s3_bucket_policy.dashboard
  id = var.bucket_name
}

import {
  to = aws_cloudfront_origin_access_control.dashboard
  id = "E<oac-id>"
}

import {
  to = aws_cloudfront_distribution.dashboard
  id = "E<distribution-id>"
}
