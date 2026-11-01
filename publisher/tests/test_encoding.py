import math
from datetime import datetime, timezone, timedelta

import pytest

from publisher.encoding import (AmbiguousMap, canonical, encode_value, obj_hash, sort_value, dt_to_us)
from publisher.notify import sign


def test_hash_vector():
    assert obj_hash(canonical({"coll": "proposals", "docs": []})) == "ceffff57e213d53c5e9eaad9cff91d3a"


def test_hmac_vector():
    assert sign("test-secret", 1730000000, b'{"messages":[]}') == \
        "v1=8cc09b05dd38fe0709b20698d82926fdf629b028c26ed77bd47929dff3df2a15"


def test_canonical_bytes():
    assert canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode("utf-8")
    with pytest.raises(ValueError):
        canonical({"x": math.nan})


def test_scalars_and_ints():
    assert encode_value(None) is None
    assert encode_value(True) is True
    assert encode_value(2 ** 53 - 1) == 2 ** 53 - 1
    assert encode_value(-(2 ** 53 - 1)) == -(2 ** 53 - 1)
    assert encode_value(2 ** 53) == {"$int": str(2 ** 53)}
    assert encode_value(-(2 ** 63)) == {"$int": str(-(2 ** 63))}
    assert encode_value(1.5) == 1.5
    assert encode_value(math.nan) == {"$float": "NaN"}
    assert encode_value(math.inf) == {"$float": "Infinity"}
    assert encode_value(-math.inf) == {"$float": "-Infinity"}


def test_timestamp_six_digits_utc():
    dt = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
    assert encode_value(dt) == {"$ts": "2026-10-13T12:00:00.000000Z"}
    dt2 = datetime(2026, 10, 13, 14, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    assert encode_value(dt2) == {"$ts": "2026-10-13T12:00:00.123456Z"}


def test_bytes_ref_geo_nested():
    class Ref:
        path = "a/b"
        parent = None
        id = "b"

    class Geo:
        latitude, longitude = 1, 2.5

    v = encode_value({"x": [b"\xDE\xAD", {"r": Ref(), "g": Geo()}]})
    assert v == {"x": [{"$bytes": "0xdead"}, {"r": {"$ref": "a/b"}, "g": {"$geo": [1.0, 2.5]}}]}


def test_ambiguous_map_fails():
    with pytest.raises(AmbiguousMap):
        encode_value({"m": {"$ts": "x"}})
    assert encode_value({"$ts": 1, "other": 2}) == {"$ts": 1, "other": 2}


def test_sort_value():
    us = dt_to_us(datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc))
    assert sort_value({"$ts": "2026-10-13T12:00:00.000000Z"}) == us
    assert sort_value("2026-10-13T12:00:00+00:00") == us
    assert sort_value("2026-10-13T12:00:00") == us            # naive = UTC
    assert sort_value("2026-10-13T14:00:00.5+02:00") == us + 500000
    assert sort_value("garbage") == 0
    assert sort_value(None) == 0
    assert sort_value(7) == 7
    assert sort_value({"$int": "99999999999999999999"}) == 99999999999999999999
