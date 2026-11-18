import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from activity import ActivityClock, ActivityStore
from bot import DohwaBot


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'activity.sqlite3'
        self.store = ActivityStore(self.path)
        self.clock = ActivityClock(self.store)
        self.key = (1, 2)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def total(self):
        return self.store.total(*self.key)

    def test_active_voice_without_chat(self):
        self.clock.update(self.key, 0, True)
        self.clock.update(self.key, 3600, True)
        self.assertEqual(self.total(), 3600)

    def test_idle_threshold_discards_entire_gap_and_resumes(self):
        self.clock.update(self.key, 0, True)
        self.clock.update(self.key, 600, False)
        self.clock.update(self.key, 2400, False)
        self.assertEqual(self.total(), 600)
        self.clock.update(self.key, 3000, True)
        self.clock.update(self.key, 3060, True)
        self.assertEqual(self.total(), 660)

    def test_chat_overrides_solo_or_deaf(self):
        self.clock.update(self.key, 0, False, message=True)
        for now in (600, 1200, 1800, 2400):
            self.clock.update(self.key, now, False, message=True)
        self.assertEqual(self.total(), 2400)
        self.clock.update(self.key, 4200, False)
        self.assertEqual(self.total(), 2400)

    def test_short_gap_is_included(self):
        self.clock.update(self.key, 0, False)
        self.clock.update(self.key, 1799, True)
        self.assertEqual(self.total(), 1799)

    def test_exact_threshold_message_starts_fresh(self):
        self.clock.update(self.key, 0, False)
        self.clock.update(self.key, 1800, False, message=True)
        self.assertEqual(self.total(), 0)
        self.clock.update(self.key, 1860, False, message=True)
        self.assertEqual(self.total(), 60)

    def test_leave_and_restart_no_offline_credit(self):
        self.clock.update(self.key, 0, True)
        self.clock.interrupt(60)
        self.clock.update(self.key, 10000, True)
        self.clock.leave(self.key, 10060)
        self.assertEqual(self.total(), 120)
        self.store.db.close()
        self.store = ActivityStore(self.path)
        self.assertEqual(self.total(), 120)

    def test_users_and_guilds_are_separate(self):
        for key in ((1,2),(1,3),(2,2)):
            self.clock.update(key, 0, True)
        self.clock.update((1,2), 100, True)
        self.assertEqual(self.store.total(1,2),100)
        self.assertEqual(self.store.total(1,3),0)
        self.assertEqual(self.store.total(2,2),0)


    def test_adjust_hours_preserves_seconds_and_rejects_overdraw(self):
        self.store.add(self.key, 123.5)
        self.assertEqual(self.store.adjust_hours(*self.key, 2), 7323.5)
        with self.assertRaises(ValueError):
            self.store.adjust_hours(*self.key, -3)
        with self.assertRaises(ValueError):
            self.store.adjust_hours(*self.key, 0)
        self.assertEqual(self.store.adjust_hours(*self.key, -1), 3723.5)
        self.assertEqual(self.store.total(2, 2), 0)
        self.assertEqual(self.store.adjust_hours(1, 3, 1), 3600)


class BotSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_uses_tracker_without_changing_discord_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'activity.sqlite3'
            application = SimpleNamespace(team=None, owner=SimpleNamespace(id=42))
            async with DohwaBot() as bot:
                with (
                    patch.dict('os.environ', {'ACTIVITY_DATABASE_PATH': str(database)}),
                    patch.object(bot, 'application_info', return_value=application),
                    patch.object(bot.tree, 'sync') as sync,
                ):
                    await bot.setup_hook()
                self.assertIsNone(bot.activity)
                self.assertIs(bot.away.store.db, bot.activity_tracker.store.db)
                sync.assert_awaited_once_with()


if __name__ == '__main__':
    unittest.main()
