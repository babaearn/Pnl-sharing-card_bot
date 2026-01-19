#!/bin/bash
export BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
export ADMIN_IDS="12345678,87654321"
export CHAT_ID="-1001234567890"
export TOPIC_ID="12345"
export TIMEZONE="Asia/Kolkata"

source venv/bin/activate
python bot.py
