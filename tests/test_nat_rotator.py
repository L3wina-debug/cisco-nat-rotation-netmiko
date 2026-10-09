"""Tests for nat_rotator.py with a fake router connection (no network needed)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nat_rotator as nr  # noqa: E402


class FakeConnection:
    def __init__(self, running_config="", fail_times=0, **_params):
        self.running_config = running_config
        self.fail_times = fail_times
        self.sent = []
        self.disconnected = False

    def send_command(self, command):
        self.sent.append(command)
        return self.running_config

    def send_command_timing(self, command):
        self.sent.append(command)
        return ""

    def send_config_set(self, commands):
        if self.fail_times:
            self.fail_times -= 1
            raise TimeoutError("SSH timeout")
        self.sent.extend(commands)
        return "ok"

    def disconnect(self):
        self.disconnected = True


class AddressTests(unittest.TestCase):
    def test_usable_addresses_excludes_network_and_broadcast(self):
        self.assertEqual(nr.usable_addresses("100.10.10.0/29"),
                         [f"100.10.10.{i}" for i in range(1, 7)])

    def test_non_strict_network_is_accepted(self):
        self.assertEqual(len(nr.usable_addresses("100.10.10.5/30")), 2)

    def test_invalid_network_raises(self):
        with self.assertRaises(ValueError):
            nr.usable_addresses("300.1.1.0/24")


class CommandTests(unittest.TestCase):
    def test_correct_ios_clear_command(self):
        self.assertEqual(nr.clear_translations_command(), "clear ip nat translation *")

    def test_pool_detection_uses_the_same_name(self):
        config = "ip nat pool NET 100.10.10.1 100.10.10.1 netmask 255.255.255.248\n"
        self.assertTrue(nr.pool_exists(config, "NET"))
        self.assertFalse(nr.pool_exists(config, "NAT"))
        self.assertEqual(nr.remove_pool_commands("NET"), ["no ip nat pool NET"])

    def test_move_pool_replaces_existing_pool_and_disconnects(self):
        conn = FakeConnection("ip nat pool NET 100.10.10.1 100.10.10.1 netmask 255.255.255.248")
        router = nr.Router({"host": "r1"}, "NET", lambda **p: conn)
        router.move_pool("100.10.10.2", "255.255.255.248")
        self.assertIn("clear ip nat translation *", conn.sent)
        self.assertIn("no ip nat pool NET", conn.sent)
        self.assertIn("ip nat pool NET 100.10.10.2 100.10.10.2 netmask 255.255.255.248", conn.sent)
        self.assertTrue(conn.disconnected)

    def test_move_pool_without_existing_pool_only_creates(self):
        conn = FakeConnection("")
        nr.Router({"host": "r1"}, "NET", lambda **p: conn).move_pool("100.10.10.3", "255.255.255.0")
        self.assertNotIn("no ip nat pool NET", conn.sent)


class RotatorTests(unittest.TestCase):
    def make(self, conn, check):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        router = nr.Router({"host": "r1"}, "NET", lambda **p: conn)
        return nr.Rotator(router, ["100.10.10.1", "100.10.10.2"], "255.255.255.252",
                          Path(tmp.name) / "used.txt", check, sleep=lambda s: None,
                          max_attempts=3, retry_delay=0)

    def test_no_rotation_while_internet_is_up(self):
        rotator = self.make(FakeConnection(), check=lambda: True)
        self.assertIsNone(rotator.step())

    def test_rotation_when_internet_is_down_and_address_logged(self):
        rotator = self.make(FakeConnection(), check=lambda: False)
        self.assertEqual(rotator.step(), "100.10.10.1")
        self.assertIn("100.10.10.1", rotator.used_log.read_text())

    def test_addresses_wrap_around(self):
        rotator = self.make(FakeConnection(), check=lambda: False)
        self.assertEqual([rotator.step() for _ in range(3)],
                         ["100.10.10.1", "100.10.10.2", "100.10.10.1"])

    def test_retry_after_ssh_failure(self):
        rotator = self.make(FakeConnection(fail_times=1), check=lambda: False)
        self.assertEqual(rotator.step(), "100.10.10.2")

    def test_gives_up_after_max_attempts(self):
        rotator = self.make(FakeConnection(fail_times=10), check=lambda: False)
        self.assertIsNone(rotator.step())


if __name__ == "__main__":
    unittest.main()
