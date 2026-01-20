#!/usr/bin/env python3
"""
Test script for Top 10 leaderboard display.
Verifies the new /pnlrank format with medals for 1-5 and plain for 6-10.
"""

from typing import Dict, List


def format_leaderboard_top10(leaderboard: List[Dict], show_points: bool) -> str:
    """
    Format Top 10 leaderboard with medals for 1-5, plain for 6-10.

    Args:
        leaderboard: List of participant dicts with display_name and points
        show_points: Whether to display points

    Returns:
        Formatted leaderboard text
    """
    if not leaderboard:
        return "📊 No submissions yet!"

    lines = ["🏆 PnL Flex Challenge - Top 10\n"]

    for idx, entry in enumerate(leaderboard, 1):
        name = entry.get('display_name') or "Unknown"
        points = entry.get('points', 0)

        if idx <= 5:
            # Top 5: Show with 🏅 medal
            if show_points:
                lines.append(f"🏅 {name} - {points} pts")
            else:
                lines.append(f"🏅 {name}")
        else:
            # Positions 6-10: Plain format (encouragement)
            if show_points:
                lines.append(f"{idx}. {name} - {points} pts")
            else:
                lines.append(f"{idx}. {name}")

    return "\n".join(lines)


def test_top10_with_points():
    """Test Top 10 leaderboard with points displayed."""
    print("=" * 60)
    print("TEST 1: Top 10 with Points Displayed")
    print("=" * 60)

    leaderboard = [
        {'display_name': 'John Doe', 'points': 45},
        {'display_name': 'Jane Smith', 'points': 38},
        {'display_name': 'Crypto Trader', 'points': 32},
        {'display_name': 'Moon Boy', 'points': 28},
        {'display_name': 'HODL Master', 'points': 25},
        {'display_name': 'Ramesh', 'points': 22},
        {'display_name': 'Dream Catcher', 'points': 19},
        {'display_name': 'Shilpa', 'points': 15},
        {'display_name': 'Trader Pro', 'points': 12},
        {'display_name': 'Crypto King', 'points': 10},
    ]

    result = format_leaderboard_top10(leaderboard, show_points=True)
    print(result)
    print()

    # Verify format
    lines = result.split('\n')
    assert lines[0] == "🏆 PnL Flex Challenge - Top 10", "Header incorrect"
    assert lines[1] == "", "Blank line missing"
    assert "🏅 John Doe - 45 pts" in result, "Position 1 format incorrect"
    assert "🏅 HODL Master - 25 pts" in result, "Position 5 format incorrect"
    assert "6. Ramesh - 22 pts" in result, "Position 6 format incorrect"
    assert "10. Crypto King - 10 pts" in result, "Position 10 format incorrect"

    print("✅ Test 1 PASSED: All positions formatted correctly")
    print()


def test_top10_without_points():
    """Test Top 10 leaderboard without points displayed."""
    print("=" * 60)
    print("TEST 2: Top 10 without Points Displayed")
    print("=" * 60)

    leaderboard = [
        {'display_name': 'John Doe', 'points': 45},
        {'display_name': 'Jane Smith', 'points': 38},
        {'display_name': 'Crypto Trader', 'points': 32},
        {'display_name': 'Moon Boy', 'points': 28},
        {'display_name': 'HODL Master', 'points': 25},
        {'display_name': 'Ramesh', 'points': 22},
        {'display_name': 'Dream Catcher', 'points': 19},
        {'display_name': 'Shilpa', 'points': 15},
    ]

    result = format_leaderboard_top10(leaderboard, show_points=False)
    print(result)
    print()

    # Verify format
    assert "🏅 John Doe" in result and "John Doe -" not in result, "Position 1 format incorrect (points shown)"
    assert "🏅 HODL Master" in result and "HODL Master -" not in result, "Position 5 format incorrect (points shown)"
    assert "6. Ramesh" in result and "Ramesh -" not in result, "Position 6 format incorrect (points shown)"
    assert "8. Shilpa" in result and "Shilpa -" not in result, "Position 8 format incorrect (points shown)"
    assert " pts" not in result, "Points values should not be displayed"

    print("✅ Test 2 PASSED: Points hidden correctly")
    print()


def test_less_than_10_participants():
    """Test with fewer than 10 participants."""
    print("=" * 60)
    print("TEST 3: Fewer than 10 Participants")
    print("=" * 60)

    leaderboard = [
        {'display_name': 'John Doe', 'points': 45},
        {'display_name': 'Jane Smith', 'points': 38},
        {'display_name': 'Crypto Trader', 'points': 32},
    ]

    result = format_leaderboard_top10(leaderboard, show_points=True)
    print(result)
    print()

    # Verify format
    assert "🏅 John Doe - 45 pts" in result, "Position 1 format incorrect"
    assert "🏅 Jane Smith - 38 pts" in result, "Position 2 format incorrect"
    assert "🏅 Crypto Trader - 32 pts" in result, "Position 3 format incorrect"
    assert "4." not in result, "Position 4 should not exist"

    print("✅ Test 3 PASSED: Shows only available participants")
    print()


def test_exactly_5_participants():
    """Test with exactly 5 participants (edge case)."""
    print("=" * 60)
    print("TEST 4: Exactly 5 Participants (Edge Case)")
    print("=" * 60)

    leaderboard = [
        {'display_name': 'John Doe', 'points': 45},
        {'display_name': 'Jane Smith', 'points': 38},
        {'display_name': 'Crypto Trader', 'points': 32},
        {'display_name': 'Moon Boy', 'points': 28},
        {'display_name': 'HODL Master', 'points': 25},
    ]

    result = format_leaderboard_top10(leaderboard, show_points=True)
    print(result)
    print()

    # Verify format
    lines = result.split('\n')
    assert len([l for l in lines if l and l != "🏆 PnL Flex Challenge - Top 10"]) == 5, "Should have exactly 5 entries"
    assert all("🏅" in line for line in lines[2:]), "All entries should have medals"
    assert "6." not in result, "Position 6 should not exist"

    print("✅ Test 4 PASSED: All 5 positions have medals")
    print()


def test_exactly_6_participants():
    """Test with exactly 6 participants (first plain entry)."""
    print("=" * 60)
    print("TEST 5: Exactly 6 Participants (First Plain Entry)")
    print("=" * 60)

    leaderboard = [
        {'display_name': 'John Doe', 'points': 45},
        {'display_name': 'Jane Smith', 'points': 38},
        {'display_name': 'Crypto Trader', 'points': 32},
        {'display_name': 'Moon Boy', 'points': 28},
        {'display_name': 'HODL Master', 'points': 25},
        {'display_name': 'Ramesh', 'points': 22},
    ]

    result = format_leaderboard_top10(leaderboard, show_points=True)
    print(result)
    print()

    # Verify format
    assert "🏅 HODL Master - 25 pts" in result, "Position 5 should have medal"
    assert "6. Ramesh - 22 pts" in result, "Position 6 should be plain"
    assert result.count("🏅") == 5, "Should have exactly 5 medals"

    print("✅ Test 5 PASSED: Position 6 is plain (no medal)")
    print()


def test_empty_leaderboard():
    """Test with no participants."""
    print("=" * 60)
    print("TEST 6: Empty Leaderboard")
    print("=" * 60)

    leaderboard = []

    result = format_leaderboard_top10(leaderboard, show_points=True)
    print(result)
    print()

    assert result == "📊 No submissions yet!", "Empty leaderboard message incorrect"

    print("✅ Test 6 PASSED: Empty leaderboard handled correctly")
    print()


def show_comparison():
    """Show side-by-side comparison of old vs new format."""
    print("\n" + "=" * 60)
    print("COMPARISON: Old Format vs New Format")
    print("=" * 60)

    leaderboard = [
        {'display_name': 'John Doe', 'points': 45},
        {'display_name': 'Jane Smith', 'points': 38},
        {'display_name': 'Crypto Trader', 'points': 32},
        {'display_name': 'Moon Boy', 'points': 28},
        {'display_name': 'HODL Master', 'points': 25},
        {'display_name': 'Ramesh', 'points': 22},
        {'display_name': 'Dream Catcher', 'points': 19},
        {'display_name': 'Shilpa', 'points': 15},
        {'display_name': 'Trader Pro', 'points': 12},
        {'display_name': 'Crypto King', 'points': 10},
    ]

    print("\n📌 OLD FORMAT (Top 5 only):")
    print("-" * 60)
    print("🏆 PnL Flex Challenge - Top 5\n")
    for i in range(5):
        name = leaderboard[i]['display_name']
        pts = leaderboard[i]['points']
        print(f"🏅 {name} - {pts} pts")

    print("\n📌 NEW FORMAT (Top 10 with encouragement):")
    print("-" * 60)
    print(format_leaderboard_top10(leaderboard, show_points=True))

    print("\n" + "=" * 60)
    print("KEY DIFFERENCES:")
    print("  ✅ Shows Top 10 (was Top 5)")
    print("  ✅ Positions 1-5: 🏅 medals (same)")
    print("  ✅ Positions 6-10: Plain numbered (NEW - encouragement)")
    print("  ✅ Helps users see they're close to Top 5")
    print("=" * 60)


if __name__ == "__main__":
    print("\n🧪 Running Top 10 Leaderboard Tests\n")

    test_top10_with_points()
    test_top10_without_points()
    test_less_than_10_participants()
    test_exactly_5_participants()
    test_exactly_6_participants()
    test_empty_leaderboard()
    show_comparison()

    print("\n✅ ALL TESTS PASSED!")
    print("🎉 Top 10 leaderboard format working correctly!\n")
