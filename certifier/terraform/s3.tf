provider "aws" {
  region = "ca-central-1"
}

variable "key_directory" {
  default = "/etc/blinkvpn"
}

resource "aws_s3_bucket_object" "client_certificate" {
  bucket = "blink-keys"
  key    = "blink-client.crt"
  source = "${var.key_directory}/blink-client.crt"
}

resource "aws_s3_bucket_object" "client_key" {
  bucket = "blink-keys"
  key    = "blink-client.key"
  source = "${var.key_directory}/blink-client.key"
}

resource "aws_s3_bucket_object" "server_certificate" {
  bucket = "blink-keys"
  key    = "blink-drive.crt"
  source = "${var.key_directory}/blink-drive.crt"
}

resource "aws_s3_bucket_object" "server_key" {
  bucket = "blink-keys"
  key    = "blink-drive.key"
  source = "${var.key_directory}/blink-drive.key"
}

resource "aws_s3_bucket_object" "certificate_authority" {
  bucket = "blink-keys"
  key    = "ca.crt"
  source = "${var.key_directory}/ca.crt"
}

resource "aws_s3_bucket_object" "dh_params" {
  bucket = "blink-keys"
  key    = "dh.pem"
  source = "${var.key_directory}/dh.pem"
}

resource "aws_s3_bucket_object" "ta_key" {
  bucket = "blink-keys"
  key    = "ta.key"
  source = "${var.key_directory}/ta.key"
}