# tests/conftest.py
import os

# handler.py reads CONTROL_REGION at import time and SHOULD fail hard in
# production if it is unset. Tests supply it here rather than weakening that.
os.environ.setdefault("CONTROL_REGION", "ca-central-1")
os.environ.setdefault("TOPIC_ARN", "arn:aws:sns:ca-central-1:000000000000:test")
