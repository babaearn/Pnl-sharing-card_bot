def get_default_hashes():
    """Return default structure for hashes.json"""
    return {
        "global_seen_ids": [],
        "phash_db": {}  # {hex_hash: [user_id, message_id, timestamp]}
    }
