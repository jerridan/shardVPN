# tests/conftest.py
import os

# handler.py reads CONTROL_REGION at import time and SHOULD fail hard in
# production if it is unset. Tests supply it here rather than weakening that.
os.environ.setdefault("CONTROL_REGION", "ca-central-1")
os.environ.setdefault("TOPIC_ARN", "arn:aws:sns:ca-central-1:000000000000:test")

# Force dummy AWS credentials so the suite is hermetic.
#
# boto3 resolves credentials when a client is CONSTRUCTED, before Stubber ever
# intercepts a call — so client creation reaches into whatever the developer
# has configured. That made the suite silently dependent on ambient state: it
# passed on a machine with no AWS config, and failed outright on one set up
# with `aws login`, because botocore's LoginProvider requires botocore[crt],
# which this project deliberately does not depend on.
#
# Assigned, not setdefault: a developer with real credentials exported must
# not have the tests pick them up. Environment variables take precedence over
# both the shared credentials file and the config file, so this preempts the
# whole chain. AWS_PROFILE is cleared for the same reason — a named profile
# would otherwise send resolution back to the config file.
#
# These values are never used to sign anything: every AWS call in the suite is
# either stubbed with botocore.stub.Stubber or a MagicMock.
os.environ.pop("AWS_PROFILE", None)
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # noqa: S105
os.environ["AWS_SESSION_TOKEN"] = "testing"  # noqa: S105
os.environ["AWS_DEFAULT_REGION"] = "ca-central-1"
