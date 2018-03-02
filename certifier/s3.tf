provider "aws" {
  region = "ca-central-1"
}

resource "aws_s3_bucket_object" "client_certificate" {
  bucket = "blink-keys"
  key    = "client_certificate"
  source = "~/blink/keys/blink-client.crt"
}

resource "aws_s3_bucket_object" "client_key" {
  bucket = "blink-keys"
  key    = "client_key"
  source = "~/blink/keys/blink-client.key"
}

resource "aws_s3_bucket_object" "server_certificate" {
  bucket = "blink-keys"
  key    = "server_certificate"
  source = "~/blink/keys/blink-drive.crt"
}

resource "aws_s3_bucket_object" "server_key" {
  bucket = "blink-keys"
  key    = "server_key"
  source = "~/blink/keys/blink-drive.key"
}

resource "aws_s3_bucket_object" "certificate_authority" {
  bucket = "blink-keys"
  key    = "certificate_authority"
  source = "~/blink/keys/ca.crt"
}

resource "aws_s3_bucket_object" "dh_params" {
  bucket = "blink-keys"
  key    = "dh_params"
  source = "~/blink/keys/dh.pem"
}

resource "aws_s3_bucket_object" "ta_key" {
  bucket = "blink-keys"
  key    = "ta_key"
  source = "~/blink/keys/ta.key"
}