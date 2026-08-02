terraform {
  required_version = ">= 1.13"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Floor is really 6.28, which added invoked_via_function_url.
      version = "~> 6.57"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }
}

provider "aws" {
  region = var.control_region

  default_tags {
    tags = {
      "shardvpn:managed-by" = "terraform"
    }
  }
}
