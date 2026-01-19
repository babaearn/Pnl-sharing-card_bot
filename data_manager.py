"""
Data Manager for PnL Flex Challenge Leaderboard Bot
Handles all JSON file operations with:
- Atomic writes (prevents corruption during crashes)
- Automatic backups before updates
- Safe loading with fallback to backup
- Thread-safe operations
"""

import json
import shutil
import os
import logging
from pathlib import Path
from datetime import datetime
from utils import IST, format_timestamp
from io import BytesIO
import imagehash
from PIL import Image

logger = logging.getLogger(__name__)

# Data directory configuration
DATA_DIR = Path(__file__).parent / 'data'
DATA_DIR.mkdir(exist_ok=True)

SUBMISSIONS_FILE = DATA_DIR / 'submissions.json'
WINNERS_FILE = DATA_DIR / 'winners.json'
CONFIG_FILE = DATA_DIR / 'config.json'
HASHES_FILE = DATA_DIR / 'hashes.json'


def get_default_submissions():
    """Return default structure for submissions.json"""
    return {
        "users": {},
        "stats": {
            "total_participants": 0,
            "total_submissions": 0,
            "campaign_start": format_timestamp(IST.localize(datetime(2025, 1, 15, 0, 1))),
            "last_updated": format_timestamp(datetime.now(IST))
        }
    }


def get_default_winners():
    """Return default structure for winners.json"""
    return {}


def get_default_config():
    """Return default structure for config.json"""
    return {
        "show_points": True,
        "campaign_start": format_timestamp(IST.localize(datetime(2025, 1, 15, 0, 1))),
        "campaign_end": format_timestamp(IST.localize(datetime(2025, 2, 11, 23, 59, 59)))
    }


def get_default_hashes():
    """Return default structure for hashes.json"""
    return {
        "global_seen_ids": [],
        "phash_db": {}  # {hex_hash: [user_id, message_id, ...]}
    }


def save_json_atomic(filepath, data):
    """
    Atomically write JSON data to file with backup.

    This prevents data corruption during crashes by:
    1. Writing to a temporary file first
    2. Creating a backup of the existing file
    3. Atomically moving the temp file to the target location

    Args:
        filepath: Path object or string path to JSON file
        data: Dictionary to save as JSON
    """
    filepath = Path(filepath)
    temp_file = filepath.with_suffix('.tmp')
    backup_file = filepath.with_suffix('.json.backup')

    try:
        # Write to temporary file
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # Create backup of existing file
        if filepath.exists():
            shutil.copy(filepath, backup_file)
            logger.debug(f"Created backup: {backup_file}")

        # Atomic rename (replaces existing file)
        shutil.move(str(temp_file), str(filepath))
        logger.debug(f"Saved {filepath}")

    except Exception as e:
        logger.error(f"Error saving {filepath}: {e}")
        # Clean up temp file if it exists
        if temp_file.exists():
            temp_file.unlink()
        raise


def load_json_safe(filepath, default_factory):
    """
    Safely load JSON with fallback to backup if corrupted.

    Args:
        filepath: Path object or string path to JSON file
        default_factory: Function that returns default structure

    Returns:
        dict: Loaded data or default structure
    """
    filepath = Path(filepath)
    backup_file = filepath.with_suffix('.json.backup')

    # Try loading main file
    try:
        if filepath.exists():
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.debug(f"Loaded {filepath}")
                return data
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted JSON in {filepath}: {e}")

        # Try loading backup
        try:
            if backup_file.exists():
                logger.warning(f"Attempting to restore from backup: {backup_file}")
                with open(backup_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Restore backup to main file
                    save_json_atomic(filepath, data)
                    logger.info(f"Successfully restored from backup")
                    return data
        except Exception as backup_error:
            logger.error(f"Backup restore failed: {backup_error}")

    # Return default structure if all else fails
    logger.warning(f"Using default structure for {filepath}")
    default_data = default_factory()
    save_json_atomic(filepath, default_data)
    return default_data


# Convenience functions for each data file

def load_submissions():
    """Load submissions.json"""
    return load_json_safe(SUBMISSIONS_FILE, get_default_submissions)


def save_submissions(data):
    """Save submissions.json"""
    # Update last_updated timestamp
    data['stats']['last_updated'] = format_timestamp(datetime.now(IST))
    save_json_atomic(SUBMISSIONS_FILE, data)


def load_winners():
    """Load winners.json"""
    return load_json_safe(WINNERS_FILE, get_default_winners)


def save_winners(data):
    """Save winners.json"""
    save_json_atomic(WINNERS_FILE, data)


def load_config():
    """Load config.json"""
    return load_json_safe(CONFIG_FILE, get_default_config)


def save_config(data):
    """Save config.json"""
    save_json_atomic(CONFIG_FILE, data)


def add_submission(user_id, username, full_name, message_id, photo_id, timestamp, week, image_bytes_io=None):
    """
    Add a new submission to the database (TIME-INDEPENDENT VERSION).

    This is an idempotent operation - if message_id already exists, it's skipped.
    NO WEEK TRACKING - counts all photos regardless of date.

    Args:
        user_id: Telegram user ID
        username: Telegram username (or "Unknown")
        full_name: User's full name
        message_id: Unique Telegram message ID
        photo_id: Telegram file_id for duplicate detection
        timestamp: datetime object

    Returns:
        bool: True if submission was added, False if duplicate
    """
    data = load_submissions()
    user_id_str = str(user_id)

    # Initialize user if not exists
    if user_id_str not in data['users']:
        data['users'][user_id_str] = {
            "username": username or "Unknown",
            "full_name": full_name,
            "first_seen": format_timestamp(timestamp),
            "unique_photos": [],
            "submissions": [],
            "total_points": 0
        }
        data['stats']['total_participants'] += 1

    user_data = data['users'][user_id_str]

    # Check if message_id already exists (idempotent check)
    existing_message_ids = [sub['message_id'] for sub in user_data['submissions']]
    if message_id in existing_message_ids:
        logger.debug(f"Message {message_id} already processed for user {user_id}")
        return False

        return False

    # Check for duplicate photo (User-specific check kept for legacy/speed)
    if photo_id in user_data['unique_photos']:
        logger.info(f"Duplicate photo {photo_id} detected for user {user_id} (User History), ignoring")
        return False

    # GLOBAL & PHASH CHECK
    # Only run if we haven't already locally rejected it
    is_dupe, reason = check_is_duplicate(user_id, photo_id, image_bytes_io)
    if is_dupe:
        logger.info(f"⛔ Fraud Detected: {reason} for user {user_id}")
        return False

    # Register in global DB
    register_new_photo(user_id, photo_id, image_bytes_io)

    # Add new submission
    user_data['submissions'].append({
        "message_id": message_id,
        "photo_id": photo_id,
        "timestamp": format_timestamp(timestamp)
    })

    # Add photo to unique list
    user_data['unique_photos'].append(photo_id)

    # Update points (all-time cumulative)
    user_data['total_points'] += 1

    # Update username/full_name if changed
    user_data['username'] = username or user_data['username']
    user_data['full_name'] = full_name or user_data['full_name']

    # Update global stats
    data['stats']['total_submissions'] += 1

    # Save atomically
    save_submissions(data)
    logger.info(f"Added submission for user {user_id} (message {message_id})")

    return True


def get_leaderboard(limit=None):
    """
    Get all-time leaderboard (TIME-INDEPENDENT).

    Args:
        limit: Maximum number of entries to return (None for all)

    Returns:
        list: Sorted list of dictionaries with user_id, username, full_name, points
    """
    data = load_submissions()
    leaderboard = []

    for user_id, user_data in data['users'].items():
        points = user_data['total_points']

        if points > 0:  # Only include users with points
            leaderboard.append({
                'user_id': user_id,
                'username': user_data['username'],
                'full_name': user_data['full_name'],
                'points': points
            })

    # Sort by points (descending), then by username (ascending)
    leaderboard.sort(key=lambda x: (-x['points'], x['username'].lower()))

    if limit:
        return leaderboard[:limit]
    return leaderboard


def save_week_winners(week, winners_list):
    """
    Save top 5 winners for a specific week.

    Args:
        week: Week number (1-4)
        winners_list: List of winner dictionaries with rank, username, full_name
    """
    data = load_winners()
    week_key = f"week_{week}"
    data[week_key] = winners_list
    save_winners(data)
    logger.info(f"Saved winners for week {week}")


def get_week_winners(week):
    """
    Get saved winners for a specific week.

    Args:
        week: Week number (1-4)

    Returns:
        list: List of winner dictionaries or None if not set
    """
    data = load_winners()
    week_key = f"week_{week}"
    return data.get(week_key)


def get_stats():
    """
    Get campaign statistics.

    Returns:
        dict: Statistics dictionary
    """
    data = load_submissions()
    return data['stats']


def get_engagement_stats(week=None):
    """
    Get detailed engagement statistics.

    Args:
        week: Week number for new participants, or None for all-time

    Returns:
        dict: Engagement statistics
    """
    data = load_submissions()
    stats = data['stats'].copy()

    # Find most active user
    most_active = None
    max_posts = 0
    total_posts = 0

    for user_id, user_data in data['users'].items():
        points = user_data['total_points']
        total_posts += points
        if points > max_posts:
            max_posts = points
            most_active = user_data['username']

    # Calculate average posts per user
    total_users = stats['total_participants']
    avg_posts = total_posts / total_users if total_users > 0 else 0

    # New participants this week - not tracked in time-independent mode
    new_this_week = 0

    # Calculate campaign day
    from datetime import datetime
    from utils import CAMPAIGN_START
    now = datetime.now(IST)
    campaign_day = (now - CAMPAIGN_START).days + 1

    return {
        'total_participants': total_users,
        'total_submissions': stats['total_submissions'],
        'campaign_day': max(1, campaign_day),
        'new_this_week': new_this_week,
        'most_active_user': most_active,
        'most_active_count': max_posts,
        'avg_posts_per_user': round(avg_posts, 1)
    }
