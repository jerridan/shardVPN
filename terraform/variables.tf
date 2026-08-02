variable "control_region" {
  description = "Region hosting the Lambda, SSM parameters and SNS topic."
  type        = string
  default     = "ca-central-1"
}

variable "default_node_region" {
  description = "Region used when a launch request omits one."
  type        = string
  default     = "ca-central-1"
}

variable "instance_type" {
  description = "arm64 instance type for exit nodes. IAM restricts this to t4g.*."
  type        = string
  default     = "t4g.small"
}

variable "default_ttl" {
  description = "Default node TTL. 'none' means manual teardown only."
  type        = string
  default     = "none"
}

variable "idle_threshold_bytes" {
  description = "NetworkOut over 24h below which a node is unused. Set from measurement."
  type        = number
  default     = 5000000
}

variable "notification_email" {
  description = "Address for idle, orphan and alarm notifications."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Budget alarm threshold. Defence in depth against runaway spend."
  type        = number
  default     = 10
}

variable "tailscale_tag" {
  description = "Tag applied to exit nodes in the tailnet."
  type        = string
  default     = "tag:shardvpn-exit"
}
