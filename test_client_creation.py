#!/usr/bin/env python3
"""
Test script to verify that client creation doesn't crash Xray.
"""
import asyncio
import sys
import os

# Add the bot directory to the path
sys.path.insert(0, os.path.dirname(__file__))

from xui import create_client, _verify_xray_running, close_session


async def test_client_creation():
    """Test creating a client and verify Xray stays running."""
    print("=" * 60)
    print("Testing client creation with safe API endpoint")
    print("=" * 60)
    
    # Check Xray status before
    print("\n1. Checking Xray status BEFORE client creation...")
    xray_running_before = await _verify_xray_running()
    print(f"   Xray running: {xray_running_before}")
    
    if not xray_running_before:
        print("   ERROR: Xray is not running before test!")
        return False
    
    # Create a test client
    print("\n2. Creating test client...")
    try:
        result = await create_client(user_id=999999, days=1, limit_ip=1)
        if result:
            print(f"   SUCCESS: Client created")
            print(f"   UUID: {result['uuid']}")
            print(f"   Short ID: {result['short_id']}")
        else:
            print("   FAILED: create_client returned None")
            return False
    except Exception as e:
        print(f"   ERROR: Exception during client creation: {e}")
        return False
    
    # Check Xray status after
    print("\n3. Checking Xray status AFTER client creation...")
    xray_running_after = await _verify_xray_running()
    print(f"   Xray running: {xray_running_after}")
    
    # Close session
    await close_session()
    
    # Verify result
    print("\n" + "=" * 60)
    if xray_running_after:
        print("TEST PASSED: Xray is still running after client creation")
        print("=" * 60)
        return True
    else:
        print("TEST FAILED: Xray crashed after client creation")
        print("=" * 60)
        return False


async def test_multiple_clients():
    """Test creating multiple clients in sequence."""
    print("\n" + "=" * 60)
    print("Testing multiple client creations")
    print("=" * 60)
    
    num_clients = 3
    results = []
    
    for i in range(num_clients):
        print(f"\nCreating client {i+1}/{num_clients}...")
        try:
            result = await create_client(user_id=999999 + i, days=1, limit_ip=1)
            if result:
                print(f"   Client {i+1} created successfully")
                results.append(True)
            else:
                print(f"   Client {i+1} creation failed")
                results.append(False)
        except Exception as e:
            print(f"   Client {i+1} creation error: {e}")
            results.append(False)
        
        # Check Xray after each creation
        xray_running = await _verify_xray_running()
        print(f"   Xray running after client {i+1}: {xray_running}")
        if not xray_running:
            print(f"   ERROR: Xray crashed after client {i+1}")
            break
    
    await close_session()
    
    print("\n" + "=" * 60)
    if all(results) and await _verify_xray_running():
        print(f"TEST PASSED: All {num_clients} clients created, Xray still running")
        print("=" * 60)
        return True
    else:
        print(f"TEST FAILED: Only {sum(results)}/{num_clients} clients created or Xray crashed")
        print("=" * 60)
        return False


async def main():
    """Run all tests."""
    print("Starting client creation tests...\n")
    
    # Test single client creation
    test1_passed = await test_client_creation()
    
    # Test multiple client creations
    test2_passed = await test_multiple_clients()
    
    # Final summary
    print("\n" + "=" * 60)
    print("FINAL TEST RESULTS")
    print("=" * 60)
    print(f"Single client test: {'PASSED' if test1_passed else 'FAILED'}")
    print(f"Multiple clients test: {'PASSED' if test2_passed else 'FAILED'}")
    print("=" * 60)
    
    if test1_passed and test2_passed:
        print("\n✓ All tests passed! The fix is working correctly.")
        return 0
    else:
        print("\n✗ Some tests failed. Please check the logs.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
