#!/usr/bin/env python3
"""
Simple test to verify the fix works - create a test client and check Xray status.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from xui import create_client, _verify_xray_running, close_session


async def main():
    print("=" * 60)
    print("Testing client creation with safe API endpoint")
    print("=" * 60)
    
    # Check Xray before
    print("\n1. Xray status BEFORE:")
    xray_before = await _verify_xray_running()
    print(f"   Running: {xray_before}")
    
    if not xray_before:
        print("   ERROR: Xray not running before test!")
        return 1
    
    # Create test client
    print("\n2. Creating test client...")
    try:
        result = await create_client(user_id=999999, days=1, limit_ip=1)
        if result:
            print(f"   SUCCESS: Client created")
            print(f"   UUID: {result['uuid']}")
            print(f"   Short ID: {result['short_id']}")
        else:
            print("   FAILED: create_client returned None")
            return 1
    except Exception as e:
        print(f"   ERROR: {e}")
        return 1
    
    # Check Xray after
    print("\n3. Xray status AFTER:")
    xray_after = await _verify_xray_running()
    print(f"   Running: {xray_after}")
    
    await close_session()
    
    print("\n" + "=" * 60)
    if xray_after:
        print("TEST PASSED: Xray still running after client creation")
        print("=" * 60)
        return 0
    else:
        print("TEST FAILED: Xray crashed after client creation")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
