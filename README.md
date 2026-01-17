# PnL Flex Challenge Leaderboard Bot 🏆

Production-ready Telegram bot for tracking PnL Share Card submissions with crash-resistant architecture and automatic sync on every restart.

## 🌟 Key Features

- **Crash-Resistant Architecture**: Automatic sync on every restart (crashes, redeployments, downtime)
- **Idempotent Processing**: Uses `message_id` as primary key to prevent duplicate counting
- **Duplicate Photo Detection**: Tracks `photo_id` to prevent same image from being counted twice
- **Atomic JSON Writes**: Prevents data corruption with automatic backups
- **Admin Notifications**: DM alerts after each sync with statistics
- **Weekly Tracking**: 4-week campaign with individual weekly leaderboards
- **Configurable Points Display**: Toggle points visibility in public leaderboard
- **Token Masking**: Sensitive data automatically masked in logs

## 📁 Project Structure

```
pnl-leaderboard-bot/
├── bot.py                      # Main bot logic with smart_backfill
├── data_manager.py             # Atomic JSON operations
├── leaderboard.py              # Ranking and display formatting
├── utils.py                    # Helper functions (week calc, admin checks)
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Railway deployment configuration
├── .dockerignore              # Docker build exclusions
└── data/                       # Created at runtime
    ├── submissions.json        # User submissions and points
    ├── submissions.json.backup # Auto-backup
    ├── winners.json            # Weekly winners (Top 5)
    ├── winners.json.backup     # Auto-backup
    ├── config.json             # Bot configuration
    └── config.json.backup      # Auto-backup
```

## 🚀 Railway Deployment

### Prerequisites

1. **Telegram Bot Token**: Get from [@BotFather](https://t.me/BotFather)
2. **Admin User IDs**: Your Telegram user ID (get from [@userinfobot](https://t.me/userinfobot))
3. **Railway Account**: Sign up at [railway.app](https://railway.app)

### Deployment Steps

1. **Create New Railway Project**
   - Go to [railway.app](https://railway.app)
   - Click "New Project" → "Deploy from GitHub repo"
   - Select your repository

2. **Set Environment Variables**

   Go to your Railway project → Variables → Add the following:

   ```env
   BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   ADMIN_IDS=1064156047,987654321,123456789
   CHAT_ID=-1001868775086
   TOPIC_ID=103380
   TIMEZONE=Asia/Kolkata
   ```

   **Important**:
   - Replace `BOT_TOKEN` with your actual bot token
   - Replace `ADMIN_IDS` with comma-separated admin user IDs (no spaces)
   - `CHAT_ID` and `TOPIC_ID` are pre-configured for the campaign

3. **Deploy**
   - Railway will automatically detect the Dockerfile
   - Click "Deploy"
   - Wait for deployment to complete

4. **Verify Deployment**
   - Check Railway logs for: `🤖 Bot initialized, running startup tasks...`
   - You should receive a DM from the bot with sync notification
   - Test with `/pnlrank` command in the Telegram topic

## 📱 Campaign Configuration

```python
CHAT_ID = -1001868775086          # Campaign group chat
TOPIC_ID = 103380                  # PnL Flex Challenge topic
CAMPAIGN_START = 2025-01-15 00:01:00 IST
CAMPAIGN_END = 2025-02-11 23:59:59 IST
DURATION = 28 days (4 weeks)
WEEKLY_REWARD = $5 USDT per winner (Top 5)
```

### Week Breakdown

- **Week 1**: Jan 15 - Jan 21
- **Week 2**: Jan 22 - Jan 28
- **Week 3**: Jan 29 - Feb 4
- **Week 4**: Feb 5 - Feb 11

## 🎯 Commands

### Public Commands (Available to Everyone)

#### `/pnlrank`
Shows Top 5 leaderboard for current week. Case-insensitive (`/PNLRank`, `/PNLRANK`, `/pnlRank` all work).

**Example output (points ON)**:
```
🏆 PnL Flex Challenge - Week 1

🥇 @rohith950 - 45 points
🥈 @crypto_king - 38 points
🥉 @trader_pro - 32 points
🏅 @moon_boy - 28 points
🏅 @hodler - 25 points
```

**Example output (points OFF)**:
```
🏆 PnL Flex Challenge - Week 1

🥇 @rohith950
🥈 @crypto_king
🥉 @trader_pro
🏅 @moon_boy
🏅 @hodler
```

### Admin Commands (DM Only)

#### `/adminboard`
Shows detailed Top 10 with user IDs and configuration status.

#### `/eng`
Displays engagement statistics including total participants, submissions, most active users, and averages.

#### `/pointson`
Enable points display in public `/pnlrank` command.

#### `/pointsoff`
Disable points display in public `/pnlrank` command (ranks only).

#### `/selectwinners <week>`
Automatically select and save Top 5 winners for specified week.

**Example**: `/selectwinners 1`

#### `/winners <week>`
View previously selected winners for a specific week.

**Example**: `/winners 1`

#### `/backfill`
Manually trigger the backfill/sync process (normally runs automatically on startup).

#### `/stats`
Show campaign statistics including total participants and submissions.

## 🔧 How It Works

### Crash-Resistant Backfill

On **every** bot startup (first deploy, crash, Railway redeploy):

1. **Load existing data** from `submissions.json`
2. **Track message IDs** already processed
3. **Real-time tracking** starts immediately for new messages
4. **Send notification** to all admins with sync results

### Message Processing Flow

1. User posts PnL card image in campaign topic
2. Bot extracts:
   - `message_id` (unique identifier)
   - `photo_id` (for duplicate detection)
   - User info (ID, username, full name)
   - Timestamp (for week calculation)
3. Checks:
   - ✅ Message ID not already processed
   - ✅ Photo ID not already submitted by user
   - ✅ Message within campaign period
4. Awards 1 point and saves atomically

### Data Safety Features

- **Atomic Writes**: Temp file → Backup → Atomic move
- **Automatic Backups**: Created before every update
- **Corruption Recovery**: Falls back to backup if JSON corrupted
- **Idempotent Operations**: Safe to run multiple times

## 📊 Point System

- **1 image = 1 point** (regardless of content)
- Duplicate photo detection prevents same image counting twice
- Points tracked both weekly and all-time
- Weekly leaderboards reset each week
- Top 5 each week eligible for $5 USDT reward

## 🔐 Security

### Token Masking
All bot tokens and sensitive user IDs are automatically masked in logs:
```
Original: Bot token 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
Masked:   Bot token [BOT_TOKEN_MASKED]
```

### Admin Authorization
- All admin commands restricted to users in `ADMIN_IDS`
- Sensitive commands require DM (not in groups)
- User IDs validated before executing privileged operations

## 🐛 Troubleshooting

### Bot Not Responding

1. **Check Railway Logs**:
   - Go to Railway project → Deployments → View Logs
   - Look for errors or connection issues

2. **Verify Environment Variables**:
   - Ensure `BOT_TOKEN` is correct
   - Check `CHAT_ID` and `TOPIC_ID` are set

3. **Test Bot Token**:
   ```bash
   curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getMe
   ```

### Messages Not Being Tracked

1. **Verify Bot Permissions**:
   - Bot must be admin in the group
   - Bot needs permission to read messages in topic

2. **Check Campaign Period**:
   - Messages outside Jan 15 - Feb 11, 2025 are ignored

3. **Check Topic ID**:
   - Ensure `TOPIC_ID=103380` is correct
   - Only messages in this specific topic are tracked

### Data Corruption

If JSON files become corrupted:
1. Bot automatically attempts to restore from `.backup` files
2. If that fails, creates fresh structure
3. Check logs for restoration messages

Manual recovery:
```bash
# In Railway console or locally
cd /app/data
cp submissions.json.backup submissions.json
```

## 📈 Monitoring

### Admin Notifications

After each restart, admins receive a DM with:
- Total messages found in topic
- Number of new submissions added
- Current top 3 leaderboard
- Sync duration

### Log Monitoring

Key log messages to watch for:
- ✅ `Bot initialized, running startup tasks...`
- ✅ `Backfill complete in X.Xs`
- ✅ `Sent sync notification to admin`
- ⚠️ `Duplicate photo detected` (normal)
- ❌ `Error fetching messages` (needs attention)

## 🔄 Maintenance

### Weekly Winner Selection

At the end of each week:
1. DM the bot: `/selectwinners <week>`
2. Bot responds with Top 5
3. Winners saved to `winners.json`
4. Share winners in the campaign topic

### Configuration Changes

To toggle points display:
- **Enable**: `/pointson`
- **Disable**: `/pointsoff`

Changes apply immediately to future `/pnlrank` commands.

## 📝 Data Files

### submissions.json
Contains all user submissions, points, and campaign stats.

### winners.json
Stores Top 5 winners for each week after `/selectwinners` is run.

### config.json
Bot configuration including points display setting.

All files have automatic `.backup` versions created before updates.

## 🚨 Critical Reminders

1. ✅ **Bot syncs on EVERY restart** - no data loss from crashes
2. ✅ **Message IDs prevent duplicates** - safe to restart anytime
3. ✅ **Photo IDs catch reposts** - same image won't count twice
4. ✅ **Atomic writes prevent corruption** - crash during save is safe
5. ✅ **Backups created automatically** - recovery is built-in
6. ✅ **Admin notifications** - you'll know when sync completes

## 📞 Support

For issues or questions:
1. Check Railway logs first
2. Verify environment variables
3. Test bot with `/pnlrank` command
4. Check admin DMs for sync notifications

## 📜 License

Production deployment for BabaEarn PnL Flex Challenge Campaign (Jan 15 - Feb 11, 2025).

---

**Built with**: Python 3.11 | python-telegram-bot 21.0 | Railway
**Timezone**: Asia/Kolkata (IST)
**Architecture**: Crash-resistant with automatic recovery
