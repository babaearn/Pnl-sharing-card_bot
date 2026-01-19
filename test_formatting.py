#!/usr/bin/env python3
"""
Simple unit test for leaderboard entry formatting.
Tests the format_leaderboard_entry function.
"""

from typing import Dict


def format_leaderboard_entry(entry: Dict, show_points: bool) -> str:
    """
    Format a single leaderboard entry for public display.

    Args:
        entry: Dict with 'display_name' and 'points' keys
        show_points: Whether to display points

    Returns:
        Formatted string like "🏅 John Doe - 45 pts" or "🏅 John Doe"
    """
    name = entry.get('display_name') or "Unknown"

    if show_points:
        points = entry.get('points', 0)
        return f"🏅 {name} - {points} pts"
    else:
        return f"🏅 {name}"


def test_format_leaderboard_entry():
    """Test the leaderboard entry formatting function."""
    print("Running format_leaderboard_entry tests...\n")

    # Test 1: Normal entry with points displayed
    entry1 = {
        'code': '#01',
        'display_name': 'John Doe',
        'tg_user_id': 123456789,
        'username': 'johndoe',
        'points': 45
    }
    result1 = format_leaderboard_entry(entry1, show_points=True)
    expected1 = "🏅 John Doe - 45 pts"
    assert result1 == expected1, f"Test 1 failed: {result1} != {expected1}"
    print(f"✅ Test 1 passed: {result1}")

    # Test 2: Normal entry with points hidden
    result2 = format_leaderboard_entry(entry1, show_points=False)
    expected2 = "🏅 John Doe"
    assert result2 == expected2, f"Test 2 failed: {result2} != {expected2}"
    print(f"✅ Test 2 passed: {result2}")

    # Test 3: Entry with missing display_name (fallback to "Unknown")
    entry3 = {
        'code': '#02',
        'display_name': None,
        'points': 30
    }
    result3 = format_leaderboard_entry(entry3, show_points=True)
    expected3 = "🏅 Unknown - 30 pts"
    assert result3 == expected3, f"Test 3 failed: {result3} != {expected3}"
    print(f"✅ Test 3 passed: {result3}")

    # Test 4: Entry with no points key (defaults to 0)
    entry4 = {
        'display_name': 'Jane Smith'
    }
    result4 = format_leaderboard_entry(entry4, show_points=True)
    expected4 = "🏅 Jane Smith - 0 pts"
    assert result4 == expected4, f"Test 4 failed: {result4} != {expected4}"
    print(f"✅ Test 4 passed: {result4}")

    # Test 5: Entry with empty display_name (fallback to "Unknown")
    entry5 = {
        'display_name': '',
        'points': 10
    }
    result5 = format_leaderboard_entry(entry5, show_points=False)
    expected5 = "🏅 Unknown"
    assert result5 == expected5, f"Test 5 failed: {result5} != {expected5}"
    print(f"✅ Test 5 passed: {result5}")

    # Test 6: Verify code and username are NOT included in output
    entry6 = {
        'code': '#03',
        'display_name': 'Crypto King',
        'username': 'cryptoking',
        'points': 38
    }
    result6 = format_leaderboard_entry(entry6, show_points=True)
    assert '#03' not in result6, f"Test 6 failed: code should not appear in output"
    assert '@' not in result6, f"Test 6 failed: username should not appear in output"
    assert 'cryptoking' not in result6, f"Test 6 failed: username should not appear in output"
    assert result6 == "🏅 Crypto King - 38 pts", f"Test 6 failed: {result6}"
    print(f"✅ Test 6 passed: {result6} (no code/username)")

    print("\n✅ All tests passed!")


def show_example_outputs():
    """Display example outputs for documentation."""
    print("\n" + "="*60)
    print("EXAMPLE OUTPUTS")
    print("="*60 + "\n")

    sample_data = [
        {'code': '#01', 'display_name': 'John Doe', 'username': 'johndoe', 'points': 45},
        {'code': '#02', 'display_name': 'Jane Smith', 'username': None, 'points': 38},
        {'code': '#03', 'display_name': 'Crypto Trader', 'username': 'trader', 'points': 32},
        {'code': '#04', 'display_name': 'Moon Boy', 'username': 'moon', 'points': 28},
        {'code': '#05', 'display_name': 'HODL Master', 'username': 'hodler', 'points': 25},
    ]

    print("Example 1: /pnlrank with show_points=True")
    print("-" * 60)
    print("🏆 PnL Flex Challenge - Top 5\n")
    for entry in sample_data:
        print(format_leaderboard_entry(entry, show_points=True))

    print("\n\nExample 2: /pnlrank with show_points=False")
    print("-" * 60)
    print("🏆 PnL Flex Challenge - Top 5\n")
    for entry in sample_data:
        print(format_leaderboard_entry(entry, show_points=False))

    print("\n" + "="*60)
    print("KEY CHANGES:")
    print("  ✅ All ranks use 🏅 emoji (not 🥇🥈🥉)")
    print("  ✅ No participant codes (#01, #02, etc.)")
    print("  ✅ No usernames (@username)")
    print("  ✅ Only display_name shown")
    print("  ✅ Points display respects show_points setting")
    print("="*60 + "\n")


if __name__ == "__main__":
    test_format_leaderboard_entry()
    show_example_outputs()
