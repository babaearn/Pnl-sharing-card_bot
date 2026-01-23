"""
PostgreSQL Database Layer for PnL Leaderboard Bot

Provides async database operations using asyncpg.
All operations are crash-safe, idempotent, and use transactions.
"""

import os
import asyncpg
import logging
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from utils import IST

logger = logging.getLogger(__name__)

# Database connection pool (initialized on startup)
_pool: Optional[asyncpg.Pool] = None


async def init_db() -> asyncpg.Pool:
    """
    Initialize database connection pool and create tables if not exist.

    Returns:
        asyncpg.Pool: Database connection pool

    Raises:
        ValueError: If DATABASE_URL is not set
        Exception: If database connection fails
    """
    global _pool

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("❌ DATABASE_URL environment variable is not set!")
        raise ValueError(
            "DATABASE_URL is required. Add PostgreSQL plugin in Railway "
            "and add Variable Reference to your service."
        )

    # Mask DATABASE_URL in logs for security
    masked_url = database_url[:15] + "***" + database_url[-10:] if len(database_url) > 25 else "***"
    logger.info(f"🗄️ Connecting to PostgreSQL: {masked_url}")

    try:
        # Create connection pool
        _pool = await asyncpg.create_pool(
            database_url,
            min_size=2,
            max_size=10,
            command_timeout=60
        )

        logger.info("✅ Database connection pool created")

        # Create tables
        await create_tables()

        # Initialize settings if empty
        await initialize_settings()

        logger.info("✅ Database initialized successfully")
        return _pool

    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise


async def create_tables():
    """Create all required tables if they don't exist."""
    async with _pool.acquire() as conn:
        # Participants table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS participants (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                identity_key TEXT UNIQUE NOT NULL,
                tg_user_id BIGINT NULL,
                username TEXT NULL,
                display_name TEXT NOT NULL,
                points INT NOT NULL DEFAULT 0,
                first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        ''')

        # Submissions table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY,
                participant_id INT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                photo_file_id TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('topic', 'forward', 'manual')),
                tg_message_id BIGINT NULL,
                week_number INT NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (participant_id, photo_file_id)
            )
        ''')

        # Add week_number column if it doesn't exist (migration)
        await conn.execute('''
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='submissions' AND column_name='week_number'
                ) THEN
                    ALTER TABLE submissions ADD COLUMN week_number INT NOT NULL DEFAULT 1;
                END IF;
            END $$;
        ''')

        # Create index on photo_file_id for faster duplicate checks
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_submissions_photo_file_id
            ON submissions(photo_file_id)
        ''')

        # Adjustments table (manual point additions/removals)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS adjustments (
                id SERIAL PRIMARY KEY,
                participant_id INT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                delta INT NOT NULL,
                admin_tg_user_id BIGINT NOT NULL,
                note TEXT NULL,
                week_number INT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        ''')

        # Add week_number column if it doesn't exist (migration)
        await conn.execute('''
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='adjustments' AND column_name='week_number'
                ) THEN
                    ALTER TABLE adjustments ADD COLUMN week_number INT NULL;
                END IF;
            END $$;
        ''')

        # Settings table (key-value store)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')

        # Winners table (weekly winner storage)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS winners (
                week INT NOT NULL,
                rank INT NOT NULL,
                participant_id INT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
                points_at_time INT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (week, rank)
            )
        ''')

        # Photo Hashes table (for fraud detection)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS photo_hashes (
                id SERIAL PRIMARY KEY,
                participant_id INT REFERENCES participants(id) ON DELETE SET NULL,
                phash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        ''')

        # Deleted submissions backup (for undo functionality)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS deleted_submissions (
                id SERIAL PRIMARY KEY,
                original_id INT NOT NULL,
                participant_id INT NOT NULL,
                photo_file_id TEXT NOT NULL,
                source TEXT NOT NULL,
                tg_message_id BIGINT NULL,
                week_number INT NOT NULL,
                original_created_at TIMESTAMPTZ NOT NULL,
                deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                deleted_by_admin BIGINT NOT NULL
            )
        ''')

        # Deleted adjustments backup (for undo functionality)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS deleted_adjustments (
                id SERIAL PRIMARY KEY,
                original_id INT NOT NULL,
                participant_id INT NOT NULL,
                delta INT NOT NULL,
                admin_tg_user_id BIGINT NOT NULL,
                note TEXT NULL,
                week_number INT NULL,
                original_created_at TIMESTAMPTZ NOT NULL,
                deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                deleted_by_admin BIGINT NOT NULL
            )
        ''')

        logger.info("✅ All tables created/verified")


async def initialize_settings():
    """Initialize default settings if not already present."""
    defaults = {
        'show_points': 'true',
        'next_code_number': '1',
        'since_reset_total_submissions': '0',
        'since_reset_duplicates': '0',
        'since_reset_manual_adjustments': '0',
        'reset_at': datetime.now(IST).isoformat(),
        'current_week': '1',
        'week_label': 'Week 1'
    }

    async with _pool.acquire() as conn:
        for key, value in defaults.items():
            # Insert if not exists (ON CONFLICT DO NOTHING)
            await conn.execute('''
                INSERT INTO settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO NOTHING
            ''', key, value)

    logger.info("✅ Settings initialized")


async def close_db():
    """Close database connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        logger.info("✅ Database connection pool closed")


# ============================================================================
# SETTINGS OPERATIONS
# ============================================================================

async def get_setting(key: str, default: str = None) -> Optional[str]:
    """Get a setting value."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow('SELECT value FROM settings WHERE key = $1', key)
        return row['value'] if row else default


async def set_setting(key: str, value: str):
    """Set a setting value."""
    async with _pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO settings (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = $2
        ''', key, value)


async def get_show_points() -> bool:
    """Check if points should be shown in public leaderboard."""
    value = await get_setting('show_points', 'true')
    return value.lower() == 'true'


async def set_show_points(show: bool):
    """Set whether points should be shown in public leaderboard."""
    await set_setting('show_points', 'true' if show else 'false')


# ============================================================================
# PARTICIPANT OPERATIONS
# ============================================================================

def normalize_name(name: str) -> str:
    """Normalize a display name for identity key generation."""
    return name.lower().strip().replace('  ', ' ')


async def get_next_code() -> str:
    """Get next available participant code and increment counter."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Get current number
            row = await conn.fetchrow('SELECT value FROM settings WHERE key = $1', 'next_code_number')
            current = int(row['value']) if row else 1

            # Format as #01, #02, etc.
            code = f"#{current:02d}"

            # Increment for next time
            await conn.execute('''
                UPDATE settings SET value = $1 WHERE key = $2
            ''', str(current + 1), 'next_code_number')

            return code


async def get_or_create_participant(
    tg_user_id: Optional[int],
    username: Optional[str],
    full_name: str
) -> int:
    """
    Get existing participant or create new one.

    Uses identity_key logic:
    - If tg_user_id exists: identity_key = 'tg:<id>'
    - Otherwise: identity_key = 'name:<normalized_display_name>'

    Returns:
        int: participant_id
    """
    # Determine identity key
    if tg_user_id:
        identity_key = f"tg:{tg_user_id}"
    else:
        identity_key = f"name:{normalize_name(full_name)}"

    # Determine display name (username takes precedence)
    display_name = f"@{username}" if username else full_name

    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Try to find existing participant
            row = await conn.fetchrow('''
                SELECT id FROM participants WHERE identity_key = $1
            ''', identity_key)

            if row:
                # Update username/display_name if changed
                await conn.execute('''
                    UPDATE participants
                    SET username = $1, display_name = $2, updated_at = now()
                    WHERE identity_key = $3
                ''', username, display_name, identity_key)

                return row['id']

            # Create new participant with next code
            code = await get_next_code()

            participant_id = await conn.fetchval('''
                INSERT INTO participants (
                    code, identity_key, tg_user_id, username, display_name, first_seen
                )
                VALUES ($1, $2, $3, $4, $5, now())
                RETURNING id
            ''', code, identity_key, tg_user_id, username, display_name)

            logger.info(f"✅ New participant created: {code} - {display_name} (ID: {participant_id})")
            return participant_id


# ============================================================================
# SUBMISSION OPERATIONS
# ============================================================================

async def add_submission(
    participant_id: int,
    photo_file_id: str,
    source: str,
    tg_message_id: Optional[int] = None
) -> Tuple[bool, str]:
    """
    Add a submission (idempotent via UNIQUE constraint).

    Returns:
        Tuple[bool, str]: (success, message)
            - (True, "added") if new submission added
            - (False, "duplicate") if submission already exists
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            try:
                # Get current week number
                current_week = int(await conn.fetchval(
                    "SELECT value FROM settings WHERE key = 'current_week'"
                ) or '1')

                # Try to insert submission with week_number
                await conn.execute('''
                    INSERT INTO submissions (participant_id, photo_file_id, source, tg_message_id, week_number)
                    VALUES ($1, $2, $3, $4, $5)
                ''', participant_id, photo_file_id, source, tg_message_id, current_week)

                # Increment points
                await conn.execute('''
                    UPDATE participants SET points = points + 1, updated_at = now()
                    WHERE id = $1
                ''', participant_id)

                # Increment since_reset counter
                await conn.execute('''
                    UPDATE settings SET value = (value::int + 1)::text
                    WHERE key = 'since_reset_total_submissions'
                ''')

                return (True, "added")

            except asyncpg.UniqueViolationError:
                # Duplicate submission - increment duplicate counter
                await conn.execute('''
                    UPDATE settings SET value = (value::int + 1)::text
                    WHERE key = 'since_reset_duplicates'
                ''')

                return (False, "duplicate")


async def delete_participant(code: str) -> Tuple[bool, str]:
    """
    Delete a participant and all their submissions by code.

    Args:
        code: Participant code (e.g., '#01')

    Returns:
        Tuple of (success: bool, message: str)
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Check if participant exists
            participant = await conn.fetchrow('''
                SELECT id, display_name, points FROM participants WHERE code = $1
            ''', code)

            if not participant:
                return (False, f"Participant {code} not found")

            participant_id = participant['id']
            display_name = participant['display_name']
            points = participant['points']

            # Delete submissions (cascades due to FK)
            deleted_submissions = await conn.execute('''
                DELETE FROM submissions WHERE participant_id = $1
            ''', participant_id)

            # Delete adjustments (cascades due to FK)
            await conn.execute('''
                DELETE FROM adjustments WHERE participant_id = $1
            ''', participant_id)

            # Delete photo hashes (cascades due to FK)
            await conn.execute('''
                DELETE FROM photo_hashes WHERE participant_id = $1
            ''', participant_id)

            # Delete participant
            await conn.execute('''
                DELETE FROM participants WHERE id = $1
            ''', participant_id)

            logger.info(f"🗑️ Deleted participant {code} ({display_name}) with {points} points")
            return (True, f"Deleted {code} ({display_name}) - {points} pts removed")


async def delete_week_data(week_number: int, admin_id: int) -> Tuple[bool, str, int, int]:
    """
    Delete all submissions and adjustments for a specific week.
    Backs up data to deleted_* tables before deletion (for undo).
    Does NOT delete participants - only their week-specific data.

    Args:
        week_number: Week number to delete data from
        admin_id: Admin user ID who performed the deletion

    Returns:
        Tuple[bool, str, int, int]: (success, message, submissions_deleted, adjustments_deleted)
    """
    if week_number < 1:
        return (False, "Week number must be 1 or greater", 0, 0)

    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Backup submissions to deleted_submissions before deleting
            await conn.execute('''
                INSERT INTO deleted_submissions
                    (original_id, participant_id, photo_file_id, source, tg_message_id,
                     week_number, original_created_at, deleted_by_admin)
                SELECT id, participant_id, photo_file_id, source, tg_message_id,
                       week_number, created_at, $2
                FROM submissions
                WHERE week_number = $1
            ''', week_number, admin_id)

            # Backup adjustments to deleted_adjustments before deleting
            await conn.execute('''
                INSERT INTO deleted_adjustments
                    (original_id, participant_id, delta, admin_tg_user_id, note,
                     week_number, original_created_at, deleted_by_admin)
                SELECT id, participant_id, delta, admin_tg_user_id, note,
                       week_number, created_at, $2
                FROM adjustments
                WHERE week_number = $1
            ''', week_number, admin_id)

            # Count and delete submissions for this week
            submissions_count = await conn.fetchval('''
                SELECT COUNT(*) FROM submissions WHERE week_number = $1
            ''', week_number)

            await conn.execute('''
                DELETE FROM submissions WHERE week_number = $1
            ''', week_number)

            # Count and delete adjustments for this week
            adjustments_count = await conn.fetchval('''
                SELECT COUNT(*) FROM adjustments WHERE week_number = $1
            ''', week_number)

            await conn.execute('''
                DELETE FROM adjustments WHERE week_number = $1
            ''', week_number)

            logger.warning(
                f"🗑️ Deleted Week {week_number} data (backed up): "
                f"{submissions_count} submissions, {adjustments_count} adjustments"
            )

            message = (
                f"Week {week_number} data deleted:\n"
                f"• {submissions_count} submissions removed\n"
                f"• {adjustments_count} adjustments removed\n"
                f"Participants remain intact.\n"
                f"Use /undodata {week_number} to restore."
            )

            return (True, message, submissions_count or 0, adjustments_count or 0)


async def restore_week_data(week_number: int) -> Tuple[bool, str, int, int]:
    """
    Restore submissions and adjustments for a specific week from backup.
    Undoes a previous /removedata operation.

    Args:
        week_number: Week number to restore data for

    Returns:
        Tuple[bool, str, int, int]: (success, message, submissions_restored, adjustments_restored)
    """
    if week_number < 1:
        return (False, "Week number must be 1 or greater", 0, 0)

    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Check if there's backup data for this week
            submissions_in_backup = await conn.fetchval('''
                SELECT COUNT(*) FROM deleted_submissions WHERE week_number = $1
            ''', week_number)

            adjustments_in_backup = await conn.fetchval('''
                SELECT COUNT(*) FROM deleted_adjustments WHERE week_number = $1
            ''', week_number)

            if submissions_in_backup == 0 and adjustments_in_backup == 0:
                return (False, f"No backup data found for Week {week_number}", 0, 0)

            # Restore submissions from backup
            await conn.execute('''
                INSERT INTO submissions
                    (participant_id, photo_file_id, source, tg_message_id, week_number, created_at)
                SELECT participant_id, photo_file_id, source, tg_message_id, week_number, original_created_at
                FROM deleted_submissions
                WHERE week_number = $1
                ON CONFLICT (participant_id, photo_file_id) DO NOTHING
            ''', week_number)

            # Restore adjustments from backup
            await conn.execute('''
                INSERT INTO adjustments
                    (participant_id, delta, admin_tg_user_id, note, week_number, created_at)
                SELECT participant_id, delta, admin_tg_user_id, note, week_number, original_created_at
                FROM deleted_adjustments
                WHERE week_number = $1
            ''', week_number)

            # Clean up backup data after successful restore
            await conn.execute('''
                DELETE FROM deleted_submissions WHERE week_number = $1
            ''', week_number)

            await conn.execute('''
                DELETE FROM deleted_adjustments WHERE week_number = $1
            ''', week_number)

            logger.info(
                f"♻️ Restored Week {week_number} data: "
                f"{submissions_in_backup} submissions, {adjustments_in_backup} adjustments"
            )

            message = (
                f"Week {week_number} data restored:\n"
                f"• {submissions_in_backup} submissions restored\n"
                f"• {adjustments_in_backup} adjustments restored\n"
                f"Data successfully recovered!"
            )

            return (True, message, submissions_in_backup or 0, adjustments_in_backup or 0)


async def recalculate_cumulative_points() -> Tuple[int, str]:
    """
    Recalculate cumulative points for all participants from submissions.
    Use this after restoring data or if points get out of sync.

    Returns:
        Tuple[int, str]: (participants_updated, summary_message)
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Count submissions for each participant (all weeks)
            submission_counts = await conn.fetch('''
                SELECT participant_id, COUNT(*) as total_submissions
                FROM submissions
                GROUP BY participant_id
            ''')

            participants_updated = 0
            updates = []

            for row in submission_counts:
                participant_id = row['participant_id']
                correct_points = row['total_submissions']

                # Get current points and participant info
                participant = await conn.fetchrow('''
                    SELECT code, display_name, points FROM participants WHERE id = $1
                ''', participant_id)

                if participant:
                    old_points = participant['points']

                    # Update if points don't match
                    if old_points != correct_points:
                        await conn.execute('''
                            UPDATE participants SET points = $1, updated_at = now()
                            WHERE id = $2
                        ''', correct_points, participant_id)

                        participants_updated += 1
                        updates.append(
                            f"{participant['code']} {participant['display_name']}: "
                            f"{old_points} → {correct_points} pts"
                        )

            logger.info(f"♻️ Recalculated points for {participants_updated} participants")

            if participants_updated == 0:
                return (0, "All cumulative points are correct! No updates needed.")
            else:
                summary = "\n".join(updates[:10])  # Show first 10
                if len(updates) > 10:
                    summary += f"\n... and {len(updates) - 10} more"

                return (participants_updated, summary)


# ============================================================================
# LEADERBOARD OPERATIONS
# ============================================================================

async def get_leaderboard(limit: int = 5, week: Optional[int] = None) -> List[Dict]:
    """
    Get top participants sorted by points.

    Args:
        limit: Maximum number of participants to return
        week: Week number to filter by (None = cumulative/all-time)

    Returns list of dicts with: code, display_name, tg_user_id, points
    """
    async with _pool.acquire() as conn:
        if week is None:
            # Cumulative (all-time) points
            rows = await conn.fetch('''
                SELECT code, display_name, tg_user_id, username, points
                FROM participants
                WHERE points > 0
                ORDER BY points DESC, first_seen ASC
                LIMIT $1
            ''', limit)
        else:
            # Weekly points (count submissions + adjustments for specific week)
            rows = await conn.fetch('''
                SELECT
                    p.code,
                    p.display_name,
                    p.tg_user_id,
                    p.username,
                    (COUNT(s.id) + COALESCE(SUM(a.delta), 0))::int as points
                FROM participants p
                LEFT JOIN submissions s ON p.id = s.participant_id AND s.week_number = $1
                LEFT JOIN adjustments a ON p.id = a.participant_id AND a.week_number = $1
                GROUP BY p.id, p.code, p.display_name, p.tg_user_id, p.username
                HAVING (COUNT(s.id) + COALESCE(SUM(a.delta), 0)) > 0
                ORDER BY points DESC, p.first_seen ASC
                LIMIT $2
            ''', week, limit)

        return [dict(row) for row in rows]


async def get_full_rankerinfo(limit: Optional[int] = 10, week: Optional[int] = None) -> List[Dict]:
    """
    Get participants with full details for admin verification.

    Args:
        limit: Maximum number of participants to return (None = all)
        week: Week number to filter by (None = cumulative/all-time)

    Returns:
        List of dicts with: code, display_name, tg_user_id, points
    """
    async with _pool.acquire() as conn:
        if week is None:
            # Cumulative (all-time) points
            if limit is None:
                rows = await conn.fetch('''
                    SELECT id, code, display_name, tg_user_id, username, points
                    FROM participants
                    WHERE points > 0
                    ORDER BY points DESC, first_seen ASC
                ''')
            else:
                rows = await conn.fetch('''
                    SELECT id, code, display_name, tg_user_id, username, points
                    FROM participants
                    WHERE points > 0
                    ORDER BY points DESC, first_seen ASC
                    LIMIT $1
                ''', limit)
        else:
            # Weekly points (count submissions + adjustments for specific week)
            if limit is None:
                rows = await conn.fetch('''
                    SELECT
                        p.id,
                        p.code,
                        p.display_name,
                        p.tg_user_id,
                        p.username,
                        (COUNT(s.id) + COALESCE(SUM(a.delta), 0))::int as points
                    FROM participants p
                    LEFT JOIN submissions s ON p.id = s.participant_id AND s.week_number = $1
                    LEFT JOIN adjustments a ON p.id = a.participant_id AND a.week_number = $1
                    GROUP BY p.id, p.code, p.display_name, p.tg_user_id, p.username
                    HAVING (COUNT(s.id) + COALESCE(SUM(a.delta), 0)) > 0
                    ORDER BY points DESC, p.first_seen ASC
                ''', week)
            else:
                rows = await conn.fetch('''
                    SELECT
                        p.id,
                        p.code,
                        p.display_name,
                        p.tg_user_id,
                        p.username,
                        (COUNT(s.id) + COALESCE(SUM(a.delta), 0))::int as points
                    FROM participants p
                    LEFT JOIN submissions s ON p.id = s.participant_id AND s.week_number = $1
                    LEFT JOIN adjustments a ON p.id = a.participant_id AND a.week_number = $1
                    GROUP BY p.id, p.code, p.display_name, p.tg_user_id, p.username
                    HAVING (COUNT(s.id) + COALESCE(SUM(a.delta), 0)) > 0
                    ORDER BY points DESC, p.first_seen ASC
                    LIMIT $2
                ''', week, limit)

        return [dict(row) for row in rows]


async def get_total_participants() -> int:
    """Get total number of participants."""
    async with _pool.acquire() as conn:
        result = await conn.fetchval('SELECT COUNT(*) FROM participants')
        return result or 0


async def get_total_submissions() -> int:
    """Get total number of submissions."""
    async with _pool.acquire() as conn:
        result = await conn.fetchval('SELECT COUNT(*) FROM submissions')
        return result or 0


async def get_current_week() -> int:
    """Get current week number."""
    async with _pool.acquire() as conn:
        result = await conn.fetchval("SELECT value FROM settings WHERE key = 'current_week'")
        return int(result) if result else 1


async def get_week_label() -> str:
    """Get current week label."""
    async with _pool.acquire() as conn:
        result = await conn.fetchval("SELECT value FROM settings WHERE key = 'week_label'")
        return result or "Week 1"


async def set_current_week(week_number: int, label: Optional[str] = None) -> Tuple[int, str]:
    """
    Manually set the current week number and label.
    Use with caution - this overrides the week counter.

    Args:
        week_number: Week number to set (must be >= 1)
        label: Optional custom label (defaults to "Week N")

    Returns:
        Tuple[int, str]: (week_number, label)
    """
    if week_number < 1:
        raise ValueError("Week number must be >= 1")

    if label is None:
        label = f"Week {week_number}"

    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute('''
                UPDATE settings SET value = $1 WHERE key = 'current_week'
            ''', str(week_number))

            await conn.execute('''
                UPDATE settings SET value = $1 WHERE key = 'week_label'
            ''', label)

            logger.info(f"⚙️ Manually set week to: Week {week_number} ({label})")
            return (week_number, label)


async def start_new_week(label: Optional[str] = None) -> Tuple[str, str, int, int]:
    """
    Start a new week by incrementing the week counter.
    Does NOT delete any data - all history is preserved.

    Args:
        label: Optional custom label for the new week (e.g., "week2", "week 3")

    Returns:
        Tuple[str, str, int, int]: (old_label, new_label, old_week, new_week)
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Get current week and label
            old_week = await conn.fetchval("SELECT value FROM settings WHERE key = 'current_week'")
            old_week = int(old_week) if old_week else 1

            old_label = await conn.fetchval("SELECT value FROM settings WHERE key = 'week_label'")
            old_label = old_label or f"Week {old_week}"

            # Increment to new week
            new_week = old_week + 1

            # Set new label (use provided label or default)
            if label:
                new_label = label
            else:
                new_label = f"Week {new_week}"

            await conn.execute('''
                UPDATE settings SET value = $1 WHERE key = 'current_week'
            ''', str(new_week))

            await conn.execute('''
                UPDATE settings SET value = $1 WHERE key = 'week_label'
            ''', new_label)

            logger.info(f"📅 Started new week: {old_label} → {new_label} (Week {old_week} → Week {new_week})")
            return (old_label, new_label, old_week, new_week)


# ============================================================================
# ADJUSTMENT OPERATIONS
# ============================================================================

async def add_adjustment(
    participant_code: str,
    delta: int,
    admin_tg_user_id: int,
    note: Optional[str] = None,
    week_number: Optional[int] = None
) -> Tuple[bool, str, Optional[int]]:
    """
    Add manual point adjustment.

    Args:
        participant_code: Participant code (e.g., '#01')
        delta: Points to add (positive) or remove (negative)
        admin_tg_user_id: Admin's Telegram user ID
        note: Optional note about the adjustment
        week_number: Optional week number for week-specific adjustment

    Returns:
        Tuple[bool, str, Optional[int]]: (success, message, new_points)
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Find participant by code
            row = await conn.fetchrow('''
                SELECT id, points, display_name FROM participants WHERE code = $1
            ''', participant_code)

            if not row:
                return (False, f"Participant {participant_code} not found", None)

            participant_id = row['id']
            current_points = row['points']
            display_name = row['display_name']

            if week_number is None:
                # Cumulative adjustment - modify cumulative points
                new_points = max(0, current_points + delta)

                # Update points
                await conn.execute('''
                    UPDATE participants SET points = $1, updated_at = now()
                    WHERE id = $2
                ''', new_points, participant_id)

                # Record adjustment (no week_number)
                await conn.execute('''
                    INSERT INTO adjustments (participant_id, delta, admin_tg_user_id, note, week_number)
                    VALUES ($1, $2, $3, $4, NULL)
                ''', participant_id, delta, admin_tg_user_id, note)

                logger.info(f"✅ Cumulative adjustment: {participant_code} {delta:+d} by admin {admin_tg_user_id}")
                message = f"{display_name}: {current_points} → {new_points} (cumulative)"
            else:
                # Week-specific adjustment - only records for that week (doesn't modify cumulative)
                # Record adjustment with week_number
                await conn.execute('''
                    INSERT INTO adjustments (participant_id, delta, admin_tg_user_id, note, week_number)
                    VALUES ($1, $2, $3, $4, $5)
                ''', participant_id, delta, admin_tg_user_id, note, week_number)

                logger.info(f"✅ Week {week_number} adjustment: {participant_code} {delta:+d} by admin {admin_tg_user_id}")
                message = f"{display_name}: {delta:+d} pts for week {week_number}"
                new_points = None  # No cumulative change

            # Increment manual adjustments counter
            await conn.execute('''
                UPDATE settings SET value = (value::int + 1)::text
                WHERE key = 'since_reset_manual_adjustments'
            ''')

            return (True, message, new_points)


# ============================================================================
# STATS OPERATIONS
# ============================================================================

async def get_stats() -> Dict:
    """Get engagement statistics since last reset."""
    async with _pool.acquire() as conn:
        # Basic counters
        total_participants = await conn.fetchval('SELECT COUNT(*) FROM participants WHERE points > 0')
        total_submissions = await conn.fetchval('SELECT value FROM settings WHERE key = $1', 'since_reset_total_submissions')
        duplicates = await conn.fetchval('SELECT value FROM settings WHERE key = $1', 'since_reset_duplicates')
        adjustments = await conn.fetchval('SELECT value FROM settings WHERE key = $1', 'since_reset_manual_adjustments')
        reset_at = await conn.fetchval('SELECT value FROM settings WHERE key = $1', 'reset_at')

        # Most active participant
        most_active_row = await conn.fetchrow('''
            SELECT display_name, points
            FROM participants
            WHERE points > 0
            ORDER BY points DESC
            LIMIT 1
        ''')

        most_active = most_active_row['display_name'] if most_active_row else "None"
        max_points = most_active_row['points'] if most_active_row else 0

        # Average points
        avg_points = 0
        if total_participants > 0:
            avg_result = await conn.fetchval('SELECT AVG(points) FROM participants WHERE points > 0')
            avg_points = float(avg_result) if avg_result else 0

        return {
            'total_participants': total_participants,
            'total_submissions': int(total_submissions or 0),
            'duplicates': int(duplicates or 0),
            'manual_adjustments': int(adjustments or 0),
            'most_active': most_active,
            'max_points': max_points,
            'avg_points': avg_points,
            'reset_at': reset_at
        }


# ============================================================================
# WINNERS OPERATIONS
# ============================================================================

async def save_winners(week: int, winners: List[Dict]):
    """Save weekly winners (based on current all-time leaderboard)."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Delete existing winners for this week
            await conn.execute('DELETE FROM winners WHERE week = $1', week)

            # Insert new winners
            for winner in winners:
                await conn.execute('''
                    INSERT INTO winners (week, rank, participant_id, points_at_time)
                    VALUES ($1, $2, $3, $4)
                ''', week, winner['rank'], winner['participant_id'], winner['points'])

            logger.info(f"✅ Saved {len(winners)} winners for week {week}")


async def get_winners(week: int) -> List[Dict]:
    """Get winners for a specific week."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT w.rank, p.code, p.display_name, w.points_at_time
            FROM winners w
            JOIN participants p ON w.participant_id = p.id
            WHERE w.week = $1
            ORDER BY w.rank ASC
        ''', week)

        return [dict(row) for row in rows]


# ============================================================================
# RESET OPERATION
# ============================================================================

async def reset_all_data():
    """Reset all data (DANGEROUS - creates fresh start)."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Clear all tables
            await conn.execute('DELETE FROM winners')
            await conn.execute('DELETE FROM adjustments')
            await conn.execute('DELETE FROM submissions')
            await conn.execute('DELETE FROM participants')

            # Reset settings counters
            await conn.execute("UPDATE settings SET value = '1' WHERE key = 'next_code_number'")
            await conn.execute("UPDATE settings SET value = '0' WHERE key = 'since_reset_total_submissions'")
            await conn.execute("UPDATE settings SET value = '0' WHERE key = 'since_reset_duplicates'")
            await conn.execute("UPDATE settings SET value = '0' WHERE key = 'since_reset_manual_adjustments'")
            await conn.execute("UPDATE settings SET value = $1 WHERE key = 'reset_at'", datetime.now(IST).isoformat())

            logger.warning("⚠️ ALL DATA RESET - Starting fresh from #01")


# ============================================================================
# HEALTH CHECK
# ============================================================================

async def health_check() -> Dict[str, str]:
    """
    Perform database health check.

    Returns dict with status of each component.
    """
    results = {}

    try:
        async with _pool.acquire() as conn:
            # Test connection
            await conn.fetchval('SELECT 1')
            results['connection'] = 'OK'

            # Test tables exist
            tables = ['participants', 'submissions', 'adjustments', 'settings', 'winners']
            for table in tables:
                count = await conn.fetchval(f'SELECT COUNT(*) FROM {table}')
                results[f'table_{table}'] = f'OK({count})'

            # Test leaderboard query
            await conn.fetch('SELECT * FROM participants ORDER BY points DESC LIMIT 5')
            results['leaderboard_query'] = 'OK'

            # Test settings
            next_code = await conn.fetchval('SELECT value FROM settings WHERE key = $1', 'next_code_number')
            results['next_code_number'] = next_code or 'ERROR'

    except Exception as e:
        results['error'] = str(e)

    return results


async def test_transaction() -> Tuple[bool, float]:
    """
    Test database transaction (rollback test).

    Returns:
        Tuple[bool, float]: (success, time_taken_ms)
    """
    import time
    start = time.time()

    try:
        async with _pool.acquire() as conn:
            async with conn.transaction():
                # Insert test participant
                test_id = await conn.fetchval('''
                    INSERT INTO participants (code, identity_key, display_name)
                    VALUES ('TEST', 'test:rollback', 'Test User')
                    RETURNING id
                ''')

                # Insert test submission
                await conn.execute('''
                    INSERT INTO submissions (participant_id, photo_file_id, source)
                    VALUES ($1, 'test_photo_id', 'manual')
                ''', test_id)

                # Select back
                row = await conn.fetchrow('SELECT * FROM participants WHERE id = $1', test_id)

                if not row:
                    raise Exception("Test row not found")

                # Rollback by raising exception
                raise Exception("ROLLBACK_TEST")

    except Exception as e:
        if "ROLLBACK_TEST" in str(e):
            # Expected - transaction rolled back successfully
            elapsed = (time.time() - start) * 1000  # Convert to ms
            return (True, elapsed)
        else:
            # Unexpected error
            return (False, 0)

    return (False, 0)


# ============================================================================
# FRAUD DETECTION OPERATIONS
# ============================================================================

async def get_all_hashes() -> List[str]:
    """Get all perceptual hashes for fraud detection."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch('SELECT phash FROM photo_hashes')
        return [row['phash'] for row in rows]


async def add_phash(participant_id: int, phash: str):
    """Add perceptual hash for a submission."""
    async with _pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO photo_hashes (participant_id, phash)
            VALUES ($1, $2)
        ''', participant_id, phash)
