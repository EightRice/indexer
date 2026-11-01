"""Value encoding, canonical JSON and hashes (CONTRACT.md section 3)."""
import calendar
import hashlib
import json
import math
from datetime import datetime, timezone

TAGS = ("$int", "$float", "$ts", "$bytes", "$ref", "$geo")
MAX_SAFE = 2 ** 53 - 1


class AmbiguousMap(ValueError):
    pass


def dt_to_us(dt):
    """datetime -> integer microseconds since epoch (naive = UTC), exact."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return calendar.timegm(dt.timetuple()) * 1_000_000 + dt.microsecond


def ts_string(dt):
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".%06dZ" % dt.microsecond


def encode_value(v):
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v if abs(v) <= MAX_SAFE else {"$int": str(v)}
    if isinstance(v, float):
        if math.isnan(v):
            return {"$float": "NaN"}
        if math.isinf(v):
            return {"$float": "Infinity" if v > 0 else "-Infinity"}
        return v
    if isinstance(v, datetime):
        return {"$ts": ts_string(v)}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$bytes": "0x" + bytes(v).hex()}
    if isinstance(v, (list, tuple)):
        return [encode_value(x) for x in v]
    if isinstance(v, dict):
        if len(v) == 1 and next(iter(v)) in TAGS:
            raise AmbiguousMap(f"Firestore map {v!r} looks like a tagged value")
        return {str(k): encode_value(x) for k, x in v.items()}
    # Firestore specific types, matched by duck typing so tests need no client
    path = getattr(v, "path", None)
    if path is not None and hasattr(v, "parent") and hasattr(v, "id"):
        return {"$ref": path}
    if hasattr(v, "latitude") and hasattr(v, "longitude"):
        return {"$geo": [float(v.latitude), float(v.longitude)]}
    raise TypeError(f"unsupported Firestore value {type(v).__name__}")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def obj_hash(data):
    """data: canonical bytes"""
    return hashlib.sha256(data).hexdigest()[:32]


def parse_iso_us(s):
    """ISO 8601 string -> µs (naive = UTC); None when unparsable."""
    try:
        t = s.strip()
        if t.endswith("Z") or t.endswith("z"):
            t = t[:-1] + "+00:00"
        return dt_to_us(datetime.fromisoformat(t))
    except (ValueError, AttributeError, OverflowError):
        return None


def sort_value(encoded):
    """Sort key k of an encoded field value (4.4): 0 when missing or unparsable."""
    v = encoded
    if v is None or isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        r = parse_iso_us(v)
        return 0 if r is None else r
    if isinstance(v, dict) and len(v) == 1:
        if "$ts" in v:
            r = parse_iso_us(v["$ts"])
            return 0 if r is None else r
        if "$int" in v:
            try:
                return int(v["$int"])
            except ValueError:
                return 0
    return 0
