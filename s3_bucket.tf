provider "aws" {
  region = "ca-central-1"
}

resource "aws_s3_bucket" "blink_keys" {
  bucket = "blink-keys"
  acl = "private"
  force_destroy = "true"
  server_side_encryption_configuration {
    rule {
      apply_server_side_encryption_by_default {
        sse_algorithm = "aws:kms"
      }
    }
  }
}
