import os
import sys
import tempfile
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.instance_lock import InstanceLock, InstanceLockedError


class InstanceLockTests(unittest.TestCase):
    def test_second_lock_is_rejected_until_first_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            first = InstanceLock(directory).acquire()
            second = InstanceLock(directory)
            try:
                with self.assertRaises(InstanceLockedError):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()


if __name__ == "__main__":
    unittest.main()