# tests/test_smoke.py
import sys


def test_python_version_matches_lambda_runtime():
    assert sys.version_info[:2] == (3, 13)


def test_package_is_importable():
    import shardvpn  # noqa: F401
