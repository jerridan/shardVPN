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
  description = "NetworkOut over 24h below which a node is considered unused."
  type        = number

  # Measured, not guessed. An idle t4g.small exit node on AL2023 emits ~45 MB
  # per 24h of NetworkOut with nothing routing through it — and only ~1.4% of
  # that is Tailscale keepalives. The rest is ordinary instance chatter
  # (SSM agent polling, DNS, NTP), so this threshold is really "is anything
  # using the VPN" layered on top of a substantial always-on floor.
  #
  # The original guess here was 5,000,000 — EIGHTEEN TIMES BELOW the real
  # floor. should_alert fires only when NetworkOut < threshold, so at 5 MB the
  # condition could never be true and the forgotten-node email would never
  # have sent. With TTL defaulting to "never", that was the entire safety net,
  # silently inert. Measured on the first live node, 2026-08-03.
  #
  # 300 MB is ~6.6x the measured floor. A node nobody is using alerts; an hour
  # of real browsing (~400 MB) stays quiet. Biased toward alerting: a spurious
  # "still running?" email costs nothing, a missed one costs $0.52/day forever.
  default = 300000000
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
