"""Voice attendance accounting; no public commands or message content storage."""
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from discord.ext import tasks

IDLE_LIMIT = 1800
LOG = logging.getLogger(__name__)


def format_activity_time(seconds):
    hours, minutes = divmod(max(0, int(seconds)) // 60, 60)
    return f'{hours}시간 {minutes}분'


@dataclass
class Session:
    stamp: float
    voice_active: bool
    pending: float = 0
    inactive: bool = False


class ActivityStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS activity_totals (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            active_seconds REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id, user_id))''')
        self.db.commit()

    def add(self, key, seconds):
        with self.db:
            self.db.execute('''INSERT INTO activity_totals VALUES (?, ?, ?)
                ON CONFLICT(guild_id,user_id) DO UPDATE SET
                active_seconds=active_seconds+excluded.active_seconds''',
                (*key, max(0, seconds)))

    def total(self, guild_id, user_id):
        row = self.db.execute('SELECT active_seconds FROM activity_totals WHERE guild_id=? AND user_id=?',
                              (guild_id, user_id)).fetchone()
        return row[0] if row else 0

class ActivityClock:
    def __init__(self, store):
        self.store = store
        self.sessions = {}

    def advance(self, key, now):
        state = self.sessions[key]
        elapsed = max(0, now - state.stamp)
        if state.voice_active:
            self.store.add(key, elapsed)
        elif not state.inactive:
            state.pending += elapsed
            if state.pending >= IDLE_LIMIT:
                state.pending = 0
                state.inactive = True
        state.stamp = now

    def update(self, key, now, voice_active, message=False):
        if key not in self.sessions:
            self.store.add(key, 0)
            self.sessions[key] = Session(now, voice_active)
        else:
            self.advance(key, now)
        state = self.sessions[key]
        if voice_active or message:
            # A gap strictly shorter than 30 minutes belongs to the active session.
            # A confirmed inactive gap has already been discarded by advance().
            self.store.add(key, state.pending)
            state.pending = 0
            state.inactive = False
        state.voice_active = voice_active

    def leave(self, key, now):
        if key in self.sessions:
            self.advance(key, now)
            state = self.sessions.pop(key)
            self.store.add(key, state.pending)

    def interrupt(self, now):
        # Confirmed time survives; do not infer activity during an outage.
        for key in list(self.sessions):
            self.advance(key, now)
        self.sessions.clear()


class ActivityTracker:
    def __init__(self, bot):
        self.bot = bot
        self.store = ActivityStore(os.getenv('ACTIVITY_DATABASE_PATH',
            str(Path(__file__).resolve().with_name('activity.sqlite3'))))
        self.clock = ActivityClock(self.store)
        self.online = False

    def snapshot(self):
        result = {}
        for guild in self.bot.guilds:
            if guild.unavailable:
                continue
            for channel in (*guild.voice_channels, *guild.stage_channels):
                people = [m for m in channel.members if not m.bot]
                for member in people:
                    voice = member.voice
                    if voice is None:
                        continue
                    active = (len(people) >= 2 and channel != guild.afk_channel
                              and not any((voice.self_mute, voice.mute,
                                           voice.self_deaf, voice.deaf, voice.suppress)))
                    result[(guild.id, member.id)] = active
        return result

    def reconcile(self, message_key=None):
        if not self.online:
            return
        now = time.monotonic()
        current = self.snapshot()
        for key in list(self.clock.sessions):
            if key not in current:
                self.clock.leave(key, now)
        for key, active in current.items():
            self.clock.update(key, now, active, message=key == message_key)

    async def ready(self):
        self.online = True
        self.reconcile()
        if not self.tick.is_running():
            self.tick.start()

    def disconnect(self):
        self.online = False
        self.clock.interrupt(time.monotonic())

    @tasks.loop(seconds=10)
    async def tick(self):
        self.reconcile()

    @tick.error
    async def tick_error(self, error):
        self.disconnect()
        LOG.error('Ȱ�� ���� �ߴ�: ����Ҹ� Ȯ���ϰ� ������ϼ���.', exc_info=error)

    def message(self, message):
        if message.guild and not message.author.bot and not message.webhook_id:
            self.reconcile((message.guild.id, message.author.id))

    def close(self):
        self.tick.cancel()
        self.disconnect()
        self.store.db.close()
