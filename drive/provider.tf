provider "aws" {
  region = "ca-central-1"
  profile = "${var.aws_credentials_profile}"
}
