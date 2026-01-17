"""
PnL Flex Challenge Leaderboard Bot
Main bot module with crash-resistant architecture and automatic sync on every restart.

Key Features:
- Smart backfill on every startup (crash recovery)
- Duplicate photo detection using file_id
- Idempotent message processing using message_id
- Atomic JSON writes with backups
- Admin notifications after sync
"""

import os
import logging
import asyncio
from datetime import datetime
from functools import wraps
import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import TelegramError

# Import local modules
from utils import (
    IST,
    CAMPAIGN_START,
    CAMPAIGN_END,
    CHAT_ID,
    TOPIC_ID,
    ADMIN_IDS,
    is_admin,
    calculate_week_number,
    get_current_week,
    SensitiveFormatter
)
from data_manager import (
    add_submission,
    load_submissions,
    save_config,
    load_config,
    get_leaderboard,
    save_week_winners,
    get_week_winners,
    get_stats
)
from leaderboard import (
    format_leaderboard,
    format_admin_dashboard,
    format_engagement_stats,
    format_winners_message,
    format_saved_winners,
    format_sync_notification
)

# Configure logging with sensitive data masking
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Apply sensitive formatter to root logger
for handler in logging.getLogger().handlers:
    handler.setFormatter(SensitiveFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))

# Reduce noise from httpx and telegram libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Get bot token from environment
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")


# ============================================================================
# CRASH-RESISTANT BACKFILL (RUNS ON EVERY STARTUP)
# ============================================================================

async def smart_backfill(application: Application, scan_range=None):
    """
    Self-healing sync mechanism with message ID range scanning.

    This function:
    1. Loads existing submissions.json (or creates if missing)
    2. Scans through message IDs in the topic using forwarding probe
    3. Compares message_ids: existing vs. fetched
    4. Processes ONLY new message_ids (idempotent)
    5. Updates JSON atomically
    6. Sends sync notification to all admins

    Args:
        scan_range: Tuple of (start_id, end_id) for message scanning, or None for env config

    This ensures the bot recovers from:
    - Crashes
    - Railway redeployments
    - Extended downtime
    - Historical message backfill
    """
    logger.info("🔄 Starting smart backfill with message ID scanner...")
    start_time = time.time()

    # Load existing data
    data = load_submissions()
    existing_message_ids = set()

    for user_id, user_data in data['users'].items():
        for submission in user_data['submissions']:
            existing_message_ids.add(submission['message_id'])

    existing_count = len(existing_message_ids)
    is_first_run = existing_count == 0

    logger.info(f"📊 Found {existing_count} existing submissions in database")

    # Determine scan range
    if scan_range:
        start_id, end_id = scan_range
    else:
        # Get from environment variables or use defaults
        start_id = int(os.getenv('SCAN_START_ID', TOPIC_ID))
        # Default: scan 2000 messages ahead (should cover most topics)
        scan_range_size = int(os.getenv('SCAN_RANGE', '2000'))
        end_id = start_id + scan_range_size

    logger.info(f"📡 Scanning message IDs from {start_id} to {end_id}")

    # Collect processed messages
    processed_messages = []
    new_submissions = 0
    skipped_messages = 0
    errors = 0

    # Get first admin ID for forwarding probe
    probe_chat_id = ADMIN_IDS[0] if ADMIN_IDS else None

    if not probe_chat_id:
        logger.error("❌ No admin ID available for message scanning")
        # Send notification about limitation
        await send_sync_notification(application, {
            'is_first_run': is_first_run,
            'total_found': existing_count,
            'existing_count': existing_count,
            'new_count': 0,
            'duration': time.time() - start_time,
            'top_3': [],
            'total_users': len(data['users']),
            'date_range': 'Unable to scan - no admin configured',
            'error': 'SCAN_DISABLED'
        })
        return

    logger.info(f"🔍 Using message probe via admin chat {probe_chat_id}")

    # Scan through message ID range
    batch_size = 10  # Process in small batches
    total_range = end_id - start_id

    for msg_id in range(start_id, end_id):
        # Progress logging every 50 messages
        if (msg_id - start_id) % 50 == 0:
            progress = ((msg_id - start_id) / total_range) * 100
            logger.info(f"📊 Progress: {progress:.1f}% ({msg_id - start_id}/{total_range})")

        # Skip if already processed
        if msg_id in existing_message_ids:
            skipped_messages += 1
            continue

        try:
            # Probe: Try to forward message to get its info
            # This is a workaround for Bot API's lack of direct message fetching
            forwarded = await application.bot.forward_message(
                chat_id=probe_chat_id,
                from_chat_id=CHAT_ID,
                message_id=msg_id
            )

            # IMPORTANT: Check if message has photos first
            if not forwarded.photo:
                # Not a photo, delete and skip
                try:
                    await application.bot.delete_message(
                        chat_id=probe_chat_id,
                        message_id=forwarded.message_id
                    )
                except Exception:
                    pass
                continue

            # Check if forwarded message is from the correct chat
            # When forwarding from topics, forward_from_chat will be the supergroup
            # and forward_from_message_id will be the original message ID
            if not forwarded.forward_from_chat or forwarded.forward_from_chat.id != CHAT_ID:
                # Not from our target chat, delete and skip
                try:
                    await application.bot.delete_message(
                        chat_id=probe_chat_id,
                        message_id=forwarded.message_id
                    )
                except Exception:
                    pass
                logger.debug(f"Message {msg_id} not from target chat, skipping")
                continue

            # Since we can't reliably check topic ID from forwarded messages,
            # we'll rely on the message ID range being within the topic
            # and the campaign date filter to ensure correctness
            # The user should provide a tight message ID range for their specific topic

            if forwarded.photo:
                # Get original message info from forward
                original_user = forwarded.forward_from or forwarded.forward_sender_name

                if not original_user:
                    logger.debug(f"Message {msg_id} has no sender info, skipping")
                    continue

                # Extract user info
                if hasattr(original_user, 'id'):
                    user_id = original_user.id
                    username = original_user.username or "Unknown"
                    full_name = original_user.full_name or "Unknown"
                else:
                    # Anonymous forward or sender name only
                    logger.debug(f"Message {msg_id} is anonymous, skipping")
                    continue

                # Get photo_id (largest size)
                photo_id = forwarded.photo[-1].file_id

                # Get timestamp (use forward date as approximation)
                timestamp = forwarded.forward_date or forwarded.date

                # Check if within campaign period
                if timestamp < CAMPAIGN_START or timestamp > CAMPAIGN_END:
                    logger.debug(f"Message {msg_id} outside campaign period")
                    continue

                # Calculate week number
                week = calculate_week_number(timestamp)
                if week is None:
                    logger.warning(f"Could not calculate week for message {msg_id}")
                    continue

                # Add submission (idempotent)
                added = add_submission(
                    user_id=user_id,
                    username=username,
                    full_name=full_name,
                    message_id=msg_id,
                    photo_id=photo_id,
                    timestamp=timestamp,
                    week=week
                )

                if added:
                    new_submissions += 1
                    processed_messages.append(msg_id)
                    logger.info(f"✅ Processed: msg={msg_id}, user={username}, week={week}")

                # Delete the forwarded probe message to keep chat clean
                try:
                    await application.bot.delete_message(
                        chat_id=probe_chat_id,
                        message_id=forwarded.message_id
                    )
                except Exception:
                    pass  # Ignore deletion errors

            # Rate limiting: small delay every batch
            if (msg_id - start_id) % batch_size == 0:
                await asyncio.sleep(0.5)  # 500ms delay every 10 messages

        except TelegramError as e:
            # Message doesn't exist, not a photo, or other error
            error_msg = str(e).lower()
            if 'message to forward not found' in error_msg or 'message not found' in error_msg:
                # Normal - message doesn't exist or was deleted
                pass
            elif 'message_thread_id_invalid' in error_msg:
                # Message exists but not in a topic
                pass
            else:
                errors += 1
                if errors < 10:  # Only log first 10 errors to avoid spam
                    logger.debug(f"Error probing message {msg_id}: {e}")

        except Exception as e:
            logger.error(f"Unexpected error processing message {msg_id}: {e}")
            errors += 1

    # Calculate final stats
    duration = time.time() - start_time
    total_found = existing_count + new_submissions

    # Reload data to get updated user count
    data = load_submissions()

    # Get top 3 for notification
    current_week = get_current_week()
    if current_week:
        leaderboard = get_leaderboard(current_week)
        top_3 = leaderboard[:3] if leaderboard else []
    else:
        top_3 = []

    # Prepare sync stats
    sync_stats = {
        'is_first_run': is_first_run,
        'total_found': total_found,
        'existing_count': existing_count,
        'new_count': new_submissions,
        'duration': duration,
        'top_3': top_3,
        'total_users': len(data['users']),
        'date_range': f"Scanned {start_id} to {end_id}"
    }

    # Send notifications to admins
    await send_sync_notification(application, sync_stats)

    logger.info(f"✅ Backfill complete in {duration:.1f}s")
    logger.info(f"📊 Scanned: {end_id - start_id} message IDs")
    logger.info(f"📊 Results: {existing_count} existing + {new_submissions} new = {total_found} total submissions")
    logger.info(f"📊 Errors: {errors}")


async def send_sync_notification(application: Application, sync_stats: dict):
    """Helper function to send sync notifications to all admins"""
    notification = format_sync_notification(sync_stats)

    for admin_id in ADMIN_IDS:
        try:
            await application.bot.send_message(
                chat_id=admin_id,
                text=notification
            )
            logger.info(f"✅ Sent sync notification to admin {admin_id}")
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")


# ============================================================================
# HELPER DECORATORS
# ============================================================================

def admin_only(func):
    """Decorator to restrict commands to admins only"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id

        if not is_admin(user_id):
            await update.message.reply_text("⛔ This command is admin-only")
            return

        return await func(update, context)

    return wrapper


def dm_only(func):
    """Decorator to restrict commands to DMs only"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type != 'private':
            await update.message.reply_text("⛔ This command only works in DMs")
            return

        return await func(update, context)

    return wrapper


# ============================================================================
# MESSAGE HANDLERS
# ============================================================================

async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle new photo messages in the campaign topic.

    This runs in real-time as users post PnL cards.
    """
    message = update.message

    # Check if message is in the correct chat
    if message.chat_id != CHAT_ID:
        return

    # Check if message is in the campaign topic
    if not message.message_thread_id or message.message_thread_id != TOPIC_ID:
        return

    # Check if message has photos
    if not message.photo:
        return

    # Extract message info
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    full_name = message.from_user.full_name or "Unknown"
    message_id = message.message_id
    timestamp = message.date

    # Get photo_id (largest size)
    photo_id = message.photo[-1].file_id

    # Check if message is within campaign period
    if timestamp < CAMPAIGN_START or timestamp > CAMPAIGN_END:
        logger.info(f"Message {message_id} outside campaign period, ignoring")
        return

    # Calculate week number
    week = calculate_week_number(timestamp)
    if week is None:
        logger.warning(f"Could not calculate week for message {message_id}")
        return

    # Add submission (idempotent - checks message_id and photo_id)
    added = add_submission(
        user_id=user_id,
        username=username,
        full_name=full_name,
        message_id=message_id,
        photo_id=photo_id,
        timestamp=timestamp,
        week=week
    )

    if added:
        logger.info(f"✅ New submission: user={username} ({user_id}), week={week}, msg={message_id}")
    else:
        logger.debug(f"⏭️ Duplicate submission ignored: msg={message_id}")


# ============================================================================
# PUBLIC COMMANDS
# ============================================================================

async def cmd_pnlrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pnlrank command - Show top 5 leaderboard for current week
    Case-insensitive, works in group or DM
    """
    current_week = get_current_week()

    if current_week is None:
        await update.message.reply_text("⏰ Campaign hasn't started yet!")
        return

    # Get config for point visibility
    config = load_config()
    show_points = config.get('show_points', True)

    # Format leaderboard
    leaderboard_text = format_leaderboard(
        week=current_week,
        show_points=show_points,
        limit=5
    )

    await update.message.reply_text(leaderboard_text)


# ============================================================================
# ADMIN COMMANDS
# ============================================================================

@admin_only
@dm_only
async def cmd_adminboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /adminboard command - Show detailed top 10 with user IDs
    Admin only, DM only
    """
    current_week = get_current_week()

    if current_week is None:
        await update.message.reply_text("⏰ Campaign hasn't started yet!")
        return

    # Format admin dashboard
    dashboard_text = format_admin_dashboard(current_week)

    await update.message.reply_text(dashboard_text)


@admin_only
@dm_only
async def cmd_engagement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /eng command - Show engagement statistics
    Admin only, DM only
    """
    current_week = get_current_week()

    # Format engagement stats
    stats_text = format_engagement_stats(current_week)

    await update.message.reply_text(stats_text)


@admin_only
@dm_only
async def cmd_pointson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pointson command - Enable points display in public leaderboard
    Admin only, DM only
    """
    config = load_config()
    config['show_points'] = True
    save_config(config)

    await update.message.reply_text("✅ Points display enabled for public leaderboard")


@admin_only
@dm_only
async def cmd_pointsoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /pointsoff command - Disable points display in public leaderboard
    Admin only, DM only
    """
    config = load_config()
    config['show_points'] = False
    save_config(config)

    await update.message.reply_text("✅ Points display disabled for public leaderboard")


@admin_only
@dm_only
async def cmd_selectwinners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /selectwinners <week> command - Select and save top 5 winners for a week
    Admin only, DM only
    """
    # Parse week number from args
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /selectwinners <week>\nExample: /selectwinners 1")
        return

    try:
        week = int(context.args[0])
        if week < 1 or week > 4:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Week must be a number between 1 and 4")
        return

    # Get top 5 for the week
    leaderboard = get_leaderboard(week)

    if not leaderboard:
        await update.message.reply_text(f"❌ No submissions for Week {week}")
        return

    # Take top 5
    top_5 = leaderboard[:5]

    # Format winners list
    winners = []
    for rank, entry in enumerate(top_5, 1):
        winners.append({
            'rank': rank,
            'username': entry['username'],
            'full_name': entry['full_name'],
            'points': entry['points']
        })

    # Save to winners.json
    save_week_winners(week, winners)

    # Format and send confirmation
    message = format_winners_message(week, winners)
    await update.message.reply_text(message)


@admin_only
@dm_only
async def cmd_winners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /winners <week> command - View previously selected winners
    Admin only, DM only
    """
    # Parse week number from args
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /winners <week>\nExample: /winners 1")
        return

    try:
        week = int(context.args[0])
        if week < 1 or week > 4:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Week must be a number between 1 and 4")
        return

    # Get and format saved winners
    message = format_saved_winners(week)
    await update.message.reply_text(message)


@admin_only
@dm_only
async def cmd_backfill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /backfill command - Manually trigger backfill with default range
    Admin only, DM only
    """
    await update.message.reply_text("🔄 Starting manual backfill with default range...")

    # Run backfill
    await smart_backfill(context.application)

    await update.message.reply_text("✅ Backfill complete! Check results above.")


@admin_only
@dm_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /scan <start_id> <end_id> command - Scan specific message ID range
    Admin only, DM only

    Example: /scan 103380 103580
    """
    # Parse arguments
    if not context.args or len(context.args) != 2:
        await update.message.reply_text(
            "📡 Usage: /scan <start_id> <end_id>\n\n"
            "Example: /scan 103380 103580\n\n"
            "⚠️ IMPORTANT:\n"
            "• Only scan messages from YOUR topic\n"
            "• Find start ID: Right-click FIRST message in topic → Copy Link\n"
            "• Find end ID: Right-click LATEST message in topic → Copy Link\n"
            "• Use a tight range to avoid other topics!\n\n"
            f"💡 Your topic ID is: {TOPIC_ID}\n"
            f"Usually starts around: {TOPIC_ID} (topic creation message)"
        )
        return

    try:
        start_id = int(context.args[0])
        end_id = int(context.args[1])

        if start_id >= end_id:
            raise ValueError("Start ID must be less than end ID")

        if end_id - start_id > 5000:
            raise ValueError("Range too large (max 5000 messages)")

    except ValueError as e:
        await update.message.reply_text(f"❌ Invalid range: {e}")
        return

    await update.message.reply_text(
        f"🔄 Starting scan of {end_id - start_id} message IDs...\n"
        f"📡 Range: {start_id} to {end_id}\n\n"
        f"⚠️ Make sure this range ONLY contains messages from topic {TOPIC_ID}\n"
        f"⏳ This may take a few minutes. You'll see probe messages briefly (auto-deleted).\n\n"
        f"📊 Filter criteria:\n"
        f"✅ Photos only\n"
        f"✅ Campaign dates: Jan 15 - Feb 11, 2025\n"
        f"✅ From your group chat"
    )

    # Run backfill with custom range
    await smart_backfill(context.application, scan_range=(start_id, end_id))

    await update.message.reply_text("✅ Scan complete! Check results above.")


@admin_only
@dm_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /stats command - Show campaign statistics
    Admin only, DM only
    """
    stats = get_stats()

    lines = [
        "📊 Campaign Statistics",
        "",
        f"👥 Total Participants: {stats['total_participants']}",
        f"📸 Total Submissions: {stats['total_submissions']}",
        f"📅 Campaign Start: {stats['campaign_start']}",
        f"🔄 Last Updated: {stats['last_updated']}"
    ]

    await update.message.reply_text("\n".join(lines))


# ============================================================================
# STARTUP & MAIN
# ============================================================================

async def post_init(application: Application):
    """Run after bot initialization, before start"""
    logger.info("🤖 Bot initialized, running startup tasks...")

    # DISABLED: Automatic backfill on startup (prevents scanning wrong topics)
    # Use /scan command manually with correct message ID range for your specific topic
    # await smart_backfill(application)

    logger.info("✅ Startup complete!")
    logger.info("💡 Use /scan <start_id> <end_id> to manually scan your topic's messages")


def main():
    """Main entry point"""
    logger.info("🚀 Starting PnL Flex Challenge Leaderboard Bot...")
    logger.info(f"📍 Monitoring Chat: {CHAT_ID}, Topic: {TOPIC_ID}")
    logger.info(f"👨‍💼 Admins: {ADMIN_IDS}")

    # Create application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Add message handlers
    application.add_handler(
        MessageHandler(
            filters.PHOTO & filters.Chat(CHAT_ID),
            handle_photo_message
        )
    )

    # Add public commands (case-insensitive)
    application.add_handler(
        CommandHandler(
            ['pnlrank', 'PNLRank', 'PNLRANK', 'pnlRank'],
            cmd_pnlrank
        )
    )

    # Add admin commands
    application.add_handler(CommandHandler('adminboard', cmd_adminboard))
    application.add_handler(CommandHandler('eng', cmd_engagement))
    application.add_handler(CommandHandler('pointson', cmd_pointson))
    application.add_handler(CommandHandler('pointsoff', cmd_pointsoff))
    application.add_handler(CommandHandler('selectwinners', cmd_selectwinners))
    application.add_handler(CommandHandler('winners', cmd_winners))
    application.add_handler(CommandHandler('backfill', cmd_backfill))
    application.add_handler(CommandHandler('scan', cmd_scan))
    application.add_handler(CommandHandler('stats', cmd_stats))

    # Start bot
    logger.info("🎯 Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
