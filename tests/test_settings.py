# tests/test_settings.py
import json

import boto3
import pytest
from botocore.stub import Stubber

from shardvpn import settings
from shardvpn.settings import (
    Pointer,
    PointerUnavailable,
    cached_secret,
    get_parameter,
    read_pointer,
    write_pointer,
)

POINTER = "/shardvpn/current-node"


@pytest.fixture
def ssm():
    return boto3.client("ssm", region_name="ca-central-1")


@pytest.fixture(autouse=True)
def clear_cache():
    settings._SECRET_CACHE.clear()
    yield
    settings._SECRET_CACHE.clear()


def test_read_pointer_parses_json(ssm):
    payload = json.dumps({"region": "eu-west-1", "instance_id": "i-0abc"})
    with Stubber(ssm) as stub:
        stub.add_response("get_parameter", {"Parameter": {"Value": payload}}, {"Name": POINTER})
        assert read_pointer(ssm, POINTER) == Pointer("eu-west-1", "i-0abc")


def test_read_pointer_returns_none_when_empty(ssm):
    with Stubber(ssm) as stub:
        stub.add_response("get_parameter", {"Parameter": {"Value": "none"}})
        assert read_pointer(ssm, POINTER) is None


def test_read_pointer_returns_none_when_parameter_missing(ssm):
    with Stubber(ssm) as stub:
        stub.add_client_error("get_parameter", service_error_code="ParameterNotFound")
        assert read_pointer(ssm, POINTER) is None


def test_read_pointer_raises_on_any_other_error(ssm):
    # Treating a transient SSM error as 'no node' would make `up` launch a
    # second instance during a blip. It must fail closed instead.
    with Stubber(ssm) as stub:
        stub.add_client_error("get_parameter", service_error_code="ThrottlingException")
        with pytest.raises(PointerUnavailable):
            read_pointer(ssm, POINTER)


def test_read_pointer_raises_on_malformed_json(ssm):
    with Stubber(ssm) as stub:
        stub.add_response("get_parameter", {"Parameter": {"Value": "{not json"}})
        with pytest.raises(PointerUnavailable):
            read_pointer(ssm, POINTER)


def test_write_pointer_stores_json(ssm):
    with Stubber(ssm) as stub:
        stub.add_response(
            "put_parameter",
            {"Version": 2},
            {
                "Name": POINTER,
                "Value": json.dumps({"region": "eu-west-1", "instance_id": "i-0abc"}),
                "Type": "String",
                "Overwrite": True,
            },
        )
        write_pointer(ssm, POINTER, Pointer("eu-west-1", "i-0abc"))


def test_write_pointer_clears_with_none(ssm):
    with Stubber(ssm) as stub:
        stub.add_response(
            "put_parameter",
            {"Version": 3},
            {"Name": POINTER, "Value": "none", "Type": "String", "Overwrite": True},
        )
        write_pointer(ssm, POINTER, None)


def test_get_parameter_requests_decryption_when_asked(ssm):
    with Stubber(ssm) as stub:
        stub.add_response(
            "get_parameter",
            {"Parameter": {"Value": "shh"}},
            {"Name": "/shardvpn/signing-secret", "WithDecryption": True},
        )
        assert get_parameter(ssm, "/shardvpn/signing-secret", decrypt=True) == "shh"


def test_cached_secret_only_calls_ssm_once_within_the_ttl(ssm):
    with Stubber(ssm) as stub:
        stub.add_response("get_parameter", {"Parameter": {"Value": "shh"}})
        assert cached_secret(ssm, "/shardvpn/signing-secret", now=1000) == "shh"
        # No second stubbed response: a second SSM call would raise.
        assert cached_secret(ssm, "/shardvpn/signing-secret", now=1200) == "shh"


def test_cached_secret_refetches_after_the_ttl(ssm):
    with Stubber(ssm) as stub:
        stub.add_response("get_parameter", {"Parameter": {"Value": "old"}})
        stub.add_response("get_parameter", {"Parameter": {"Value": "new"}})
        assert cached_secret(ssm, "/shardvpn/signing-secret", now=1000) == "old"
        assert cached_secret(ssm, "/shardvpn/signing-secret", now=1000 + 301) == "new"
