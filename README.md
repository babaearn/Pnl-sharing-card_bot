# PnL Flex Challenge Leaderboard Bot 🏆 (PostgreSQL Edition)

Production-ready Telegram bot for tracking PnL Share Card submissions using **PostgreSQL** as the source of truth. Features batch forwarding system, participant codes, and crash-resistant architecture.

## 🌟 Key Features

- **PostgreSQL Database**: Reliable, scalable persistence with proper transactions
- **Batch Forwarding**: Forward ~180 photos at once with progress tracking
- **Participant Codes**: Auto-assigned #01, #02, etc. for easy reference
- **Identity System**: Tracks users by Telegram ID or normalized name
- **forward_origin Support**: Uses Bot API 7.0+ for proper forward handling
- **Idempotent Processing**: Duplicate protection via UNIQUE constraints
- **Manual Adjustments**: Add/remove points with audit trail
- **Time-Independent**: No campaign date filtering
- **Auto-Delete Leaderboard**: Public /pnlrank cleans up after 60s

## 📁 Project Structure

```
pnl-leaderboard-bot/
├── bot.py                 # Main bot logic with PostgreSQL
├── db.py                  # Database layer (asyncpg)
├── utils.py               # Helper functions
├── requirements.txt       # Python dependencies (includes asyncpg)
├── Dockerfile             # Railway deployment
└── .dockerignore         # Docker build exclusions
```

## 🗄️ Database Schema

### Tables

- **participants**: Users with codes (#01, #02...), points, identity keys
- **submissions**: Photo submissions (unique per participant+photo)
- **adjustments**: Manual point changes with audit trail
- **settings**: Key-value config store
- **winners**: Weekly Top 5 snapshots

### Identity Keys

- Telegram ID available: `tg:<user_id>` (best case)
- No Telegram ID: `name:<normalized_display_name>` (fallback)

## 🚀 Railway Deployment

### Prerequisites

1. **Telegram Bot Token**: Get from [@BotFather](https://t.me/BotFather)
2. **Admin User IDs**: Your Telegram user ID (get from [@userinfobot](https://t.me/userinfobot))
3. **Railway Account**: Sign up at [railway.app](https://railway.app)

### Deployment Steps

#### 1. Create Railway Project

- Go to [railway.app](https://railway.app)
- Click "New Project" → "Deploy from GitHub repo"
- Select your repository

#### 2. Add PostgreSQL Plugin

**CRITICAL**: You must add PostgreSQL database:

1. Click on your project
2. Click "+ New" → "Database" → "Add PostgreSQL"
3. Railway will create a Postgres instance and set `DATABASE_URL`

#### 3. Set Environment Variables

Go to your Railway project → Variables → Add:

```env
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
ADMIN_IDS=1064156047,987654321
CHAT_ID=-1001868775086
TOPIC_ID=103380
TZ=Asia/Kolkata
```

**Important:**
- `DATABASE_URL` is automatically set by PostgreSQL plugin
- Replace `BOT_TOKEN` with your actual token
- Replace `ADMIN_IDS` with comma-separated admin user IDs (no spaces)
- `TZ` is optional (only affects logs, not logic)

#### 4. Add Variable Reference (Required!)

The bot service needs access to PostgreSQL's `DATABASE_URL`:

1. Go to bot service → Variables
2. Click "+ New Variable" → "Reference"
3. Select: PostgreSQL service → `DATABASE_URL`
4. Click "Add"

**Without this step, the bot cannot connect to PostgreSQL!**

#### 5. Deploy

- Railway will automatically detect Dockerfile
- Click "Deploy"
- Watch deployment logs
- Bot will create all tables automatically on first run

#### 6. Verify Deployment

Check Railway logs for:
```
✅ Database connection pool created
✅ All tables created/verified
✅ Bot ready!
```

## 📱 Campaign Configuration

```python
CHAT_ID = -1001868775086          # Campaign group chat
TOPIC_ID = 103380                  # PnL Flex Challenge topic
```

No campaign dates - bot counts all photos regardless of time!

## 🎯 Commands

### Public Commands

#### `/pnlrank` (case-insensitive)

Shows Top 5 leaderboard. Works in group or DM.

**Auto-delete**: Bot response disappears after 60 seconds (user command stays).

**Example output (points ON)**:
```
🏆 PnL Flex Challenge - Top 5

🥇 #01 @rohith950 - 45 pts
🥈 #02 @crypto_king - 38 pts
🥉 #03 @trader_pro - 32 pts
4. #04 @moon_boy - 28 pts
5. #05 @hodler - 25 pts
```

**Example output (points OFF)**:
```
🏆 PnL Flex Challenge - Top 5

🥇 #01 @rohith950
🥈 #02 @crypto_king
🥉 #03 @trader_pro
4. #04 @moon_boy
5. #05 @hodler
```

### Admin Commands (DM Only)

#### `/rankerinfo`

Shows Top 10 with full verification details:
```
#01 | @username | 1064156047 | 45 pts
#02 | John Doe | Unknown | 38 pts
...
```

Useful for verifying Telegram IDs and checking participant status.

#### `/add #01 5`

Manually adjust points:
- Positive delta: `/add #01 5` (add 5 points)
- Negative delta: `/add #01 -3` (remove 3 points)

Creates audit record in `adjustments` table.

#### `/stats`

Shows engagement statistics since last reset:
- Total participants
- Total submissions
- Duplicates ignored
- Manual adjustments
- Most active participant
- Average points per user
- Last reset timestamp

#### `/reset`

**DANGEROUS**: Clears all data and restarts codes from #01.

Two-step confirmation:
1. `/reset` → Shows warning with current stats
2. `/reset CONFIRM` → Actually performs reset

Creates backup export before reset (optional).

#### `/pointson` / `/pointsoff`

Toggle points display in public `/pnlrank` command.

#### `/selectwinners <week>`

Save current Top 5 as weekly winners:
```
/selectwinners 1
```

Stores snapshot in `winners` table.

#### `/winners <week>`

View previously saved winners:
```
/winners 1
```

Shows Top 5 from that week with points at time of selection.

#### `/help`

Shows admin command reference.

#### `/test`

Runs health check:
```
✅ BOT HEALTH REPORT (/test)

🔐 Admin: OK
⚙️ Config: OK (CHAT_ID=-1001868775086, TOPIC_ID=103380)
🗄️ Database: Connected
  • Tables: OK
  • Leaderboard query: OK
  • Next code: #05
📦 Batch Worker: OK (initialized)
🧹 Auto Delete: Enabled (60s)

✅ Overall: HEALTHY
```

#### `/testdata`

Tests database transactions with rollback:
```
✅ TRANSACTION TEST PASSED

• Insert participant: OK
• Insert submission: OK
• Select data: OK
• Rollback: OK
• Time: 23.45ms
```

## 🔄 Batch Forwarding Workflow

**Best way to count historical PnL cards!**

### How It Works

1. **Go to PnL Flex Challenge topic**
2. **Select ~180 PnL card photos**
3. **Forward all at once to bot DM**
4. **Bot shows progress** (updates every 10 items):
   ```
   ⏳ Processing forwarded media...

   📨 Received: 45
   ✅ Points added: 40
   ⏭️ Duplicates: 3
   ❌ Failed: 2
   ```

5. **After 12 seconds of no new forwards**, bot sends summary:
   ```
   ✅ Batch processing complete!

   📊 Summary:
   • Received: 180
   • Points added: 165
   • Duplicates ignored: 10
   • Failed/uncredited: 5

   🏆 Current Top 5:
   🥇 #01 John Doe - 45 pts
   🥈 #02 Jane Smith - 38 pts
   ...
   ```

### Requirements

- Must forward TO bot DM (not in group)
- Only admins can use this feature
- Works with privacy settings if "Link account when forwarding" is enabled
- If original poster has strict privacy: those forwards will be marked as "Failed"

### What Gets Counted

✅ **Counted** (MessageOriginUser):
- Forwards from users with "Link account when forwarding" enabled
- Bot extracts: User ID, username, full name

⚠️ **Name-only** (MessageOriginHiddenUser):
- Forwards from users with privacy settings
- Bot extracts: Display name only (no Telegram ID)
- Creates participant with `name:<normalized_name>` identity

❌ **Not Counted** (MessageOriginChat/Channel):
- Forwards from topics/channels themselves
- Cannot determine individual user

## 🔧 How It Works

### Real-Time Topic Tracking

1. User posts PnL card photo in topic
2. Bot extracts:
   - User ID, username, full name
   - Photo file_id
   - Message ID
3. Creates/updates participant
4. Adds submission to database (idempotent via UNIQUE constraint)
5. Increments points
6. Logs success

### Batch Forwarding System

- Uses AsyncIO queue per admin
- Processes photos in transactions
- Shows progress every 10 items or 3 seconds
- Finalizes after 12 seconds of inactivity
- Single progress message (no spam!)
- Final summary includes Top 5 snapshot

### Data Safety

- **PostgreSQL transactions**: ACID guarantees
- **UNIQUE constraints**: Prevent duplicate points
- **Identity keys**: Track users reliably
- **Audit trail**: All manual adjustments logged
- **Idempotent operations**: Safe to retry

## 🔐 Security

### Token Masking

Bot tokens masked in logs:
```
Original: postgresql://user:pass@host/db
Masked:   postgresql://***
```

### Admin Authorization

- All admin commands verify user ID in `ADMIN_IDS`
- DM-only commands reject group usage
- Adjustment commands log admin user ID

## 🐛 Troubleshooting

### Bot Won't Start

**Error: "DATABASE_URL environment variable is not set"**

**Fix**: Add PostgreSQL plugin and Variable Reference:
1. Railway project → "+ New" → "Database" → "Add PostgreSQL"
2. Bot service → Variables → "+ New Variable" → "Reference"
3. Select PostgreSQL → `DATABASE_URL`

### Messages Not Being Tracked

1. **Check topic ID**: Ensure `TOPIC_ID=103380` matches your topic
2. **Verify bot permissions**: Bot must be admin with message access
3. **Check logs**: Look for "📸 Photo in topic" messages

### Batch Forwarding Not Working

1. **Forward TO bot DM**, not in group
2. **Check admin ID**: Your ID must be in `ADMIN_IDS`
3. **Privacy settings**: Original poster needs "Link account when forwarding" enabled
4. **Check logs**: Look for "📨 Forwarded photo in admin DM" messages

### Database Connection Failed

1. **Check DATABASE_URL**: Should be set automatically by PostgreSQL plugin
2. **Check Variable Reference**: Bot service must reference PostgreSQL's DATABASE_URL
3. **Check PostgreSQL status**: Ensure database is running in Railway

## 📊 Point System

- **1 photo = 1 point** (simple and fair)
- Duplicate protection via UNIQUE constraint on (participant_id, photo_file_id)
- No time-based filtering
- Codes assigned in order (#01, #02, ...)
- Codes permanent until `/reset`

## 🔄 Maintenance

### Weekly Winner Selection

At end of each week:
1. Run: `/selectwinners 1` (or 2, 3, 4)
2. Bot saves Top 5 snapshot
3. Share winners in campaign topic

### Reset (New Campaign)

To start fresh:
1. Run: `/reset`
2. Confirm with: `/reset CONFIRM`
3. All participants deleted
4. Codes restart from #01
5. Submissions cleared
6. Adjustments cleared

## 📈 Monitoring

### Health Checks

Run `/test` periodically to verify:
- Database connection
- Table integrity
- Query performance
- Settings status

### Performance

Run `/testdata` to check transaction speed.
Typical result: 20-50ms per transaction.

## 🚨 Critical Reminders

1. ✅ **Add PostgreSQL plugin** - Bot needs DATABASE_URL
2. ✅ **Add Variable Reference** - Bot service → Reference PostgreSQL DATABASE_URL
3. ✅ **Batch forwarding** - Forward TO bot DM, not in group
4. ✅ **Privacy settings** - Users need "Link account when forwarding" enabled
5. ✅ **Auto-delete** - /pnlrank response disappears after 60s
6. ✅ **Codes are permanent** - Until /reset, #01 stays #01

## 📞 Support

For issues:
1. Check `/test` command output
2. Verify Railway environment variables
3. Check PostgreSQL status in Railway
4. Review Railway deployment logs

## 📜 Architecture

**Tech Stack**:
- Python 3.11
- python-telegram-bot 21.0 (Bot API 7.0+)
- asyncpg 0.29.0
- PostgreSQL (Railway plugin)
- Docker deployment

**Design Principles**:
- PostgreSQL as source of truth
- Async/await throughout
- Transaction-based updates
- Idempotent operations
- forward_origin for proper forwards
- Batch queue system

---

**Built for**: BabaEarn PnL Flex Challenge Campaign
**Timezone**: Asia/Kolkata (IST) - for logs only, not logic
**Architecture**: PostgreSQL + Async + Crash-resistant
