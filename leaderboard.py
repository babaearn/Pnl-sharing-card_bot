"""
Leaderboard Generation Module for PnL Flex Challenge Bot
Handles formatting and display of leaderboards with configurable point visibility.
"""

import logging
from data_manager import get_leaderboard, load_config, get_week_winners
from utils import get_current_week, get_week_date_range

logger = logging.getLogger(__name__)

# Emoji mappings for ranks
RANK_EMOJIS = {
    1: "🥇",
    2: "🥈",
    3: "🥉",
    4: "🏅",
    5: "🏅"
}


def format_leaderboard(show_points=None, limit=5, show_user_ids=False):
    """
    Format all-time leaderboard for display (TIME-INDEPENDENT).

    Args:
        show_points: Override config setting (True/False/None)
        limit: Number of entries to show (default 5)
        show_user_ids: Show user IDs (for admin view)

    Returns:
        str: Formatted leaderboard text
    """
    # Get leaderboard data (all-time)
    leaderboard = get_leaderboard(limit=limit)

    if not leaderboard:
        return "📊 No submissions yet"

    # Get config for point visibility
    if show_points is None:
        config = load_config()
        show_points = config.get('show_points', True)

    # Build header (all-time leaderboard)
    lines = ["🏆 PnL Flex Challenge - All Time", ""]

    # Format top entries
    for idx, entry in enumerate(leaderboard, 1):
        emoji = RANK_EMOJIS.get(idx, f"{idx}.")
        username = entry['username']

        # Format username with @ if it's not "Unknown"
        if username and username != "Unknown":
            username_display = f"@{username}"
        else:
            username_display = entry['full_name'] or f"User {entry['user_id']}"

        # Build line
        if show_points:
            line = f"{emoji} {username_display} - {entry['points']} points"
        else:
            line = f"{emoji} {username_display}"

        lines.append(line)

        # Add user ID for admin view
        if show_user_ids:
            lines.append(f"   ID: {entry['user_id']}")
            lines.append("")

    return "\n".join(lines)


def format_admin_dashboard():
    """
    Format detailed admin dashboard with top 10 and configuration (all-time).

    Returns:
        str: Formatted admin dashboard
    """
    # Get leaderboard data (all-time, top 10)
    leaderboard = get_leaderboard(limit=10)

    if not leaderboard:
        return "📊 No submissions yet"

    # Get config
    config = load_config()
    show_points = config.get('show_points', True)

    # Build header
    lines = ["🔐 Admin Dashboard - All Time", ""]

    # Format top 10 with user IDs
    for idx, entry in enumerate(leaderboard[:10], 1):
        emoji = RANK_EMOJIS.get(idx, f"{idx}.")
        username = entry['username']

        # Format username
        if username and username != "Unknown":
            username_display = f"@{username}"
        else:
            username_display = entry['full_name'] or f"User {entry['user_id']}"

        # Add entry with points
        lines.append(f"{emoji} {username_display} - {entry['points']} points")
        lines.append(f"   ID: {entry['user_id']}")
        lines.append("")

    # Add config status
    lines.append("")
    points_status = "ON ✅" if show_points else "OFF ❌"
    lines.append(f"⚙️ Points Display: {points_status}")

    return "\n".join(lines)


def format_engagement_stats(week=None):
    """
    Format engagement statistics for admin view.

    Args:
        week: Week number for new participants (None for current)

    Returns:
        str: Formatted engagement stats
    """
    from data_manager import get_engagement_stats, get_leaderboard
    from utils import get_current_week

    # Get current week if not specified
    if week is None:
        week = get_current_week()

    stats = get_engagement_stats(week)

    lines = [
        "📊 PnL Flex Challenge - Engagement Stats",
        "",
        f"👥 Total Participants: {stats['total_participants']} users",
        f"📸 Total Submissions: {stats['total_submissions']} images",
        f"📅 Campaign Day: {stats['campaign_day']} of 28",
        ""
    ]

    if week:
        lines.append(f"🆕 New Participants This Week: {stats['new_this_week']} users")

    if stats['most_active_user']:
        most_active_display = f"@{stats['most_active_user']}" if stats['most_active_user'] != "Unknown" else "Unknown User"
        lines.append(f"🔥 Most Active: {most_active_display} ({stats['most_active_count']} posts)")

    lines.append(f"📈 Avg Posts per User: {stats['avg_posts_per_user']}")

    return "\n".join(lines)


def format_winners_message(week, winners):
    """
    Format winners announcement message.

    Args:
        week: Week number (1-4)
        winners: List of winner dictionaries

    Returns:
        str: Formatted winners announcement
    """
    lines = [f"✅ Winners Selected for Week {week}", ""]

    for winner in winners:
        rank = winner['rank']
        emoji = RANK_EMOJIS.get(rank, f"{rank}.")
        username = winner['username']

        # Format username
        if username and username != "Unknown":
            username_display = f"@{username}"
        else:
            username_display = winner.get('full_name', f"Rank {rank}")

        # Include points in selection message
        points = winner.get('points', 0)
        lines.append(f"{emoji} {username_display} - {points} points")

    lines.append("")
    lines.append("✅ Saved to winners.json")

    return "\n".join(lines)


def format_saved_winners(week):
    """
    Format saved winners for display.

    Args:
        week: Week number (1-4)

    Returns:
        str: Formatted saved winners or error message
    """
    winners = get_week_winners(week)

    if not winners:
        return f"❌ No winners saved for Week {week} yet.\nUse /selectwinners {week} to select them."

    lines = [f"🏆 Week {week} Winners", ""]

    for winner in winners:
        rank = winner['rank']
        emoji = RANK_EMOJIS.get(rank, f"{rank}.")
        username = winner['username']

        # Format username
        if username and username != "Unknown":
            username_display = f"@{username}"
        else:
            username_display = winner.get('full_name', f"Rank {rank}")

        lines.append(f"{emoji} {username_display}")

    return "\n".join(lines)


def format_sync_notification(sync_stats):
    """
    Format sync notification message for admins.

    Args:
        sync_stats: Dictionary with sync statistics

    Returns:
        str: Formatted notification message
    """
    is_first_run = sync_stats.get('is_first_run', False)
    total_found = sync_stats.get('total_found', 0)
    existing_count = sync_stats.get('existing_count', 0)
    new_count = sync_stats.get('new_count', 0)
    duration = sync_stats.get('duration', 0)
    top_3 = sync_stats.get('top_3', [])
    date_range = sync_stats.get('date_range', '')

    lines = ["✅ Sync Complete" if not is_first_run else "✅ Initial Backfill Complete", ""]

    # First run message
    if is_first_run:
        lines.append(f"📸 Found {total_found} PnL cards")
        lines.append(f"👥 From {sync_stats.get('total_users', 0)} users")
        if date_range:
            lines.append(f"📅 {date_range}")
        lines.append(f"⏱️ Processed in {duration:.1f} seconds")
        lines.append("")
        lines.append(f"🆕 All {new_count} submissions added to database")
    else:
        # Restart message
        lines.append(f"📸 Found {total_found} PnL cards in topic")
        lines.append(f"💾 Database already has {existing_count} submissions")

        if new_count > 0:
            lines.append(f"🆕 Added {new_count} new submissions (missed while offline)")
            if date_range:
                lines.append("")
                lines.append(f"📅 Gap filled: {date_range}")
        else:
            lines.append("")
            lines.append("✅ Leaderboard is up to date")

        lines.append(f"⏱️ Scan took {duration:.1f} seconds")

    # Show top 3
    if top_3:
        lines.append("")
        if is_first_run:
            lines.append("Top 3:")
        else:
            lines.append("New Top 3:")

        for idx, entry in enumerate(top_3, 1):
            emoji = RANK_EMOJIS.get(idx, f"{idx}.")
            username = entry['username']

            if username and username != "Unknown":
                username_display = f"@{username}"
            else:
                username_display = entry.get('full_name', 'Unknown')

            change = entry.get('change', '')
            lines.append(f"{emoji} {username_display} - {entry['points']} points {change}")

    lines.append("")
    lines.append("Use /adminboard to see full rankings")

    return "\n".join(lines)
