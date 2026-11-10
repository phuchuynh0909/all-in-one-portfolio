"""Cache keys for shared market data, independent of database connections."""
import hashlib
import inspect
import json

from fastapi.encoders import jsonable_encoder


def market_data_key_builder(func, namespace="", *, request=None, response=None, args=(), kwargs=None):
    arguments = inspect.signature(func).bind(*args, **(kwargs or {}))
    arguments.apply_defaults()
    arguments.arguments.pop("db", None)
    payload = json.dumps(jsonable_encoder(arguments.arguments), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"{namespace}:{func.__module__}.{func.__qualname__}:{digest}"
