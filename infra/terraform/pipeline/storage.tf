# Private data bucket: bronze / silver / gold.
resource "random_id" "data_bucket" {
  byte_length = 4
}

resource "aws_s3_bucket" "data" {
  bucket = "berlin-temp-data-${random_id.data_bucket.hex}"
  tags   = { component = "data" }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Static bronze and gold are kept until the final teardown.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-raw-grib"
    status = "Enabled"
    filter { prefix = "bronze/forecast/" }
    expiration { days = 2 }
  }

  rule {
    id     = "expire-silver-forecast-cell"
    status = "Enabled"
    filter { prefix = "silver/forecast_cell/" }
    expiration { days = 7 }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
