"""Persistent external-activity requests. One running bot process per database."""
import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import tasks

LOG = logging.getLogger(__name__)
END_TEXT = '외활이 끝났습니다! 다시 서버로 돌아오세요!'
START_TEXT = '외활이 허락되었습니다! 지금부터 시간이 지나갑니다.'


class AwayStore:
    def __init__(self, db):
        self.db = db
        with db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS away_settings (
                    guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS away_requests (
                    id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL, hours INTEGER NOT NULL CHECK(hours>0),
                    activity_seconds REAL NOT NULL, channel_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', message_id INTEGER,
                    reviewer_id INTEGER, started REAL, ends REAL,
                    start_dm TEXT NOT NULL DEFAULT 'pending',
                    end_dm TEXT NOT NULL DEFAULT 'pending', end_message INTEGER,
                    dirty INTEGER NOT NULL DEFAULT 1, last_call REAL NOT NULL DEFAULT 0);
                CREATE UNIQUE INDEX IF NOT EXISTS away_one_open ON away_requests(guild_id,user_id)
                    WHERE status IN ('pending','active');
            ''')

    def rows(self, sql, args=()):
        cur = self.db.execute(sql, args)
        names = [col[0] for col in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def get(self, request_id):
        rows = self.rows('SELECT * FROM away_requests WHERE id=?', (request_id,))
        return rows[0] if rows else None

    def update(self, request_id, **values):
        allowed = {'message_id','start_dm','end_dm','end_message','dirty','last_call'}
        if not values or not set(values) <= allowed:
            raise ValueError('Invalid update')
        with self.db:
            self.db.execute('UPDATE away_requests SET '+','.join(f'{k}=?' for k in values)+' WHERE id=?',
                            (*values.values(),request_id))

    def channel(self, guild_id):
        row = self.db.execute('SELECT channel_id FROM away_settings WHERE guild_id=?',(guild_id,)).fetchone()
        return row[0] if row else None

    def configure(self, guild_id, channel_id):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO away_settings VALUES (?,?)',(guild_id,channel_id))

    def available(self, guild_id, user_id):
        row = self.db.execute('SELECT active_seconds FROM activity_totals WHERE guild_id=? AND user_id=?',
                              (guild_id,user_id)).fetchone()
        seconds = row[0] if row else 0
        used = self.db.execute("SELECT COALESCE(SUM(hours),0) FROM away_requests WHERE guild_id=? AND user_id=? AND status IN ('active','ended')",
                               (guild_id,user_id)).fetchone()[0]
        return max(0, int(seconds // 18000) - used), seconds

    def request(self, request_id, guild_id, user_id, hours):
        if not isinstance(hours,int) or not 1 <= hours <= 8760:
            raise ValueError('시간은 1~8760 사이의 정수로 입력해 주세요.')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing = self.get(request_id)
            if existing:
                return existing
            if self.db.execute("SELECT 1 FROM away_requests WHERE guild_id=? AND user_id=? AND status IN ('pending','active')",
                               (guild_id,user_id)).fetchone():
                raise ValueError('이미 대기 중이거나 진행 중인 외활이 있습니다.')
            channel = self.channel(guild_id)
            if channel is None:
                raise ValueError('아직 외활요청방이 지정되지 않았습니다.')
            available, seconds = self.available(guild_id,user_id)
            if hours > available:
                raise ValueError(f'활동시간이 부족하여 자동 거절되었습니다. 외활 가능 시간: {available}시간')
            self.db.execute('INSERT INTO away_requests(id,guild_id,user_id,hours,activity_seconds,channel_id) VALUES (?,?,?,?,?,?)',
                            (request_id,guild_id,user_id,hours,seconds,channel))
        return self.get(request_id)

    def decide(self, request_id, reviewer_id, approve, now):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.get(request_id)
            if row is None or row['status'] != 'pending':
                raise ValueError('이미 처리되었거나 없는 요청입니다.')
            if approve and row['hours'] > self.available(row['guild_id'],row['user_id'])[0]:
                raise ValueError('현재 외활 가능 시간이 부족합니다. 거절 처리해 주세요.')
            self.db.execute('UPDATE away_requests SET status=?,reviewer_id=?,started=?,ends=?,dirty=1 WHERE id=?',
                            ('active' if approve else 'rejected',reviewer_id,
                             now if approve else None,now+row['hours']*3600 if approve else None,request_id))
        return self.get(request_id)

    def expire(self, now):
        with self.db:
            self.db.execute("UPDATE away_requests SET status='ended',dirty=1 WHERE status='active' AND ends<=?",(now,))


def request_embed(row):
    status = row['status']
    title = {'pending':'외활 요청','active':'외활 수락','rejected':'외활 거절','ended':'외활 종료'}[status]
    color = 0xF1C40F if status == 'pending' else 0x2ECC71 if status == 'active' else 0xE74C3C
    embed = discord.Embed(title=title,colour=color)
    embed.add_field(name='유저',value=f"<@{row['user_id']}>",inline=False)
    embed.add_field(name='외활 요청시간',value=f"{row['hours']}시간",inline=False)
    embed.add_field(name='활동 시간',value=f"{int(row['activity_seconds']//3600)}시간",inline=False)
    if row['ends']:
        embed.add_field(name='외활 종료 예정',value=f"<t:{int(row['ends'])}:F>",inline=False)
    notes = [f"요청 번호: {row['id']}"]
    if row['start_dm'] == 'blocked':
        notes.append('시작 DM 차단됨 — DM 허용 후 관리자에게 알려 주세요.')
    if row['end_dm'] == 'blocked':
        notes.append('종료 DM 차단됨 — 부르기로 다시 전송할 수 있습니다.')
    embed.set_footer(text=' | '.join(notes))
    return embed


class ReviewView(discord.ui.View):
    def __init__(self, feature):
        super().__init__(timeout=None)
        self.feature = feature

    @discord.ui.button(label='수락',style=discord.ButtonStyle.success,custom_id='dohwa:away:approve:v1')
    async def approve(self, interaction, button):
        await self.feature.review(interaction, True)

    @discord.ui.button(label='거절',style=discord.ButtonStyle.danger,custom_id='dohwa:away:reject:v1')
    async def reject(self, interaction, button):
        await self.feature.review(interaction, False)


class RecallView(discord.ui.View):
    def __init__(self, feature):
        super().__init__(timeout=None)
        self.feature = feature

    @discord.ui.button(label='부르기',style=discord.ButtonStyle.primary,custom_id='dohwa:away:recall:v1')
    async def recall(self, interaction, button):
        await self.feature.recall(interaction)


class AwayFeature:
    def __init__(self, bot):
        self.bot = bot
        self.store = AwayStore(bot.activity.store.db)
        self.lock = asyncio.Lock()
        bot.add_view(ReviewView(self))
        bot.add_view(RecallView(self))

    def manager(self, interaction):
        return interaction.guild is not None and interaction.user.id == self.bot.owner_id

    async def authorize(self, interaction):
        if self.manager(interaction):
            return True
        await interaction.response.send_message('도화 소유자만 처리할 수 있습니다.',ephemeral=True)
        return False

    async def dm(self, row, text):
        user = self.bot.get_user(row['user_id']) or await self.bot.fetch_user(row['user_id'])
        await user.send(f"<@{row['user_id']}>\n{text}",
                        allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=row['user_id'])]))

    async def request(self, interaction, hours):
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            self.bot.activity.reconcile()
            try:
                row = self.store.request(interaction.id,interaction.guild_id,interaction.user.id,hours)
            except ValueError as error:
                await interaction.followup.send(embed=discord.Embed(description=str(error),colour=0xE74C3C),ephemeral=True)
                return
            try:
                await self.deliver(row)
                text = '외활 요청을 보냈습니다. 승인되면 DM으로 알려드릴게요.'
            except discord.HTTPException:
                LOG.exception('외활 요청 게시 실패: %s', row['id'])
                text = '요청은 저장했지만 요청방에 보내지 못했습니다. 채널 권한을 확인하면 자동 재시도합니다.'
        await interaction.followup.send(text,ephemeral=True)

    async def review(self, interaction, approve):
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            rows = self.store.rows('SELECT * FROM away_requests WHERE message_id=? AND guild_id=?',
                                   (interaction.message.id,interaction.guild_id))
            try:
                if not rows:
                    raise ValueError('요청을 찾을 수 없습니다.')
                self.bot.activity.reconcile()
                row = self.store.decide(rows[0]['id'],interaction.user.id,approve,time.time())
            except ValueError as error:
                await interaction.followup.send(str(error),ephemeral=True)
                return
            try:
                await self.deliver(row)
                result = '수락했습니다. 승인 시점부터 시간이 계산됩니다.' if approve else '거절했습니다.'
                if self.store.get(row['id'])['start_dm'] == 'blocked':
                    result += ' 유저가 DM을 차단하여 시작 안내를 보내지 못했습니다.'
            except discord.HTTPException:
                LOG.exception('외활 처리 알림 실패: %s',row['id'])
                result = '처리는 저장됐지만 일부 알림 전송에 실패했습니다. 자동 재시도합니다.'
        await interaction.followup.send(result,ephemeral=True)

    async def deliver(self, row):
        rid = row['id']
        # Delivery flags survive restart. Transient failures are retried by the worker.
        if row['status'] == 'active' and row['start_dm'] == 'pending':
            try:
                await self.dm(row,START_TEXT)
                self.store.update(rid,start_dm='sent',dirty=1)
            except (discord.Forbidden,discord.NotFound):
                self.store.update(rid,start_dm='blocked',dirty=1)
            except discord.HTTPException:
                LOG.warning('시작 DM 전송 실패, 재시도 예정: %s', rid)
        if row['status'] == 'ended' and row['end_dm'] == 'pending':
            try:
                await self.dm(row,END_TEXT)
                self.store.update(rid,end_dm='sent',dirty=1)
            except (discord.Forbidden,discord.NotFound):
                self.store.update(rid,end_dm='blocked',dirty=1)
            except discord.HTTPException:
                LOG.warning('종료 DM 전송 실패, 재시도 예정: %s', rid)
        row = self.store.get(rid)
        channel = self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id'])
        if channel.guild.id != row['guild_id']:
            raise RuntimeError('Request channel guild mismatch')
        if row['dirty']:
            view = ReviewView(self) if row['status'] == 'pending' else None
            if row['message_id']:
                try:
                    await channel.get_partial_message(row['message_id']).edit(embed=request_embed(row),view=view)
                except discord.NotFound:
                    self.store.update(rid,message_id=None)
                    row['message_id'] = None
            if not row['message_id']:
                msg = await channel.send(embed=request_embed(row),view=view,allowed_mentions=discord.AllowedMentions.none())
                self.store.update(rid,message_id=msg.id)
            self.store.update(rid,dirty=0)
        if row['status'] == 'ended' and row['end_message'] is None:
            embed = discord.Embed(title='외활 종료',description=f"<@{row['user_id']}>\n\n외활이 종료되었습니다!",colour=0xE74C3C)
            if row['end_dm'] == 'blocked':
                embed.set_footer(text='종료 DM 전송 실패: DM 허용 후 부르기를 눌러 주세요.')
            msg = await channel.send(embed=embed,view=RecallView(self),allowed_mentions=discord.AllowedMentions.none())
            self.store.update(rid,end_message=msg.id)

    async def recall(self, interaction):
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            rows = self.store.rows("SELECT * FROM away_requests WHERE end_message=? AND guild_id=? AND status='ended'",
                                   (interaction.message.id,interaction.guild_id))
            if not rows:
                await interaction.followup.send('종료된 요청을 찾을 수 없습니다.',ephemeral=True)
                return
            row = rows[0]
            if time.time()-row['last_call'] < 60:
                await interaction.followup.send('부르기는 1분에 한 번 사용할 수 있습니다.',ephemeral=True)
                return
            self.store.update(row['id'],last_call=time.time())
            try:
                await self.dm(row,END_TEXT)
                self.store.update(row['id'],end_dm='sent',dirty=1)
                result = '유저를 멘션하여 종료 DM을 다시 보냈습니다.'
            except discord.HTTPException:
                result = 'DM 전송에 실패했습니다. 유저의 DM 허용 여부를 확인해 주세요.'
            await interaction.followup.send(result,ephemeral=True)

    @tasks.loop(seconds=30)
    async def worker(self):
        async with self.lock:
            self.store.expire(time.time())
            rows = self.store.rows("""SELECT * FROM away_requests WHERE dirty=1
                OR (status='active' AND start_dm='pending')
                OR (status='ended' AND (end_dm='pending' OR end_message IS NULL))""")
            for row in rows:
                try:
                    await self.deliver(row)
                except Exception:
                    LOG.exception('외활 알림 재시도 실패: %s',row['id'])

    @worker.before_loop
    async def before_worker(self):
        await self.bot.wait_until_ready()

    def start(self):
        if not self.worker.is_running():
            self.worker.start()

    def close(self):
        self.worker.cancel()


def register(bot):
    @bot.tree.command(name='외활요청',description='활동시간 5시간당 외활 1시간을 요청합니다.')
    @app_commands.guild_only()
    @app_commands.describe(시간='요청할 외활 시간 (정수 시간)')
    async def request(interaction: discord.Interaction, 시간: app_commands.Range[int,1,8760]):
        await bot.away.request(interaction,시간)

    @bot.tree.command(name='외활요청방',description='소유자 전용: 외활 요청과 종료 알림을 보낼 방을 지정합니다.')
    @app_commands.guild_only()
    async def configure(interaction: discord.Interaction, 채널: discord.TextChannel):
        permissions = 채널.permissions_for(interaction.guild.me)
        if not all((permissions.view_channel,permissions.send_messages,permissions.embed_links)):
            await interaction.response.send_message('해당 방에서 봇의 채널 보기·메시지 보내기·링크 임베드 권한이 필요합니다.',ephemeral=True)
            return
        bot.away.store.configure(interaction.guild_id,채널.id)
        await interaction.response.send_message(f'{채널.mention}을 외활요청방으로 설정했습니다.',ephemeral=True)
