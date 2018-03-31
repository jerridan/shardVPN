variable "aws_credentials_profile" {}

variable "traffic_protocol" {
  default = "udp"
}

variable "port_numbers" {
  type = "map"
  default = {
    "udp" = 1194
    "tcp" = 443
  }
}
