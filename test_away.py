import sqlite3
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, Mock, patch

from away import AwayStore, AwayFeature, request_embed, START_TEXT, END_TEXT
from bot import DohwaBot, bot as configured_bot
from profile_card import ProfileStore


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

    def test_adjusted_hours_are_spendable_and_saved(self):
        self.assertEqual(self.store.adjust_hours(1, 2, 2), 5)
        self.store.request(1, 1, 2, 4)
        self.store.decide(1, 5, True, 1000)
        self.assertEqual(self.store.adjust_hours(1, 2, -1), 0)
        with self.assertRaises(ValueError):
            self.store.adjust_hours(1, 2, -1)
        with self.assertRaises(ValueError):
            self.store.adjust_hours(1, 2, 0)
        self.assertEqual(AwayStore(self.db).available(1, 2), (0, 54000))
        self.assertEqual(self.store.available(2, 2), (0, 0))

    def test_approval_and_activity_adjustment_change_only_usable_activity(self):
        with self.db:
            self.db.execute('UPDATE activity_totals SET active_seconds=active_seconds+1800')
        self.store.request(1, 1, 2, 2)
        self.store.decide(1, 5, True, 1000)
        self.store.expire(8200)
        balance = self.store.balance(1, 2)
        self.assertEqual((balance.activity_seconds, balance.usable_seconds), (55800, 19800))
        self.assertEqual(balance.available_seconds, 3960)
        with self.assertRaises(ValueError):
            self.store.adjust_activity_hours(1, 2, -6)
        adjusted = self.store.adjust_activity_hours(1, 2, -1)
        self.assertEqual((adjusted.activity_seconds, adjusted.usable_seconds), (55800, 16200))
        self.assertEqual(adjusted.available_hours, 0)
        with self.assertRaises(ValueError):
            self.store.request(2, 1, 2, 1)
        self.assertEqual(self.store.adjust_activity_hours(1, 2, 1).available_hours, 1)

    def test_granting_away_credits_does_not_refund_used_activity(self):
        self.store.request(1, 1, 2, 2)
        self.store.decide(1, 5, True, 1000)
        self.store.expire(8200)
        self.store.adjust_hours(1, 2, 1)
        self.assertEqual(self.store.balance(1, 2).usable_seconds, 18000)
        self.store.request(2, 1, 2, 1)
        self.store.decide(2, 5, True, 9000)
        self.assertEqual(self.store.balance(1, 2).usable_seconds, 18000)
        self.assertEqual(self.store.available(1, 2), (1, 54000))

    def test_legacy_approval_migration_keeps_away_balance(self):
        self.store.adjust_hours(1, 2, 2)
        self.store.request(1, 1, 2, 1)
        self.store.decide(1, 5, True, 1000)
        self.store.expire(4600)
        self.store.request(2, 1, 2, 2)
        self.store.decide(2, 5, True, 5000)
        self.db.execute('ALTER TABLE away_requests DROP COLUMN credit_hours_used')
        migrated = AwayStore(self.db)
        self.assertEqual(migrated.available(1, 2), (2, 54000))
        self.assertEqual(migrated.get(1)['credit_hours_used'], 1)
        self.assertEqual(migrated.get(2)['credit_hours_used'], 1)
        migrated.adjust_hours(1, 2, 1)
        self.assertEqual(AwayStore(self.db).get(2)['credit_hours_used'], 1)

    def test_positive_adjustment_adds_to_visible_balance_after_activity_correction(self):
        self.store.request(1, 1, 2, 3)
        self.store.decide(1, 5, True, 1000)
        with self.db:
            self.db.execute('UPDATE activity_totals SET active_seconds=0')
        self.assertEqual(self.store.adjust_hours(1, 2, 2), 2)
        self.assertEqual(self.store.available(1, 2), (2, 0))
        self.assertEqual(self.store.get(1)['status'], 'active')

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

class RoleAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        self.bot = DohwaBot()
        self.bot.owner_id = 5
        self.bot.profile_store = ProfileStore(db)
        for category, role_id in [('서버장', 10), ('총관리자', 20), ('단속팀', 30), ('단속팀장', 40)]:
            self.bot.profile_store.assign(1, category, role_id)
        self.away = AwayFeature.__new__(AwayFeature)
        self.away.bot = self.bot

    async def test_command_access_for_each_registered_command(self):
        public = {'정보', '외활요청', '외활시간측정'}
        permits = {'외활권 부여', '외활권 회수'}
        for user_id, roles, allowed in [
            (5, (), None),
            (6, (40, 10), None),
            (6, (40, 20), public | permits),
            (6, (30,), public),
            (6, (40,), public),
            (6, (), public),
        ]:
            for command in configured_bot.tree.walk_commands():
                if not hasattr(command, 'callback'):
                    continue
                interaction = self.interaction(1, user_id, roles, command)
                with self.subTest(roles=roles, command=command.qualified_name):
                    self.assertEqual(
                        await self.bot.tree.interaction_check(interaction),
                        allowed is None or command.qualified_name in allowed,
                    )

    async def test_buttons_use_current_guild_role_mappings(self):
        for guild, user_id, roles, allowed in [
            (1, 5, (), True),
            (1, 6, (10,), True),
            (1, 6, (20,), True),
            (1, 6, (40, 30), True),
            (1, 6, (40,), False),
            (1, 6, (), False),
            (2, 6, (10, 20, 30), False),
            (None, 5, (), False),
        ]:
            interaction = self.interaction(guild, user_id, roles)
            with self.subTest(guild=guild, roles=roles):
                self.assertEqual(await self.away.authorize(interaction), allowed)
                if not allowed:
                    interaction.response.send_message.assert_awaited_once()

        self.bot.profile_store.assign(1, '단속팀', 31)
        self.assertFalse(self.away.manager(self.interaction(1, 6, (30,))))
        self.assertTrue(self.away.manager(self.interaction(1, 6, (31,))))

    async def test_measure_command_reports_manual_away_adjustments_in_embed(self):
        db = self.bot.profile_store.db
        db.execute('CREATE TABLE activity_totals(guild_id INTEGER, user_id INTEGER, active_seconds REAL)')
        db.execute('INSERT INTO activity_totals VALUES (1,2,9000)')
        db.commit()
        store = AwayStore(db)
        member = N(id=2, mention='<@2>')
        interaction = self.interaction(1, 5, ())
        interaction.response.defer = AsyncMock()
        interaction.followup = N(send=AsyncMock())
        adjust = configured_bot.tree.get_command('외활시간추가감소')
        measure = configured_bot.tree.get_command('외활시간측정')
        with (
            patch.object(configured_bot, 'away', N(store=store)),
            patch.object(configured_bot, 'activity_tracker', N(reconcile=Mock())),
        ):
            for change, color, time in [(1, 0x2ECC71, '1시간 30분'), (-1, 0xE74C3C, '0시간')]:
                await adjust.callback(interaction, 유저=member, 시간=change)
                await measure.callback(interaction, 유저=member)
                embed = interaction.response.send_message.await_args.kwargs['embed']
                self.assertEqual(embed.colour.value, color)
                self.assertEqual([(field.name, field.value) for field in embed.fields],
                                 [('유저', '<@2>'), ('외활 가능 시간', time)])

    def interaction(self, guild_id, user_id, roles, command=None):
        return N(
            guild_id=guild_id,
            user=N(id=user_id, roles=[N(id=role_id) for role_id in roles]),
            command=command,
            response=N(send_message=AsyncMock()),
        )


if __name__ == '__main__':
    unittest.main()
