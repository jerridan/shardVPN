variable "amis" {
  type = "map"
  default = {
    "ca-central-1" = "ami-b3d965d7"
  }
}
variable "region" {
  default = "ca-central-1"
}
