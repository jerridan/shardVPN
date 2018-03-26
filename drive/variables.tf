variable "traffic_protocol" {}

variable "port_numbers" {
  type = "map"
  default = {
    "udp" = 1194
    "tcp" = 443
  }
}
