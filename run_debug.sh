#!/bin/bash
# Dummy environment variables for debugging
export BOT_TOKEN="dummy_token_12345"
export ADMIN_IDS="123456789,987654321"
export CHAT_ID="-1001234567890"
export TOPIC_ID="5"
# Use a local test DB if possible, or a dummy string that will fail connection but allow import check
export DATABASE_URL="postgresql://user:password@localhost:5432/pnlbot_test"

# Run the bot
python3 bot.py TIMEZONE="Asia/Kolkata"

source venv/bin/activate
python bot.py
