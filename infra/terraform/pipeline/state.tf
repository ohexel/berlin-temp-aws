# Pipeline state: one item per (run, lead) plus one "meta" item per run.
# Key attributes PK/SK as used in src/berlin_temp/pipeline/state.py.
resource "aws_dynamodb_table" "state" {
  name         = "berlin-temp-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { component = "state" }
}
