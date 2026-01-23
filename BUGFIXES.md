# Bug Fixes for PnL Leaderboard Bot

## Issues Found and Proposed Fixes

### 1. Missing Week Number Upper Bound Validation

**Problem:** Commands accept week numbers up to infinity (e.g., week 999999)

**Affected Commands:**
- `/add`
- `/breakdown`
- `/rankerinfo`
- `/removedata` (disabled)
- `/undodata` (disabled)
- `/clearadjustments` (disabled)
- `/current`
- `/setweek`

**Proposed Fix:**
Add a constant `MAX_WEEK = 10` (or campaign-appropriate limit) and validate:
```python
if week_number < 1 or week_number > MAX_WEEK:
    await update.message.reply_text(f"❌ Week number must be between 1 and {MAX_WEEK}")
    return
```

### 2. Hardcoded Week Limit in `/selectwinners`

**Problem:** Cannot save winners beyond week 4
```python
if week < 1 or week > 4:
    raise ValueError()
```

**Proposed Fix:**
Use the same `MAX_WEEK` constant:
```python
if week < 1 or week > MAX_WEEK:
    await update.message.reply_text(f"❌ Week must be between 1 and {MAX_WEEK}")
    return
```

### 3. Direct Database Access in `/selectwinners`

**Problem:** Bypasses db.py abstraction layer
```python
async with db._pool.acquire() as conn:
    participant_id = await conn.fetchval(...)
```

**Proposed Fix:**
Add helper function to db.py:
```python
async def get_participant_id_by_code(code: str) -> Optional[int]:
    """Get participant ID by code."""
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            'SELECT id FROM participants WHERE code = $1',
            code
        )
```

### 4. No Comprehensive Test Suite

**Problem:** Manual testing required for each change

**Proposed Fix:**
Create `test_bot_commands.py` with pytest to test:
- Week validation
- Point calculations
- Command permissions
- Database operations

---

## Severity Assessment

| Issue | Severity | Impact | Fix Difficulty |
|-------|----------|--------|---------------|
| Week upper bound | MEDIUM | User confusion, invalid data | EASY |
| Hardcoded limit | LOW | Campaign extension blocker | EASY |
| Direct DB access | LOW | Code maintainability | EASY |
| No tests | MEDIUM | Bug detection | MEDIUM |

---

## Implementation Priority

1. ✅ **HIGH:** Add week number validation constant
2. ✅ **HIGH:** Fix all validation checks
3. ✅ **MEDIUM:** Add db.py helper for participant lookup
4. ✅ **MEDIUM:** Fix `/selectwinners` validation
5. ⏸️ **LOW:** Create test suite (future work)
