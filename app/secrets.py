"""Secrets that can change without a redeploy.

SECRETS_BACKEND=env (default): the value comes from the environment, as before.
SECRETS_BACKEND=ssm: the value is read from AWS Systems Manager Parameter Store (`/careerpilot/<NAME>`, SecureString) with the
server's IAM role, cached for 60 seconds. Rotating a key is then "edit the parameter in the AWS console": the next model call
after a minute uses it, with no image rebuild and no restart. If the parameter is missing or unreachable, the environment value
is used, so a Parameter Store problem degrades instead of taking the app down.
"""
import logging
import time
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger(__name__)
CACHE_SECONDS = 60
_cache: dict[str, tuple[float, str]] = {}


@lru_cache
def _ssm():
    import boto3  # noqa: PLC0415

    return boto3.client("ssm", region_name=get_settings().aws_region)


def get_secret(name: str, default: str = "") -> str:
    s = get_settings()
    if s.secrets_backend != "ssm":
        return default
    now = time.monotonic()
    hit = _cache.get(name)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1] or default
    try:
        value = _ssm().get_parameter(Name=f"{s.secrets_prefix.rstrip('/')}/{name}", WithDecryption=True)["Parameter"]["Value"].strip()
    except Exception as e:  # noqa: BLE001 - never block a request because the secret store is unreachable
        log.warning("could not read secret %s from Parameter Store (%s); using the environment value", name, type(e).__name__)
        value = ""
    _cache[name] = (now, value)
    return value or default


def clear_cache() -> None:
    _cache.clear()
