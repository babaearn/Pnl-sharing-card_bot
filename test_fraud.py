
import unittest
from unittest.mock import MagicMock, patch
import io
from PIL import Image
import imagehash
import data_manager
from data_manager import check_is_duplicate, register_new_photo, HASHES_FILE
import os

class TestFraudDetection(unittest.TestCase):
    def setUp(self):
        # Reset hashes.json
        if HASHES_FILE.exists():
            os.remove(HASHES_FILE)
            
    def create_test_image(self, color='red'):
        img = Image.new('RGB', (100, 100), color=color)
        byte_io = io.BytesIO()
        img.save(byte_io, 'PNG')
        byte_io.seek(0)
        return byte_io

    def test_global_deduplication(self):
        # 1. Register Photo A
        register_new_photo('user1', 'file_id_1')
        
        # 2. Check Photo A from user2 (should fail)
        is_dupe, reason = check_is_duplicate('user2', 'file_id_1')
        self.assertTrue(is_dupe)
        self.assertIn("Global Duplicate", reason)
        print("✅ Global Deduplication Test Passed")

    def test_phash_detection(self):
        # 1. Create Base Image
        img1 = self.create_test_image('blue')
        
        # 2. Register Base Image
        register_new_photo('user1', 'file_id_A', img1)
        
        # 3. Create "Modified" Image (same content, new file)
        # We simulate visual similarity by using same content
        # In real world, this would be a crop or slightly different compression
        img2 = self.create_test_image('blue') 
        
        # 4. Check Modified Image from user2
        is_dupe, reason = check_is_duplicate('user2', 'file_id_B', img2)
        
        self.assertTrue(is_dupe)
        self.assertIn("Visual Duplicate", reason)
        print("✅ Visual pHash Test Passed")

if __name__ == '__main__':
    unittest.main()
