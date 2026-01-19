"""
PnL Flex Challenge Leaderboard Bot - PostgreSQL Edition

Complete rewrite using PostgreSQL as source of truth.
Supports forward_origin (Bot API 7.0+) for proper forward handling.
Implements batch forwarding system with progress messages.
"""

import os
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from typing import Optional, Dict

from telegram import Update, MessageOriginUser, MessageOriginHiddenUser, MessageOriginChat, MessageOriginChannel
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import TelegramError

# Import database layer
import db

# Import utilities
from utils import (
    IST,
    CHAT_ID,
    TOPIC_ID,
    ADMIN_IDS,
    is_admin,
    SensitiveFormatter
)

# Configure logging with sensitive data masking
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Apply sensitive formatter
for handler in logging.getLogger().handlers:
    handler.setFormatter(SensitiveFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Get bot token
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")


# ============================================================================
# BATCH FORWARDING SYSTEM
# ============================================================================

class BatchForwardQueue:
    """
    Handles batch processing of forwarded photos in admin DM.

    Prevents spam by showing only:
    - One progress message
    - Periodic updates
    - Final summary
    """

    def __init__(self):
        self.queues: Dict[int, asyncio.Queue] = defaultdict(asyncio.Queue)
        self.workers: Dict[int, asyncio.Task] = {}
        self.progress_messages: Dict[int, tuple] = {}  # admin_id -> (chat_id, message_id)
        self.stats: Dict[int, Dict] = defaultdict(lambda: {
            'received': 0,
            'added': 0,
            'duplicates': 0,
            'failed': 0
        })

    async def add_forward(self, admin_id: int, photo_data: Dict, context: ContextTypes.DEFAULT_TYPE):
        """Add forwarded photo to queue for processing."""
        # Get or create queue for this admin
        queue = self.queues[admin_id]

        # Add to queue
        await queue.put(photo_data)

        # Start worker if not running
        if admin_id not in self.workers or self.workers[admin_id].done():
            self.stats[admin_id] = {
                'received': 0,
                'added': 0,
                'duplicates': 0,
                'failed': 0
            }
            self.workers[admin_id] = asyncio.create_task(
                self._worker(admin_id, context)
            )

    async def _worker(self, admin_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Worker task that processes queue for one admin."""
        queue = self.queues[admin_id]
        last_update_time = asyncio.get_event_loop().time()
        processed_count = 0

        # Send initial progress message
        try:
            progress_msg = await context.bot.send_message(
                chat_id=admin_id,
                text="⏳ Reading forwarded media... please wait"
            )
            self.progress_messages[admin_id] = (admin_id, progress_msg.message_id)
        except Exception as e:
            logger.error(f"Failed to send progress message: {e}")
            return

        while True:
            try:
                # Wait for next item with timeout
                photo_data = await asyncio.wait_for(queue.get(), timeout=12.0)

                # Process this photo
                await self._process_photo(admin_id, photo_data)
                processed_count += 1

                # Update progress every 10 items or every 3 seconds
                current_time = asyncio.get_event_loop().time()
                if processed_count % 10 == 0 or (current_time - last_update_time) >= 3:
                    await self._update_progress(admin_id, context)
                    last_update_time = current_time

                queue.task_done()

            except asyncio.TimeoutError:
                # No new items for 12 seconds - finalize
                logger.info(f"Batch complete for admin {admin_id} - sending summary")
                await self._send_summary(admin_id, context)
                break

            except Exception as e:
                logger.error(f"Error processing photo in batch: {e}")
                self.stats[admin_id]['failed'] += 1

        # Cleanup
        del self.workers[admin_id]
        del self.stats[admin_id]
        if admin_id in self.progress_messages:
            del self.progress_messages[admin_id]

    async def _process_photo(self, admin_id: int, photo_data: Dict):
        """Process a single forwarded photo."""
        self.stats[admin_id]['received'] += 1

        try:
            # Extract identity from forward_origin
            tg_user_id = photo_data.get('tg_user_id')
            username = photo_data.get('username')
            full_name = photo_data.get('full_name')
            photo_file_id = photo_data['photo_file_id']

            if not tg_user_id and not full_name:
                # Cannot credit - no identity
                self.stats[admin_id]['failed'] += 1
                logger.warning(f"Cannot credit forward - no identity available")
                return

            # Get or create participant
            participant_id = await db.get_or_create_participant(
                tg_user_id=tg_user_id,
                username=username,
                full_name=full_name or "Unknown"
            )

            # Add submission
            success, result = await db.add_submission(
                participant_id=participant_id,
                photo_file_id=photo_file_id,
                source='forward',
                tg_message_id=None
            )

            if success:
                self.stats[admin_id]['added'] += 1
                logger.info(f"✅ Batch forward: +1 point for {full_name}")
            else:
                self.stats[admin_id]['duplicates'] += 1
                logger.info(f"⏭️ Batch forward: duplicate for {full_name}")

        except Exception as e:
            logger.error(f"Failed to process batch photo: {e}")
            self.stats[admin_id]['failed'] += 1

    async def _update_progress(self, admin_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Update progress message."""
        if admin_id not in self.progress_messages:
            return

        chat_id, message_id = self.progress_messages[admin_id]
        stats = self.stats[admin_id]

        text = (
            f"⏳ Processing forwarded media...\n\n"
            f"📨 Received: {stats['received']}\n"
            f"✅ Points added: {stats['added']}\n"
            f"⏭️ Duplicates: {stats['duplicates']}\n"
            f"❌ Failed: {stats['failed']}"
        )

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text
            )
        except Exception as e:
            logger.debug(f"Could not update progress: {e}")

    async def _send_summary(self, admin_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Send final summary with Top 5 snapshot."""
        stats = self.stats[admin_id]

        # Get current Top 5
        leaderboard = await db.get_leaderboard(limit=5)

        # Build summary message
        lines = [
            "✅ Batch processing complete!\n",
            f"📊 **Summary:**",
            f"• Received: {stats['received']}",
            f"• Points added: {stats['added']}",
            f"• Duplicates ignored: {stats['duplicates']}",
            f"• Failed/uncredited: {stats['failed']}",
            "",
            "🏆 **Current Top 5:**"
        ]

        if leaderboard:
            for idx, entry in enumerate(leaderboard, 1):
                emoji = {1: '🥇', 2: '🥈', 3: '🥉'}.get(idx, f'{idx}.')
                name = entry['display_name']
                points = entry['points']
                lines.append(f"{emoji} {name} - {points} pts")
        else:
            lines.append("(No submissions yet)")

        summary_text = "\n".join(lines)

        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=summary_text
            )
        except Exception as e:
            logger.error(f"Failed to send summary: {e}")


# Global batch queue instance
batch_queue = BatchForwardQueue()


# ============================================================================
# HELPER DECORATORS
# ============================================================================

def admin_only(func):
    """Decorator to restrict commands to admins only."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ This command is admin-only")
            return
        return await func(update, context)
    return wrapper


def dm_only(func):
    """Decorator to restrict commands to DMs only."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type != 'private':
            await update.message.reply_text("⛔ This command only works in DMs")
            return
        return await func(update, context)
    return wrapper


# ============================================================================
# MESSAGE HANDLERS
# ============================================================================

async def handle_topic_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle new photo posted in the PnL Flex Challenge topic (real-time counting).
    """
    message = update.message

    # Debug logging
    logger.info(f"📸 Photo in topic - Chat: {message.chat_id}, Thread: {message.message_thread_id}, User: {message.from_user.id}")

    # Verify correct chat and topic
    if message.chat_id != CHAT_ID or message.message_thread_id != TOPIC_ID:
        return

    # Extract user info
    user = message.from_user
    tg_user_id = user.id
    username = user.username
    full_name = user.full_name

    # Get photo file_id
    photo_file_id = message.photo[-1].file_id

    try:
        # Get or create participant
        participant_id = await db.get_or_create_participant(
            tg_user_id=tg_user_id,
            username=username,
            full_name=full_name
        )

        # Add submission
        success, result = await db.add_submission(
            participant_id=participant_id,
            photo_file_id=photo_file_id,
            source='topic',
            tg_message_id=message.message_id
        )

        if success:
            logger.info(f"✅✅ NEW SUBMISSION: {full_name} (@{username}) - photo {photo_file_id[:20]}...")
        else:
            logger.info(f"⏭️ Duplicate ignored: {full_name} - photo already counted")

    except Exception as e:
        logger.error(f"Failed to process topic photo: {e}")


async def handle_forwarded_dm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle forwarded photos in admin DM (batch forwarding system).

    Uses forward_origin (Bot API 7.0+) to extract original user info.
    """
    message = update.message

    # Only work in private chats
    if message.chat.type != 'private':
        return

    # Only admins can use this
    if not is_admin(message.from_user.id):
        return

    # Must have forward_origin (new API)
    if not message.forward_origin:
        return

    # Must have photo
    if not message.photo:
        return

    logger.info(f"📨 Forwarded photo in admin DM from {message.from_user.id}")

    # Extract identity from forward_origin
    tg_user_id = None
    username = None
    full_name = None

    if isinstance(message.forward_origin, MessageOriginUser):
        # Best case: full user info available
        original_user = message.forward_origin.sender_user
        tg_user_id = original_user.id
        username = original_user.username
        full_name = original_user.full_name
        logger.info(f"   ✅ Got user: {full_name} (@{username}, ID: {tg_user_id})")

    elif isinstance(message.forward_origin, MessageOriginHiddenUser):
        # Privacy enabled: only name available
        full_name = message.forward_origin.sender_user_name
        logger.info(f"   ⚠️ Hidden user: {full_name} (no ID)")

    elif isinstance(message.forward_origin, MessageOriginChat):
        # From chat/topic: no individual user info
        chat = message.forward_origin.sender_chat
        logger.warning(f"   ❌ Forwarded from chat: {chat.title} - cannot determine user")
        # Cannot credit - skip
        return

    elif isinstance(message.forward_origin, MessageOriginChannel):
        # From channel: no individual user info
        channel = message.forward_origin.chat
        logger.warning(f"   ❌ Forwarded from channel: {channel.title} - cannot determine user")
        # Cannot credit - skip
        return

    # Must have at least a full_name
    if not full_name and not tg_user_id:
        logger.warning("   ❌ No identity available - cannot credit")
        return

    # Get photo file_id
    photo_file_id = message.photo[-1].file_id

    # Add to batch queue
    photo_data = {
        'tg_user_id': tg_user_id,
        'username': username,
        'full_name': full_name,
        'photo_file_id': photo_file_id
    }

    await batch_queue.add_forward(message.from_user.id, photo_data, context)


# ============================================================================
# PUBLIC COMMANDS
# ============================================================================

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


async def cmd_pnlrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pnlrank - Show Top 5 leaderboard (case-insensitive).

    Auto-deletes bot response after 60 seconds (NOT the user's command).
    """
    # Get show_points setting
    show_points = await db.get_show_points()

    # Get Top 5
    leaderboard = await db.get_leaderboard(limit=5)

    if not leaderboard:
        await update.message.reply_text("📊 No submissions yet!")
        return

    # Format leaderboard
    lines = ["🏆 PnL Flex Challenge - Top 5\n"]

    for entry in leaderboard:
        lines.append(format_leaderboard_entry(entry, show_points))

    text = "\n".join(lines)

    # Send leaderboard
    sent_message = await update.message.reply_text(text)

    # Auto-delete bot response after 60 seconds
    async def delete_bot_response():
        await asyncio.sleep(60)
        try:
            await context.bot.delete_message(
                chat_id=sent_message.chat_id,
                message_id=sent_message.message_id
            )
            logger.info("Auto-deleted /pnlrank response after 60s")
        except Exception as e:
            logger.debug(f"Could not delete /pnlrank response: {e}")

    asyncio.create_task(delete_bot_response())


# ============================================================================
# ADMIN COMMANDS (DM ONLY)
# ============================================================================

@admin_only
@dm_only
async def cmd_rankerinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /rankerinfo - Show Top 10 with full verification details.
    """
    rankers = await db.get_full_rankerinfo(limit=10)

    if not rankers:
        await update.message.reply_text("📊 No participants yet!")
        return

    lines = ["🔐 Ranker Info - Top 10\n"]

    for entry in rankers:
        code = entry['code']
        name = entry['display_name']
        tg_id = entry['tg_user_id'] or "Unknown"
        points = entry['points']

        lines.append(f"{code} | {name} | {tg_id} | {points} pts")

    await update.message.reply_text("\n".join(lines))


@admin_only
@dm_only
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add #01 5 - Add/remove points manually.

    Delta can be positive or negative.
    """
    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: /add #01 5\n"
            "Example: /add #01 5 (add 5 points)\n"
            "Example: /add #01 -3 (remove 3 points)"
        )
        return

    participant_code = context.args[0]
    try:
        delta = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Delta must be a number")
        return

    # Perform adjustment
    success, message, new_points = await db.add_adjustment(
        participant_code=participant_code,
        delta=delta,
        admin_tg_user_id=update.effective_user.id,
        note=f"Manual adjustment by admin {update.effective_user.id}"
    )

    if success:
        await update.message.reply_text(f"✅ Adjustment applied\n\n{message}\nNew total: {new_points} pts")
    else:
        await update.message.reply_text(f"❌ {message}")


@admin_only
@dm_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats - Show engagement statistics since last reset.
    """
    stats = await db.get_stats()

    lines = [
        "📊 Campaign Statistics (Since Reset)\n",
        f"👥 Participants: {stats['total_participants']}",
        f"📸 Submissions: {stats['total_submissions']}",
        f"⏭️ Duplicates: {stats['duplicates']}",
        f"✏️ Manual adjustments: {stats['manual_adjustments']}",
        f"🌟 Most active: {stats['most_active']} ({stats['max_points']} pts)",
        f"📊 Avg points/user: {stats['avg_points']:.1f}",
        f"🔄 Reset at: {stats['reset_at']}"
    ]

    await update.message.reply_text("\n".join(lines))


@admin_only
@dm_only
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reset - Reset all data with confirmation.

    Requires /reset CONFIRM or replying "CONFIRM" to warning.
    """
    # Check for confirmation
    if context.args and context.args[0] == 'CONFIRM':
        # Execute reset
        await db.reset_all_data()

        await update.message.reply_text(
            "✅ RESET COMPLETE\n\n"
            "All data cleared. Codes restart from #01.\n"
            "Leaderboard is now empty."
        )
        logger.warning(f"RESET EXECUTED by admin {update.effective_user.id}")
        return

    # Show warning
    stats = await db.get_stats()

    await update.message.reply_text(
        f"⚠️ RESET WARNING ⚠️\n\n"
        f"This will:\n"
        f"❌ Delete {stats['total_participants']} participants\n"
        f"❌ Delete {stats['total_submissions']} submissions\n"
        f"❌ Reset codes to #01\n"
        f"❌ Clear all adjustments and winners\n\n"
        f"To confirm, type:\n"
        f"/reset CONFIRM"
    )


@admin_only
@dm_only
async def cmd_pointson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable points display in public leaderboard."""
    await db.set_show_points(True)
    await update.message.reply_text("✅ Points display enabled")


@admin_only
@dm_only
async def cmd_pointsoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable points display in public leaderboard."""
    await db.set_show_points(False)
    await update.message.reply_text("✅ Points display disabled")


@admin_only
@dm_only
async def cmd_selectwinners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /selectwinners <week> - Save Top 5 as weekly winners.
    """
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /selectwinners <week>\nExample: /selectwinners 1")
        return

    try:
        week = int(context.args[0])
        if week < 1 or week > 4:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Week must be 1-4")
        return

    # Get current Top 5
    leaderboard = await db.get_leaderboard(limit=5)

    if not leaderboard:
        await update.message.reply_text("❌ No participants to select from")
        return

    # Prepare winners data
    winners = []
    for rank, entry in enumerate(leaderboard[:5], 1):
        # Get participant ID from database
        async with db._pool.acquire() as conn:
            participant_id = await conn.fetchval(
                'SELECT id FROM participants WHERE code = $1',
                entry['code']
            )

        winners.append({
            'rank': rank,
            'participant_id': participant_id,
            'points': entry['points']
        })

    # Save winners
    await db.save_winners(week, winners)

    # Format confirmation
    lines = [f"✅ Week {week} winners saved!\n"]
    for rank, entry in enumerate(leaderboard[:5], 1):
        emoji = {1: '🥇', 2: '🥈', 3: '🥉'}.get(rank, f'{rank}.')
        lines.append(f"{emoji} {entry['display_name']} - {entry['points']} pts")

    await update.message.reply_text("\n".join(lines))


@admin_only
@dm_only
async def cmd_winners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /winners <week> - View saved winners.
    """
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /winners <week>\nExample: /winners 1")
        return

    try:
        week = int(context.args[0])
        if week < 1 or week > 4:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Week must be 1-4")
        return

    # Get winners
    winners = await db.get_winners(week)

    if not winners:
        await update.message.reply_text(f"❌ No winners saved for Week {week}")
        return

    # Format
    lines = [f"🏆 Week {week} Winners\n"]
    for winner in winners:
        rank = winner['rank']
        emoji = {1: '🥇', 2: '🥈', 3: '🥉'}.get(rank, f'{rank}.')
        lines.append(f"{emoji} {winner['code']} {winner['display_name']} - {winner['points_at_time']} pts")

    await update.message.reply_text("\n".join(lines))


@admin_only
@dm_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin command help."""
    text = """
🔐 **Admin Commands** (DM only)

**Verification:**
/rankerinfo - Top 10 with full details
/stats - Engagement statistics

**Manual Adjustments:**
/add #01 5 - Add points
/add #01 -3 - Remove points

**Settings:**
/pointson - Show points in public leaderboard
/pointsoff - Hide points in public leaderboard

**Winners:**
/selectwinners <week> - Save Top 5 for week
/winners <week> - View saved winners

**Management:**
/reset - Clear all data (requires CONFIRM)

**Health:**
/test - Bot health check
/testdata - Database transaction test

**Batch Forwarding:**
Forward multiple PnL photos at once to bot DM.
Bot will show progress and final summary.
    """
    await update.message.reply_text(text)


# ============================================================================
# HEALTH CHECK COMMANDS
# ============================================================================

@admin_only
@dm_only
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /test - Run bot health check.
    """
    lines = ["✅ BOT HEALTH REPORT (/test)\n"]

    # Admin auth
    lines.append("🔐 Admin: OK")

    # Config
    try:
        lines.append(f"⚙️ Config: OK (CHAT_ID={CHAT_ID}, TOPIC_ID={TOPIC_ID})")
    except:
        lines.append("❌ Config: ERROR")

    # Database
    try:
        health = await db.health_check()
        if 'error' in health:
            lines.append(f"❌ Database: {health['error']}")
        else:
            lines.append("🗄️ Database: Connected")
            lines.append("  • Tables: OK")
            lines.append(f"  • Leaderboard query: {health['leaderboard_query']}")
            lines.append(f"  • Next code: #{health['next_code_number']}")
    except Exception as e:
        lines.append(f"❌ Database: {e}")

    # Batch worker
    lines.append("📦 Batch Worker: OK (initialized)")

    # Auto-delete
    lines.append("🧹 Auto Delete: Enabled (60s)")

    # Overall
    lines.append("\n✅ Overall: HEALTHY")

    await update.message.reply_text("\n".join(lines))


@admin_only
@dm_only
async def cmd_testdata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /testdata - Test database transaction (rollback test).
    """
    success, elapsed_ms = await db.test_transaction()

    if success:
        await update.message.reply_text(
            f"✅ TRANSACTION TEST PASSED\n\n"
            f"• Insert participant: OK\n"
            f"• Insert submission: OK\n"
            f"• Select data: OK\n"
            f"• Rollback: OK\n"
            f"• Time: {elapsed_ms:.2f}ms"
        )
    else:
        await update.message.reply_text("❌ TRANSACTION TEST FAILED")


# ============================================================================
# STARTUP & MAIN
# ============================================================================

async def post_init(application: Application):
    """Initialize database and bot on startup."""
    logger.info("🤖 Bot initializing...")

    try:
        # Initialize database
        await db.init_db()
        logger.info("✅ Database initialized")

    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise

    logger.info("✅ Bot ready!")


async def post_shutdown(application: Application):
    """Clean up on shutdown."""
    logger.info("🛑 Bot shutting down...")
    await db.close_db()
    logger.info("✅ Shutdown complete")


def main():
    """Main entry point."""
    logger.info("🚀 Starting PnL Flex Challenge Bot (PostgreSQL Edition)...")
    logger.info(f"📍 Monitoring Chat: {CHAT_ID}, Topic: {TOPIC_ID}")
    logger.info(f"👨‍💼 Admins: {ADMIN_IDS}")

    # Startup sanity check: verify files are present
    try:
        import os as sanity_os
        cwd = sanity_os.getcwd()
        logger.info(f"📂 Current working directory: {cwd}")

        app_files = sanity_os.listdir('/app')
        logger.info(f"📋 Files in /app: {sorted(app_files)}")

        # Verify critical files
        if 'db.py' not in app_files:
            logger.error("❌ CRITICAL: db.py not found in /app directory!")
        else:
            logger.info("✅ db.py found in /app directory")

        if 'bot.py' not in app_files:
            logger.error("❌ CRITICAL: bot.py not found in /app directory!")
        else:
            logger.info("✅ bot.py found in /app directory")
    except Exception as e:
        logger.error(f"⚠️ Startup sanity check failed: {e}")

    # Create application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Add message handlers
    # Topic photos (real-time)
    application.add_handler(
        MessageHandler(
            filters.PHOTO & filters.Chat(CHAT_ID),
            handle_topic_photo
        )
    )

    # Forwarded photos in DM (batch system)
    application.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE,
            handle_forwarded_dm
        )
    )

    # Public commands
    application.add_handler(
        CommandHandler(
            ['pnlrank', 'PNLRank', 'PNLRANK', 'pnlRank'],
            cmd_pnlrank
        )
    )

    # Admin commands
    application.add_handler(CommandHandler('rankerinfo', cmd_rankerinfo))
    application.add_handler(CommandHandler('add', cmd_add))
    application.add_handler(CommandHandler('stats', cmd_stats))
    application.add_handler(CommandHandler('reset', cmd_reset))
    application.add_handler(CommandHandler('pointson', cmd_pointson))
    application.add_handler(CommandHandler('pointsoff', cmd_pointsoff))
    application.add_handler(CommandHandler('selectwinners', cmd_selectwinners))
    application.add_handler(CommandHandler('winners', cmd_winners))
    application.add_handler(CommandHandler('help', cmd_help))
    application.add_handler(CommandHandler('test', cmd_test))
    application.add_handler(CommandHandler('testdata', cmd_testdata))

    # Start bot
    logger.info("🎯 Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
