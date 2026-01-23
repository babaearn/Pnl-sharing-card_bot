# Bug Fixes Applied - PnL Leaderboard Bot

**Date:** 2026-01-23
**Branch:** claude/pnl-leaderboard-bot-INcNF

## Summary

Applied fixes for **8 validation bugs** and **1 code quality issue** identified during deep diagnosis.

---

## Changes Made

### 1. Added MAX_WEEK Constant
**File:** `utils.py:31-32`
```python
# Maximum week number (campaign has 4 weeks, but allow up to 10 for extensions)
MAX_WEEK = 10
```

### 2. Updated Imports
**File:** `bot.py:38`
```python
from utils import (
    ...
    MAX_WEEK,  # NEW
    ...
)
```

### 3. Fixed Week Validation in `/rankerinfo`
**File:** `bot.py:585-586`
**Before:**
```python
if week < 1:
    await update.message.reply_text("❌ Week number must be 1 or greater")
```
**After:**
```python
if week < 1 or week > MAX_WEEK:
    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
```

### 4. Fixed Week Validation in `/add`
**File:** `bot.py:691-696`
**Added:**
```python
# Validate week number
if week_number < 1 or week_number > MAX_WEEK:
    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
    return
```

### 5. Fixed Week Validation in `/breakdown`
**File:** `bot.py:1125-1127`
**Before:**
```python
if week < 1:
    await update.message.reply_text("❌ Week number must be 1 or greater")
```
**After:**
```python
if week < 1 or week > MAX_WEEK:
    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
```

### 6. Fixed Week Validation in `/current`
**File:** `bot.py:1009-1011`
**Before:**
```python
if week_number < 1:
    await update.message.reply_text("❌ Week number must be 1 or greater")
```
**After:**
```python
if week_number < 1 or week_number > MAX_WEEK:
    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
```

### 7. Fixed Week Validation in `/setweek`
**File:** `bot.py:1047-1049`
**Before:**
```python
if week_number < 1:
    await update.message.reply_text("❌ Week number must be 1 or greater")
```
**After:**
```python
if week_number < 1 or week_number > MAX_WEEK:
    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
```

### 8. Fixed Week Validation in `/bulkadd`
**File:** `bot.py:777-782`
**Added:**
```python
# Validate week number
if week_number < 1 or week_number > MAX_WEEK:
    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
    return
```

### 9. Fixed Week Validation in `/selectwinners`
**File:** `bot.py:1293-1296`
**Before:**
```python
if week < 1 or week > 4:
    raise ValueError()
except ValueError:
    await update.message.reply_text("❌ Week must be 1-4")
```
**After:**
```python
if week < 1 or week > MAX_WEEK:
    await update.message.reply_text(f"❌ Week must be between 1 and {MAX_WEEK}")
    return
except ValueError:
    await update.message.reply_text("❌ Week number must be a number")
```

### 10. Fixed Week Validation in `/winners`
**File:** `bot.py:1347-1350`
**Before:**
```python
if week < 1 or week > 4:
    raise ValueError()
except ValueError:
    await update.message.reply_text("❌ Week must be 1-4")
```
**After:**
```python
if week < 1 or week > MAX_WEEK:
    await update.message.reply_text(f"❌ Week must be between 1 and {MAX_WEEK}")
    return
except ValueError:
    await update.message.reply_text("❌ Week number must be a number")
```

### 11. Added Database Helper Function
**File:** `db.py:307-320`
**Added:**
```python
async def get_participant_id_by_code(code: str) -> Optional[int]:
    """
    Get participant ID by code.

    Args:
        code: Participant code (e.g., '#01')

    Returns:
        int: participant_id or None if not found
    """
    async with _pool.acquire() as conn:
        participant_id = await conn.fetchval(
            'SELECT id FROM participants WHERE code = $1',
            code
        )
        return participant_id
```

### 12. Fixed Direct Database Access in `/selectwinners`
**File:** `bot.py:1307-1315`
**Before:**
```python
# Get participant ID from database
async with db._pool.acquire() as conn:
    participant_id = await conn.fetchval(
        'SELECT id FROM participants WHERE code = $1',
        entry['code']
    )
```
**After:**
```python
# Get participant ID using db helper
participant_id = await db.get_participant_id_by_code(entry['code'])

if participant_id is None:
    logger.error(f"Could not find participant ID for code {entry['code']}")
    continue
```

---

## Files Modified

1. ✅ `utils.py` - Added MAX_WEEK constant
2. ✅ `bot.py` - Fixed 10 validation issues across 8 commands
3. ✅ `db.py` - Added get_participant_id_by_code() helper

---

## Testing Checklist

### Week Validation Tests
- [ ] `/rankerinfo 999` → should reject with "must be between 1 and 10"
- [ ] `/rankerinfo 0` → should reject with "must be between 1 and 10"
- [ ] `/add #01 999 5` → should reject
- [ ] `/breakdown #01 999` → should reject
- [ ] `/current week 999` → should reject
- [ ] `/setweek 999` → should reject
- [ ] `/selectwinners 999` → should reject
- [ ] `/winners 999` → should reject

### Valid Week Tests
- [ ] `/rankerinfo 1` → should work
- [ ] `/rankerinfo 10` → should work
- [ ] `/add #01 current 5` → should work
- [ ] `/breakdown #01 2` → should work
- [ ] `/current week 2` → should work

### Database Helper Test
- [ ] `/selectwinners 1` → should use db helper correctly

---

## Impact Assessment

### Security Impact
✅ **POSITIVE** - Prevents invalid week numbers from being stored in database

### Performance Impact
✅ **NEUTRAL** - No performance change, just validation

### User Experience Impact
✅ **POSITIVE** - Better error messages with clear bounds

### Code Quality Impact
✅ **POSITIVE** - More maintainable, consistent validation across all commands

---

## Rollback Plan

If issues arise, revert with:
```bash
git revert HEAD
```

Or manually:
1. Remove `MAX_WEEK` from `utils.py`
2. Remove `MAX_WEEK` from `bot.py` imports
3. Revert validation checks to `>= 1` only
4. Remove `get_participant_id_by_code()` from `db.py`
5. Restore direct database access in `/selectwinners`

---

## Next Steps

1. **Test in staging** - Run all test scenarios above
2. **Monitor logs** - Check for validation rejections
3. **Update documentation** - Mention MAX_WEEK limit in /help
4. **Consider config** - Move MAX_WEEK to environment variable if needed

---

**Status:** ✅ Ready for Testing
**Risk Level:** LOW (only adds validation, no logic changes)
