"""
Helper functions for PnL Flex Challenge Leaderboard Bot
- Week calculation based on campaign dates
- Admin authorization checks
- Timezone utilities
"""

from datetime import datetime, timedelta
import pytz
import os
import logging

logger = logging.getLogger(__name__)

# Timezone configuration
IST = pytz.timezone('Asia/Kolkata')

# Campaign dates in IST
# IMPORTANT: Use IST.localize() for proper timezone handling with pytz
CAMPAIGN_START = IST.localize(datetime(2025, 1, 15, 0, 1))
CAMPAIGN_END = IST.localize(datetime(2025, 2, 11, 23, 59, 59))

# Chat configuration from environment
CHAT_ID = int(os.getenv('CHAT_ID', '-1001868775086'))
TOPIC_ID = int(os.getenv('TOPIC_ID', '103380'))

# Admin IDs from environment (comma-separated)
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '1064156047').split(',')]


def calculate_week_number(timestamp):
    """
    Calculate week number (1-4) based on timestamp.

    Week breakdown:
    - Week 1: Jan 15-21
    - Week 2: Jan 22-28
    - Week 3: Jan 29-Feb 4
    - Week 4: Feb 5-11

    Args:
        timestamp: datetime object (timezone-aware)

    Returns:
        int: Week number (1-4) or None if before campaign start
    """
    # Ensure timestamp is timezone-aware
    if timestamp.tzinfo is None:
        timestamp = IST.localize(timestamp)
    else:
        timestamp = timestamp.astimezone(IST)

    if timestamp < CAMPAIGN_START:
        return None

    if timestamp > CAMPAIGN_END:
        return 4  # Cap at week 4 for late submissions

    days_since_start = (timestamp - CAMPAIGN_START).days
    week = (days_since_start // 7) + 1

    return min(week, 4)  # Cap at week 4


def get_current_week():
    """
    Get current week number based on current IST time.

    Returns:
        int: Current week number (1-4) or None if before campaign
    """
    now = datetime.now(IST)
    return calculate_week_number(now)


def get_week_date_range(week_num):
    """
    Get the start and end dates for a specific week.

    Args:
        week_num: Week number (1-4)

    Returns:
        tuple: (start_date, end_date) as datetime objects in IST
    """
    if week_num < 1 or week_num > 4:
        return None, None

    start_date = CAMPAIGN_START + timedelta(days=(week_num - 1) * 7)
    end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)

    # Cap week 4 end at campaign end
    if week_num == 4:
        end_date = CAMPAIGN_END

    return start_date, end_date


def is_admin(user_id):
    """
    Check if user ID is in admin list.

    Args:
        user_id: Telegram user ID

    Returns:
        bool: True if user is admin
    """
    return user_id in ADMIN_IDS


def normalize_participant_code(code: str) -> str:
    """
    Normalize participant code by ensuring it has # prefix.

    Examples:
        "33" -> "#33"
        "#33" -> "#33"
        "01" -> "#01"
        "#01" -> "#01"

    Args:
        code: Participant code (with or without # prefix)

    Returns:
        str: Normalized code with # prefix
    """
    code = code.strip()
    if not code.startswith('#'):
        # Pad single digit with leading zero if needed
        if code.isdigit() and len(code) == 1:
            return f"#{code.zfill(2)}"
        return f"#{code}"
    return code


def format_timestamp(dt):
    """
    Format datetime to IST string.

    Args:
        dt: datetime object

    Returns:
        str: Formatted timestamp in IST
    """
    if dt.tzinfo is None:
        dt = IST.localize(dt)
    else:
        dt = dt.astimezone(IST)

    return dt.strftime('%Y-%m-%dT%H:%M:%S%z')


def parse_timestamp(timestamp_str):
    """
    Parse ISO format timestamp string to datetime.

    Args:
        timestamp_str: ISO format timestamp string

    Returns:
        datetime: Parsed datetime object in IST
    """
    from dateutil import parser
    dt = parser.parse(timestamp_str)
    return dt.astimezone(IST)


class SensitiveFormatter(logging.Formatter):
    """
    Custom log formatter that masks sensitive information.
    Prevents bot tokens, DATABASE_URL, and user IDs from appearing in logs.
    """

    def format(self, record):
        import re
        message = super().format(record)

        # Mask bot token (format: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz)
        message = re.sub(r'\d{10}:[A-Za-z0-9_-]{35}', '[BOT_TOKEN_MASKED]', message)

        # Mask DATABASE_URL (postgresql://user:password@host/db)
        message = re.sub(r'postgresql://[^\s]+', 'postgresql://***', message)
        message = re.sub(r'postgres://[^\s]+', 'postgres://***', message)

        # Mask user IDs in various formats
        message = re.sub(r'user_id["\s:]+(\d{8,})', r'user_id: [MASKED]', message)
        message = re.sub(r'"user_id":\s*"?(\d{8,})"?', r'"user_id": "[MASKED]"', message)

        return message
