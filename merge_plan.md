
# Merge & Port Plan

## Goal
Integrate `JM-Changes` (Fraud Prevention) into the new DB-based architecture from `claude/pnl-leaderboard-bot-INcNF`.

## DB Schema Changes
- `photo_hashes` table: `id`, `participant_id`, `phash`, `created_at`.

## Code Changes
### [db.py](file:///Users/jm/.gemini/antigravity/scratch/Pnl-sharing-card_bot/db.py)
- `create_tables`: Add `photo_hashes`.
- `add_phash(participant_id, phash_str)`
- `get_all_hashes()` -> `List[str]`

### [bot.py](file:///Users/jm/.gemini/antigravity/scratch/Pnl-sharing-card_bot/bot.py)
- Resolve conflict by accepting Remote version.
- Re-apply `main()` event loop fix.
- Re-apply `handle_topic_photo` pHash logic (download -> calc hash -> check `db.get_all_hashes`).

## Verification
- Run `test_fraud.py` (need to update it to mock `asyncpg` or run against local DB? Or just verify logic manually).
- Run `run_debug.sh`.
