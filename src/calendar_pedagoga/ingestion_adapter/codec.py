"""Deterministic JSON transport for an explicit whitelist, no pickle or imports from wire."""
import base64
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from hashlib import sha256
import json
import math

from calendar_pedagoga.lossless_document import models as a
from calendar_pedagoga.structural_interpretation import models as b
from calendar_pedagoga.ingestion_confirmation import models as c
from . import models as d

_MODULES = (a, b, c, d)
_TYPES = {cls.__module__ + "." + cls.__name__: cls
          for module in _MODULES for cls in vars(module).values()
          if isinstance(cls, type) and is_dataclass(cls) and cls.__module__ == module.__name__}


def encode(value):
    if isinstance(value, Enum):
        return {"$enum": "State", "value": value.value}
    if isinstance(value, Fraction):
        return {"$fraction": [value.numerator, value.denominator]}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Nonfinite decimal")
        return {"$decimal": str(value)}
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if is_dataclass(value):
        name = type(value).__module__ + "." + type(value).__name__
        if name not in _TYPES:
            raise ValueError("Unsupported transport type: " + name)
        return {"$type": name, "fields": {f.name: encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, tuple):
        return {"$tuple": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("Transport keys must be strings")
        return {"$dict": {k: encode(v) for k, v in value.items()}}
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("Unsupported transport value")


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        if value is None or type(value) in (str, int, bool):
            return value
        if type(value) is float and math.isfinite(value):
            return value
        raise ValueError("Invalid transport primitive")
    if set(value) == {"$type", "fields"}:
        cls = _TYPES.get(value["$type"])
        if cls is None or not isinstance(value["fields"], dict):
            raise ValueError("Unknown transport type")
        if set(value["fields"]) != {f.name for f in fields(cls)}:
            raise ValueError("Incomplete or unexpected fields")
        return cls(**{k: decode(v) for k, v in value["fields"].items()})
    if set(value) == {"$fraction"}:
        n, den = value["$fraction"]
        if type(n) is not int or type(den) is not int or den <= 0:
            raise ValueError("Invalid rational hours")
        return Fraction(n, den)
    if set(value) == {"$bytes"}:
        return base64.b64decode(value["$bytes"], validate=True)
    if set(value) == {"$decimal"}:
        result = Decimal(value["$decimal"])
        if not result.is_finite():
            raise ValueError("Nonfinite decimal")
        return result
    if set(value) == {"$enum", "value"} and value["$enum"] == "State":
        return c.State(value["value"])
    if set(value) == {"$tuple"}:
        return tuple(decode(v) for v in value["$tuple"])
    if set(value) == {"$dict"}:
        return {k: decode(v) for k, v in value["$dict"].items()}
    raise ValueError("Unknown transport tag")


def dumps(value):
    return json.dumps(encode(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def loads(text):
    return decode(json.loads(text, object_pairs_hook=_unique_object,
                             parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x))))


def digest(value):
    return sha256(dumps(value).encode("utf-8")).hexdigest()
