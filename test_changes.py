#!/usr/bin/env python3
"""
Quick sanity tests for recent changes.
Run with: python test_changes.py
"""

import re
import sys

def test_rate_limit_parsing():
    """Test the rate limit retry time parsing logic from app.py"""

    test_cases = [
        # (error_message, expected_wait_time)
        ("retry in 10m0s", 600),      # 10 minutes
        ("retry in 10m30s", 600),     # 10 min 30 sec -> capped at 600
        ("retry in 5m0s", 300),       # 5 minutes
        ("retry in 30s", 30),         # 30 seconds (but min is 10)
        ("retry in 10s", 10),         # 10 seconds
        ("retry in 1m0s", 60),        # 1 minute
        ("retry in 15m0s", 600),      # 15 min -> capped at 600
        ("some other error", 10),     # No match -> default 10
    ]

    passed = 0
    failed = 0

    for error_msg, expected in test_cases:
        # This is the logic from app.py
        wait_time = 10  # Default
        retry_match = re.search(r'retry in (\d+)m?(\d*)s?', error_msg)
        if retry_match:
            minutes = int(retry_match.group(1)) if retry_match.group(1) else 0
            seconds = int(retry_match.group(2)) if retry_match.group(2) else 0
            if 'm' in error_msg[retry_match.start():retry_match.end()]:
                wait_time = minutes * 60 + seconds
            else:
                wait_time = minutes  # First number is seconds
            wait_time = max(10, min(wait_time, 600))

        if wait_time == expected:
            print(f"  PASS: '{error_msg}' -> {wait_time}s")
            passed += 1
        else:
            print(f"  FAIL: '{error_msg}' -> {wait_time}s (expected {expected}s)")
            failed += 1

    return failed == 0


def test_imports():
    """Test that modified modules can be imported without errors"""

    modules_to_test = [
        ("app", "Main indexer app"),
        ("apps.homebase.indexer", "Homebase indexer"),
        ("apps.homebase.paper", "Homebase paper"),
        ("apps.homebase.config", "Homebase config"),
        ("apps.trustless.paper", "Trustless paper"),
        ("apps.trustless.indexer", "Trustless indexer"),
        ("apps.autonet.paper", "Autonet paper"),
        ("activity", "Activity feed"),
    ]

    passed = 0
    failed = 0

    # Add current directory to path
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    for module_name, description in modules_to_test:
        try:
            # We can't fully import app.py because it parses args and connects to things
            # So we just check syntax by compiling
            if module_name == "app":
                with open("app.py", "r", encoding="utf-8") as f:
                    compile(f.read(), "app.py", "exec")
                print(f"  PASS: {description} (syntax OK)")
            else:
                __import__(module_name)
                print(f"  PASS: {description}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {description} - {e}")
            failed += 1

    return failed == 0


def test_activity_feed():
    """The activity feed's helpers: document ids match per-project activity, amounts read in whole units"""
    from activity import doc_id, format_units, short, record_activity

    class FakeHash(bytes):
        def hex(self):
            return "ab" * 32

    log = {'transactionHash': FakeHash(b''), 'logIndex': 3, 'blockNumber': 7}
    checks = [
        (doc_id(log), "ab" * 32 + "_3"),
        (format_units(1500 * 10**18, 18), "1,500"),
        (format_units(15 * 10**17, 18), "1.5"),
        (format_units(123456789, 6), "123.456789"),
        (format_units(5, 0), "5"),
        (short("0x" + "1" * 40), "0x1111...1111"),
    ]

    # record_activity writes one doc under activity/{id}, with the 0x-prefixed tx hash and block time
    written = {}

    class Ref:
        def __init__(self, path):
            self.path = path
            self.id = path.split('/')[-1]

        def collection(self, name):
            return Ref(f"{self.path}/{name}")

        def document(self, name):
            return Ref(f"{self.path}/{name}")

        def set(self, data):
            written[self.path] = data

    class Eth:
        def get_block(self, n):
            return {'timestamp': 1_700_000_000}

        def get_transaction(self, h):
            return {'from': '0xABCDEF0000000000000000000000000000000001'}

    class Web3Fake:
        eth = Eth()

    record_activity(Ref("idaosX/0xDao"), Web3Fake(), log, 'vote', 'Voted For on P', target_kind='proposal', target_id=12)
    entry = written.get("idaosX/0xDao/activity/" + "ab" * 32 + "_3", {})
    checks += [
        (entry.get('txHash'), "0x" + "ab" * 32),
        (entry.get('actor'), "0xabcdef0000000000000000000000000000000001"),
        (entry.get('targetId'), "12"),
        (entry.get('type'), "vote"),
    ]
    failed = [(got, want) for got, want in checks if got != want]
    for got, want in failed:
        print(f"  FAIL: got {got!r}, expected {want!r}")
    if not failed:
        print(f"  PASS: {len(checks)} activity checks")
    return not failed


def main():
    print("=" * 60)
    print("  INDEXER SANITY TESTS")
    print("=" * 60)

    all_passed = True

    print("\n[1] Rate Limit Parsing")
    print("-" * 40)
    if not test_rate_limit_parsing():
        all_passed = False

    print("\n[2] Module Imports")
    print("-" * 40)
    if not test_imports():
        all_passed = False

    print("\n[3] Activity Feed")
    print("-" * 40)
    if not test_activity_feed():
        all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("  ALL TESTS PASSED")
        print("=" * 60)
        return 0
    else:
        print("  SOME TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
