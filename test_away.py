import sqlite3
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch

from away import AwayStore, AwayFeature, request_embed, START_TEXT, END_TEXT


def run_mocked(coroutine):
    # All I/O is mocked; no OS socket/event-loop dependency in these unit tests.
    try:
        coroutine.send(None)
    except StopIteration:
        return
    raise AssertionError('Unexpected non-mocked I/O')


class AwayTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE activity_totals(guild_id INTEGER,user_id INTEGER,active_seconds REAL)')
        self.db.execute('INSERT INTO activity_totals VALUES (1,2,54000)')
        self.db.commit()
        self.store = AwayStore(self.db)
        self.store.configure(1,99)

    def tearDown(self):
        self.db.close()

    def test_missing_and_insufficient_auto_rejection(self):
        with self.assertRaises(ValueError):
            self.store.request(1,1,3,1)
        with self.assertRaises(ValueError):
            self.store.request(2,1,2,4)
        self.assertEqual(self.store.rows('SELECT * FROM away_requests'),[])

    def test_approval_spends_once(self):
        self.store.request(10,1,2,2)
        self.assertEqual(self.store.available(1,2)[0],3)
        row = self.store.decide(10,5,True,100)
        self.assertEqual(row['ends'],7300)
        self.assertEqual(self.store.available(1,2)[0],1)
        with self.assertRaises(ValueError):
            self.store.decide(10,5,True,200)
        self.assertEqual(self.store.available(1,2)[0],1)

    def test_reject_does_not_spend(self):
        self.store.request(10,1,2,3)
        row = self.store.decide(10,5,False,100)
        self.assertEqual(row['status'],'rejected')
        self.assertEqual(self.store.available(1,2)[0],3)
        self.store.request(11,1,2,3)

    def test_duplicate_pending_and_active_requests(self):
        self.store.request(10,1,2,1)
        self.store.request(10,1,2,1)
        with self.assertRaises(ValueError):
            self.store.request(11,1,2,1)
        self.store.decide(10,5,True,0)
        with self.assertRaises(ValueError):
            self.store.request(11,1,2,1)

    def test_restart_expiry_and_balance(self):
        self.store.request(10,1,2,2)
        self.store.decide(10,5,True,100)
        self.store = AwayStore(self.db)
        self.store.expire(7299)
        self.assertEqual(self.store.get(10)['status'],'active')
        self.store.expire(7300)
        self.store.expire(9999)
        self.assertEqual(self.store.get(10)['status'],'ended')
        self.assertEqual(self.store.available(1,2)[0],1)
        self.store.request(11,1,2,1)

    def test_scoping_and_integer_hours(self):
        self.assertEqual(self.store.available(2,2)[0],0)
        for hours in (0,-1,1.5,8761):
            with self.assertRaises(ValueError):
                self.store.request(10,1,2,hours)

    def test_colors_and_user_fields(self):
        row = self.store.request(10,1,2,1)
        self.assertEqual(request_embed(row).colour.value,0xF1C40F)
        self.assertEqual(request_embed(row).fields[0].value,'<@2>')
        row = self.store.decide(10,5,True,0)
        self.assertEqual(request_embed(row).colour.value,0x2ECC71)
        self.store.expire(3600)
        self.assertEqual(request_embed(self.store.get(10)).colour.value,0xE74C3C)

    def test_delivery_lifecycle_no_repeated_dm(self):
        feature = AwayFeature.__new__(AwayFeature)
        feature.store = self.store
        feature.dm = AsyncMock()
        message = N(edit=AsyncMock())
        channel = N(guild=N(id=1),send=AsyncMock(side_effect=[N(id=100),N(id=200)]),
                    get_partial_message=lambda mid: message)
        feature.bot = N(get_channel=lambda cid: channel)
        with patch('away.ReviewView',return_value=None), patch('away.RecallView',return_value=None):
            row = self.store.request(10,1,2,1)
            run_mocked(feature.deliver(row))
            self.assertEqual(self.store.get(10)['message_id'],100)
            row = self.store.decide(10,5,True,0)
            run_mocked(feature.deliver(row))
            self.assertEqual(feature.dm.await_args.args[1],START_TEXT)
            run_mocked(feature.deliver(self.store.get(10)))
            self.assertEqual(feature.dm.await_count,1)
            self.store.expire(3600)
            run_mocked(feature.deliver(self.store.get(10)))
            self.assertEqual(feature.dm.await_args.args[1],END_TEXT)
            self.assertEqual(self.store.get(10)['end_message'],200)
            run_mocked(feature.deliver(self.store.get(10)))
            self.assertEqual(feature.dm.await_count,2)
            self.assertEqual(channel.send.await_count,2)

    def test_dm_mentions_target(self):
        feature = AwayFeature.__new__(AwayFeature)
        user = N(send=AsyncMock())
        feature.bot = N(get_user=lambda uid:user)
        run_mocked(feature.dm({'user_id':2},END_TEXT))
        self.assertEqual(user.send.await_args.args[0],'<@2>\n'+END_TEXT)

    def test_manager_restricts_buttons(self):
        feature = AwayFeature.__new__(AwayFeature)
        feature.bot = N(owner_id=5)
        self.assertTrue(feature.manager(N(guild=N(id=1),user=N(id=5))))
        self.assertFalse(feature.manager(N(guild=N(id=1),user=N(id=9))))
        self.assertFalse(feature.manager(N(guild=None,user=N(id=5))))


if __name__ == '__main__':
    unittest.main()
