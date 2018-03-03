provider "aws" {
  region = "ca-central-1"
}

variable "key_directory" {
  default = "/etc/blinkvpn"
}

resource "aws_s3_bucket_object" "client_certificate" {
  bucket = "blink-keys"
  key    = "client_certificate"
  source = "${var.key_directory}/blink-client.crt"
  server_side_encryption = "aws:kms"
}

resource "aws_s3_bucket_object" "client_key" {
  bucket = "blink-keys"
  key    = "client_key"
  source = "${var.key_directory}/blink-client.key"
  server_side_encryption = "aws:kms"
}

resource "aws_s3_bucket_object" "server_certificate" {
  bucket = "blink-keys"
  key    = "server_certificate"
  source = "${var.key_directory}/blink-drive.crt"
  server_side_encryption = "aws:kms"
}

resource "aws_s3_bucket_object" "server_key" {
  bucket = "blink-keys"
  key    = "server_key"
  source = "${var.key_directory}/blink-drive.key"
  server_side_encryption = "aws:kms"
}

resource "aws_s3_bucket_object" "certificate_authority" {
  bucket = "blink-keys"
  key    = "certificate_authority"
  source = "${var.key_directory}/ca.crt"
  server_side_encryption = "aws:kms"
}

resource "aws_s3_bucket_object" "dh_params" {
  bucket = "blink-keys"
  key    = "dh_params"
  source = "${var.key_directory}/dh.pem"
  server_side_encryption = "aws:kms"
}

resource "aws_s3_bucket_object" "ta_key" {
  bucket = "blink-keys"
  key    = "ta_key"
  source = "${var.key_directory}/ta.key"
  server_side_encryption = "aws:kms"
}