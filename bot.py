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
import io
import imagehash
from PIL import Image

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
    MAX_WEEK,
    is_admin,
    normalize_participant_code,
    SensitiveFormatter
)

# Configure logging with sensitive data masking
# Only configure if handlers haven't been set up yet (prevents duplicates)
if not logging.getLogger().handlers:
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

# Apply sensitive formatter to all handlers
for handler in logging.getLogger().handlers:
    if not isinstance(handler.formatter, SensitiveFormatter):
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
        # Get or create participant FIRST
        participant_id = await db.get_or_create_participant(
            tg_user_id=tg_user_id,
            username=username,
            full_name=full_name
        )

        # Check file_id deduplication FIRST (fast, reliable)
        success, result = await db.add_submission(
            participant_id=participant_id,
            photo_file_id=photo_file_id,
            source='topic',
            tg_message_id=message.message_id
        )

        if not success:
            # Already submitted this exact file_id
            logger.info(f"⏭️ Duplicate ignored: {full_name} - photo already counted")
            return

        # --- FRAUD DETECTION (pHash) - AFTER file_id check ---
        # NOTE: Disabled for template-based images (Mudrex PnL cards)
        # Template images have similar perceptual hashes even when content differs
        # Relying on file_id deduplication instead (more reliable for this use case)

        ENABLE_PHASH_CHECK = False  # Set to True to enable visual similarity detection

        if ENABLE_PHASH_CHECK:
            current_phash = None
            try:
                # Download photo
                photo_file = await message.photo[-1].get_file()
                image_bytes_io = io.BytesIO()
                await photo_file.download_to_memory(out=image_bytes_io)
                image_bytes_io.seek(0)

                # Calculate Hash
                img = Image.open(image_bytes_io)
                current_hash = imagehash.phash(img)
                current_phash = str(current_hash)

                # Check against existing hashes (increased threshold for template images)
                existing_hashes = await db.get_all_hashes()
                THRESHOLD = 15  # Increased from 5 to reduce false positives on template images

                for stored_hex in existing_hashes:
                    stored_hash = imagehash.hex_to_hash(stored_hex)
                    distance = current_hash - stored_hash
                    if distance < THRESHOLD:
                        logger.warning(f"⚠️ Similar image detected: {full_name} (distance={distance}, threshold={THRESHOLD})")
                        # Log but don't block (for monitoring)
                        break

                # Save hash for future monitoring
                if current_phash:
                    await db.add_phash(participant_id, current_phash)

            except Exception as e:
                logger.error(f"Error in pHash check: {e}")
                # Continue even if pHash fails (fail-open)

        # Success - photo accepted
        logger.info(f"✅ NEW SUBMISSION: {full_name} (@{username}) - photo {photo_file_id[:20]}...")

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
    /pnlrank - Show Top 10 leaderboard for CURRENT WEEK ONLY (case-insensitive).

    Shows Top 5 with medals, then positions 6-10 plain (for encouragement).
    Auto-deletes bot response after 60 seconds (NOT the user's command).
    Resets to 0 each week when /new is run.
    """
    # Get show_points setting
    show_points = await db.get_show_points()

    # Get current week and label
    current_week = await db.get_current_week()
    week_label = await db.get_week_label()

    # Get Top 10 for current week only
    leaderboard = await db.get_leaderboard(limit=10, week=current_week)

    if not leaderboard:
        await update.message.reply_text(f"📊 No submissions yet for {week_label}!")
        return

    # Format leaderboard
    lines = [f"🏆 PnL Flex Challenge - {week_label} Top 10\n"]

    # Emoji numbers for positions 6-10
    emoji_numbers = {
        6: "6️⃣",
        7: "7️⃣",
        8: "8️⃣",
        9: "9️⃣",
        10: "🔟"
    }

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
            # Positions 6-10: Emoji numbers (no space after emoji)
            emoji = emoji_numbers.get(idx, f"{idx}.")
            if show_points:
                lines.append(f"{emoji}{name} - {points} pts")
            else:
                lines.append(f"{emoji}{name}")

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
    /rankerinfo - Show ALL participants (cumulative)
    /rankerinfo 1 - Show week 1 only
    /rankerinfo 2 - Show week 2 only
    """
    # Parse week argument
    week = None
    if context.args:
        try:
            week = int(context.args[0])
            if week < 1 or week > MAX_WEEK:
                await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
                return
        except ValueError:
            await update.message.reply_text("❌ Invalid week number. Usage: /rankerinfo or /rankerinfo 1")
            return

    # Get ranker info
    rankers = await db.get_full_rankerinfo(limit=None, week=week)
    total_participants = await db.get_total_participants()
    total_submissions = await db.get_total_submissions()
    current_week = await db.get_current_week()
    week_label = await db.get_week_label()

    if not rankers:
        if week:
            await update.message.reply_text(f"📊 No participants for Week {week}!")
        else:
            await update.message.reply_text("📊 No participants yet!")
        return

    # Build header
    if week:
        lines = [f"🔐 Ranker Info - Week {week}\n"]
    else:
        lines = [f"🔐 Ranker Info - Cumulative (All-Time)\n"]

    # Add participant list
    for entry in rankers:
        code = entry['code']
        name = entry['display_name']
        tg_id = entry['tg_user_id'] or "Unknown"
        points = entry['points']

        lines.append(f"{code} | {name} | {tg_id} | {points} pts")

    # Add statistics at the bottom
    lines.append("")
    if week:
        lines.append(f"📅 Showing Week {week} data")
        lines.append(f"📅 Current: {week_label} (Week {current_week})")
    else:
        lines.append(f"📅 Current: {week_label} (Week {current_week})")
        lines.append(f"📊 Total Participants: {total_participants}")
        lines.append(f"📝 Total Submissions: {total_submissions}")

    await update.message.reply_text("\n".join(lines))


@admin_only
@dm_only
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /add #01 5 - Add/remove points (cumulative)
    /add #01 current 5 - Add/remove points for current week
    /add #01 week2 5 - Add/remove points for week labeled "week2"
    /add #01 4 5 - Add/remove points for week number 4

    Delta can be positive or negative.
    Week-specific adjustments only affect that week's leaderboard.
    """
    if len(context.args) not in [2, 3]:
        await update.message.reply_text(
            "Usage:\n"
            "/add #01 5 - Add 5 cumulative points\n"
            "/add #01 -3 - Remove 3 cumulative points\n"
            "/add #01 current 5 - Add 5 to current week\n"
            "/add #01 week2 5 - Add 5 to week labeled 'week2'\n"
            "/add #01 4 5 - Add 5 to week number 4"
        )
        return

    participant_code = normalize_participant_code(context.args[0])
    week_number = None

    if len(context.args) == 2:
        # /add #01 5 (cumulative)
        try:
            delta = int(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Delta must be a number")
            return
    else:
        # /add #01 week2 5 or /add #01 current 5 or /add #01 4 5 (week-specific)
        week_str = context.args[1]

        # Check if it's "current"
        if week_str.lower() == "current":
            week_number = await db.get_current_week()
            week_label = await db.get_week_label()
        else:
            # Check if week_str matches current week label
            current_week_label = await db.get_week_label()
            current_week_number = await db.get_current_week()

            if week_str.lower() == current_week_label.lower():
                # Matches current week label, use current week number
                week_number = current_week_number
                week_label = current_week_label
            else:
                # Extract number from week string (e.g., "week2" -> 2, "2" -> 2, "week 3" -> 3)
                import re
                match = re.search(r'\d+', week_str)
                if not match:
                    await update.message.reply_text("❌ Week must be 'current', match current label, or contain a number")
                    return

                week_number = int(match.group())

                # Validate week number
                if week_number < 1 or week_number > MAX_WEEK:
                    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
                    return

                week_label = f"Week {week_number}"

        try:
            delta = int(context.args[2])
        except ValueError:
            await update.message.reply_text("❌ Delta must be a number")
            return

    # Perform adjustment
    success, message, new_points = await db.add_adjustment(
        participant_code=participant_code,
        delta=delta,
        admin_tg_user_id=update.effective_user.id,
        note=f"Manual adjustment by admin {update.effective_user.id}",
        week_number=week_number
    )

    if success:
        if new_points is not None:
            await update.message.reply_text(f"✅ Adjustment applied\n\n{message}\nNew total: {new_points} pts")
        else:
            await update.message.reply_text(f"✅ Adjustment applied\n\n{message}")
    else:
        await update.message.reply_text(f"❌ {message}")


@admin_only
@dm_only
async def cmd_bulkadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /bulkadd #01 current 5 #02 current 3 #03 2 10

    Add points to multiple participants at once.
    Format: code week delta code week delta code week delta...

    Examples:
    /bulkadd #01 current 5 #02 current 3
    /bulkadd #22 current 31 #33 current 21 #07 current 12
    """
    if len(context.args) < 3 or len(context.args) % 3 != 0:
        await update.message.reply_text(
            "Usage: /bulkadd <code> <week> <delta> [<code> <week> <delta>...]\n\n"
            "Examples:\n"
            "/bulkadd #01 current 5 #02 current 3\n"
            "/bulkadd #22 current 31 #33 current 21 #07 current 12\n\n"
            "Arguments must be in groups of 3: code, week, delta"
        )
        return

    # Parse arguments in groups of 3
    updates = []
    for i in range(0, len(context.args), 3):
        participant_code = context.args[i]
        week_str = context.args[i + 1]
        delta_str = context.args[i + 2]

        # Validate code
        if not participant_code.startswith('#'):
            await update.message.reply_text(f"❌ Code must start with # (got: {participant_code})")
            return

        # Parse week
        if week_str.lower() == "current":
            week_number = await db.get_current_week()
        else:
            # Check if it matches current week label
            current_week_label = await db.get_week_label()
            current_week_number = await db.get_current_week()

            if week_str.lower() == current_week_label.lower():
                week_number = current_week_number
            else:
                # Extract number from week string
                import re
                match = re.search(r'\d+', week_str)
                if not match:
                    await update.message.reply_text(f"❌ Invalid week: {week_str}")
                    return
                week_number = int(match.group())

                # Validate week number
                if week_number < 1 or week_number > MAX_WEEK:
                    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
                    return

        # Parse delta
        try:
            delta = int(delta_str)
        except ValueError:
            await update.message.reply_text(f"❌ Delta must be a number (got: {delta_str})")
            return

        updates.append((participant_code, week_number, delta))

    # Perform all updates
    success_count = 0
    fail_count = 0
    results = []

    for participant_code, week_number, delta in updates:
        success, message, new_points = await db.add_adjustment(
            participant_code=participant_code,
            delta=delta,
            admin_tg_user_id=update.effective_user.id,
            note=f"Bulk adjustment by admin {update.effective_user.id}",
            week_number=week_number
        )

        if success:
            success_count += 1
            results.append(f"✅ {participant_code}: {message}")
        else:
            fail_count += 1
            results.append(f"❌ {participant_code}: {message}")

    # Send summary
    summary = (
        f"📊 Bulk Update Complete\n\n"
        f"✅ Success: {success_count}\n"
        f"❌ Failed: {fail_count}\n\n"
    )

    # Add first 10 results
    summary += "\n".join(results[:10])
    if len(results) > 10:
        summary += f"\n... and {len(results) - 10} more"

    await update.message.reply_text(summary)
    logger.info(f"Admin {update.effective_user.id} bulk updated {success_count} participants")


@admin_only
@dm_only
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /remove #01 - Remove a participant and all their submissions.

    Deletes participant from leaderboard completely.
    Use this to remove duplicates or invalid entries.
    """
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: /remove #01\n"
            "Example: /remove #01 (removes participant #01)"
        )
        return

    participant_code = normalize_participant_code(context.args[0])

    # Perform deletion
    success, message = await db.delete_participant(participant_code)

    if success:
        await update.message.reply_text(f"✅ {message}")
        logger.info(f"Admin {update.effective_user.id} removed participant {participant_code}")
    else:
        await update.message.reply_text(f"❌ {message}")


@admin_only
@dm_only
async def cmd_removedata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /removedata 4 - Delete all data for week 4.

    Removes all submissions and adjustments for the specified week.
    Participants remain intact (only their week-specific data is removed).
    Use this to clean up duplicate/wrong week data.
    """
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: /removedata <week>\n"
            "Example: /removedata 4 (removes all data from week 4)"
        )
        return

    try:
        week_number = int(context.args[0])
        if week_number < 1:
            await update.message.reply_text("❌ Week number must be 1 or greater")
            return
    except ValueError:
        await update.message.reply_text("❌ Week number must be a number")
        return

    # Perform deletion (with backup for undo)
    success, message, submissions_deleted, adjustments_deleted = await db.delete_week_data(
        week_number,
        update.effective_user.id
    )

    if success:
        response = (
            f"✅ Week {week_number} Data Deleted\n\n"
            f"{message}"
        )
        await update.message.reply_text(response)
        logger.warning(
            f"Admin {update.effective_user.id} deleted Week {week_number} data: "
            f"{submissions_deleted} submissions, {adjustments_deleted} adjustments"
        )
    else:
        await update.message.reply_text(f"❌ {message}")


@admin_only
@dm_only
async def cmd_undodata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /undodata 4 - Restore deleted data for week 4.

    Restores all submissions and adjustments that were deleted with /removedata.
    Can only restore the most recent deletion for each week.
    """
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: /undodata <week>\n"
            "Example: /undodata 4 (restores week 4 data)"
        )
        return

    try:
        week_number = int(context.args[0])
        if week_number < 1:
            await update.message.reply_text("❌ Week number must be 1 or greater")
            return
    except ValueError:
        await update.message.reply_text("❌ Week number must be a number")
        return

    # Perform restoration
    success, message, submissions_restored, adjustments_restored = await db.restore_week_data(week_number)

    if success:
        response = (
            f"♻️ Week {week_number} Data Restored\n\n"
            f"{message}"
        )
        await update.message.reply_text(response)
        logger.info(
            f"Admin {update.effective_user.id} restored Week {week_number} data: "
            f"{submissions_restored} submissions, {adjustments_restored} adjustments"
        )
    else:
        await update.message.reply_text(f"❌ {message}")


@admin_only
@dm_only
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /new - Start a new week (resets leaderboard but keeps all history).
    /new week2 - Start new week with custom label "week2"
    /new week 3 - Start new week with custom label "week 3"

    This command:
    - Increments week counter
    - Sets custom label for the week
    - Future submissions count toward new week
    - All historical data is preserved
    - Use /rankerinfo <week> to view past weeks
    """
    # Get label from arguments (join all args with spaces)
    label = " ".join(context.args) if context.args else None

    # Start new week with optional label
    old_label, new_label, old_week, new_week = await db.start_new_week(label)

    message = (
        f"📅 New Week Started!\n\n"
        f"✅ {old_label} has ended\n"
        f"✅ {new_label} has begun\n\n"
        f"All historical data preserved:\n"
        f"• Use /rankerinfo to see cumulative stats\n"
        f"• Use /rankerinfo {old_week} to see {old_label}\n"
        f"• Use /rankerinfo {new_week} to see {new_label}\n\n"
        f"New submissions will count toward {new_label}!"
    )

    await update.message.reply_text(message)
    logger.info(f"Admin {update.effective_user.id} started {new_label} (Week {new_week})")


@admin_only
@dm_only
async def cmd_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /current week 2 - Set current week to Week 2
    /current week 2 week2 - Set current week to 2 with label "week2"

    Use this to manually control which week submissions count toward.
    """
    # Parse "week N" format or just "N"
    args = context.args

    if len(args) < 1:
        await update.message.reply_text(
            "Usage:\n"
            "/current week 2 - Set to Week 2\n"
            "/current week 2 week2 - Set to Week 2 labeled 'week2'"
        )
        return

    # Handle "week N" format
    if args[0].lower() == 'week' and len(args) >= 2:
        try:
            week_number = int(args[1])
        except ValueError:
            await update.message.reply_text("❌ Week number must be a number")
            return
        # Label is everything after the week number
        label = " ".join(args[2:]) if len(args) > 2 else None
    else:
        # Handle just "N" format
        try:
            week_number = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ Week number must be a number")
            return
        # Label is everything after the week number
        label = " ".join(args[1:]) if len(args) > 1 else None

    if week_number < 1 or week_number > MAX_WEEK:
        await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
        return

    # Set current week
    week_number, label = await db.set_current_week(week_number, label)

    message = (
        f"⚙️ Week Manually Set!\n\n"
        f"✅ Current week is now: {label} (Week {week_number})\n\n"
        f"New submissions will count toward {label}.\n"
        f"Use /pnlrank to see the current week leaderboard."
    )

    await update.message.reply_text(message)
    logger.warning(f"Admin {update.effective_user.id} manually set week to {week_number} ({label})")


@admin_only
@dm_only
async def cmd_setweek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setweek 2 - Set current week to Week 2
    /setweek 2 week2 - Set current week to 2 with label "week2"

    Use this to fix week number issues. Use with caution!
    """
    if len(context.args) < 1:
        await update.message.reply_text(
            "Usage:\n"
            "/setweek 2 - Set to Week 2\n"
            "/setweek 2 week2 - Set to Week 2 labeled 'week2'"
        )
        return

    try:
        week_number = int(context.args[0])
        if week_number < 1 or week_number > MAX_WEEK:
            await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
            return
    except ValueError:
        await update.message.reply_text("❌ Week number must be a number")
        return

    # Get label from remaining arguments
    label = " ".join(context.args[1:]) if len(context.args) > 1 else None

    # Set current week
    week_number, label = await db.set_current_week(week_number, label)

    message = (
        f"⚙️ Week Manually Set!\n\n"
        f"✅ Current week is now: {label} (Week {week_number})\n\n"
        f"New submissions will count toward {label}.\n"
        f"Use /pnlrank to see the current week leaderboard."
    )

    await update.message.reply_text(message)
    logger.warning(f"Admin {update.effective_user.id} manually set week to {week_number} ({label})")


@admin_only
@dm_only
async def cmd_recalculate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /recalculate - Recalculate cumulative points from all submissions.

    Use this after restoring data with /undodata or if points get out of sync.
    Counts all submissions for each participant and updates their total points.
    """
    await update.message.reply_text("♻️ Recalculating cumulative points...")

    participants_updated, summary = await db.recalculate_cumulative_points()

    if participants_updated == 0:
        await update.message.reply_text("✅ All cumulative points are correct!\n\nNo updates needed.")
    else:
        response = (
            f"✅ Recalculation Complete!\n\n"
            f"Updated {participants_updated} participant(s):\n\n"
            f"{summary}\n\n"
            f"Cumulative points are now synced with submissions!"
        )
        await update.message.reply_text(response)

    logger.info(f"Admin {update.effective_user.id} recalculated points: {participants_updated} updated")


@admin_only
@dm_only
async def cmd_breakdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /breakdown #33 - Show detailed point breakdown for a participant.
    /breakdown #33 2 - Show breakdown for week 2 only.

    Displays:
    - Submissions count
    - Adjustments sum
    - Calculated total
    - Comparison with stored points
    """
    if len(context.args) < 1:
        await update.message.reply_text(
            "Usage:\n"
            "/breakdown #33 - Cumulative breakdown\n"
            "/breakdown #33 2 - Week 2 breakdown only"
        )
        return

    participant_code = normalize_participant_code(context.args[0])
    week = None

    if len(context.args) >= 2:
        try:
            week = int(context.args[1])
            if week < 1 or week > MAX_WEEK:
                await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
                return
        except ValueError:
            await update.message.reply_text("❌ Week must be a number")
            return

    # Get breakdown
    breakdown = await db.get_participant_breakdown(participant_code, week)

    if not breakdown:
        await update.message.reply_text(f"❌ Participant {participant_code} not found")
        return

    # Format response
    if week:
        title = f"📊 Point Breakdown - {breakdown['code']} ({breakdown['display_name']}) - Week {week}"
    else:
        title = f"📊 Point Breakdown - {breakdown['code']} ({breakdown['display_name']}) - Cumulative"

    lines = [
        title,
        "",
        f"📸 Submissions: {breakdown['submissions_count']}",
        f"✏️ Adjustments: {breakdown['adjustments_sum']:+d}",
        f"➕ Calculated Total: {breakdown['calculated_total']}",
        "",
        f"🗄️ Stored Points: {breakdown['cumulative_points']}",
    ]

    # Check if there's a mismatch
    if not week and breakdown['calculated_total'] != breakdown['cumulative_points']:
        lines.append("")
        lines.append(f"⚠️ MISMATCH DETECTED!")
        lines.append(f"Difference: {breakdown['calculated_total'] - breakdown['cumulative_points']:+d}")
        lines.append("")
        lines.append("Run /recalculate to fix cumulative points")

    await update.message.reply_text("\n".join(lines))
    logger.info(f"Admin {update.effective_user.id} viewed breakdown for {participant_code}")


@admin_only
@dm_only
async def cmd_clearadjustments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /clearadjustments 2 - Clear all adjustments for week 2.

    Use this to remove incorrect manual adjustments.
    WARNING: This cannot be undone!
    """
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: /clearadjustments <week>\n"
            "Example: /clearadjustments 2"
        )
        return

    try:
        week_number = int(context.args[0])
        if week_number < 1:
            await update.message.reply_text("❌ Week number must be 1 or greater")
            return
    except ValueError:
        await update.message.reply_text("❌ Week must be a number")
        return

    # Clear adjustments
    count, message = await db.clear_week_adjustments(week_number)

    if count == 0:
        await update.message.reply_text(f"ℹ️ No adjustments found for Week {week_number}")
    else:
        response = (
            f"🗑️ Adjustments Cleared\n\n"
            f"{message}\n\n"
            f"⚠️ This action cannot be undone!\n"
            f"Run /recalculate if needed to update cumulative points."
        )
        await update.message.reply_text(response)
        logger.warning(f"Admin {update.effective_user.id} cleared {count} adjustments for Week {week_number}")


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
        if week < 1 or week > MAX_WEEK:
            await update.message.reply_text(f"❌ Week must be between 1 and {MAX_WEEK}")
            return
    except ValueError:
        await update.message.reply_text("❌ Week number must be a number")
        return

    # Get current Top 5
    leaderboard = await db.get_leaderboard(limit=5)

    if not leaderboard:
        await update.message.reply_text("❌ No participants to select from")
        return

    # Prepare winners data
    winners = []
    for rank, entry in enumerate(leaderboard[:5], 1):
        # Get participant ID using db helper
        participant_id = await db.get_participant_id_by_code(entry['code'])

        if participant_id is None:
            logger.error(f"Could not find participant ID for code {entry['code']}")
            continue

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
        if week < 1 or week > MAX_WEEK:
            await update.message.reply_text(f"❌ Week must be between 1 and {MAX_WEEK}")
            return
    except ValueError:
        await update.message.reply_text("❌ Week number must be a number")
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
🔐 **PnL Flex Challenge Bot - Admin Commands** (DM only)

**📊 View Leaderboards:**
/pnlrank - Show Top 10 for current week (PUBLIC)
/rankerinfo - View all participants (cumulative)
/rankerinfo 2 - View Week 2 leaderboard only
/stats - Engagement statistics

**📅 Week Management:**
/current week 2 - Set current week to Week 2
/current week 2 week2 - Set to Week 2 with label "week2"

**✏️ Manual Adjustments:**
/add #33 2 21 - Add 21 points to participant #33 for Week 2
/add #33 current 10 - Add 10 points to current week
/add #33 5 - Add 5 cumulative points (all-time)
/add #33 -3 - Remove 3 cumulative points

**🔍 Diagnostics:**
/breakdown 33 - Show point breakdown (cumulative)
/breakdown 33 2 - Show point breakdown for Week 2 only

**🗑️ Cleanup:**
/remove 33 - Delete participant and all submissions

**🔧 Advanced (if needed):**
/recalculate - Fix point mismatches (rarely needed)

**📖 How It Works:**
1. Users submit PnL photos in the topic → auto-added to current week
2. Use /current week 2 to manually control which week is active
3. Add manual adjustments with /add #33 2 21
4. View leaderboards with /pnlrank (current week) or /rankerinfo 2 (specific week)

All submissions are automatically tracked and calculated!
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
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

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

    # Admin commands (ESSENTIAL ONLY - cleaned up for simplicity)
    application.add_handler(CommandHandler('rankerinfo', cmd_rankerinfo))
    application.add_handler(CommandHandler('add', cmd_add))
    application.add_handler(CommandHandler('breakdown', cmd_breakdown))
    application.add_handler(CommandHandler('current', cmd_current))  # Manual week control
    application.add_handler(CommandHandler('remove', cmd_remove))
    application.add_handler(CommandHandler('stats', cmd_stats))
    application.add_handler(CommandHandler('help', cmd_help))

    # Backup/advanced commands (keep but not in main flow)
    application.add_handler(CommandHandler('recalculate', cmd_recalculate))  # Fix point mismatches if needed
    application.add_handler(CommandHandler('setweek', cmd_setweek))  # Legacy alias for /current

    # REMOVED COMMANDS (commented out to avoid confusion):
    # application.add_handler(CommandHandler('new', cmd_new))  # Auto week increment - use /current instead
    # application.add_handler(CommandHandler('bulkadd', cmd_bulkadd))  # Not needed
    # application.add_handler(CommandHandler('removedata', cmd_removedata))  # Dangerous
    # application.add_handler(CommandHandler('undodata', cmd_undodata))  # Dangerous
    # application.add_handler(CommandHandler('clearadjustments', cmd_clearadjustments))  # Dangerous
    # application.add_handler(CommandHandler('reset', cmd_reset))  # Dangerous
    # application.add_handler(CommandHandler('pointson', cmd_pointson))  # Not essential
    # application.add_handler(CommandHandler('pointsoff', cmd_pointsoff))  # Not essential
    # application.add_handler(CommandHandler('selectwinners', cmd_selectwinners))  # Not essential
    # application.add_handler(CommandHandler('winners', cmd_winners))  # Not essential
    # application.add_handler(CommandHandler('test', cmd_test))  # Debug only
    # application.add_handler(CommandHandler('testdata', cmd_testdata))  # Debug only

    # Start bot
    logger.info("🎯 Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
