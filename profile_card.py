"""User-specific image cards and persistent role-category / permit settings."""
import asyncio
import io
import logging
from functools import lru_cache
from pathlib import Path

import discord
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageChops

ASSETS = Path(__file__).resolve().parent / 'assets'
SIZE = (1500, 900)
CATEGORIES = ('서버장','총관리자','단속팀장','단속팀','홍보팀장','홍보팀',
              '활동팀장','활동팀','뉴관팀장','뉴관팀')
COLORS = {
    '서버장': '#0b3296', '총관리자': '#101116',
    '단속팀장': '#701c29', '단속팀': '#c73441',
    '홍보팀장': '#462567', '홍보팀': '#884fc2',
    '활동팀장': '#8c6718', '활동팀': '#f1cf48',
    '뉴관팀장': '#487023', '뉴관팀': '#b7db66',
    '서버원': '#8bc3df',
}


class ProfileStore:
    def __init__(self, db):
        self.db = db
        with db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS profile_roles (
                    guild_id INTEGER NOT NULL, category TEXT NOT NULL, role_id INTEGER NOT NULL,
                    PRIMARY KEY(guild_id,category), UNIQUE(guild_id,role_id));
                CREATE TABLE IF NOT EXISTS external_permits (
                    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    granted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(guild_id,user_id));
            ''')

    def assign(self, guild_id, category, role_id):
        if category not in CATEGORIES:
            raise ValueError('지원하지 않는 카테고리입니다.')
        with self.db:
            # A Discord role identifies one category; reassignment replaces its old mapping.
            self.db.execute('DELETE FROM profile_roles WHERE guild_id=? AND (category=? OR role_id=?)',
                            (guild_id,category,role_id))
            self.db.execute('INSERT INTO profile_roles VALUES (?,?,?)',(guild_id,category,role_id))

    def role_categories(self, guild_id):
        return dict(self.db.execute('SELECT role_id,category FROM profile_roles WHERE guild_id=?',(guild_id,)))

    def category(self, guild_id, member_roles):
        mapping = self.role_categories(guild_id)
        for role in sorted(member_roles,reverse=True):
            if role.id in mapping:
                return mapping[role.id]
        return '서버원'

    def has_role_category(self, guild_id, member_roles, categories):
        mapping = self.role_categories(guild_id)
        return any(mapping.get(role.id) in categories for role in member_roles)

    def permit(self, guild_id, user_id):
        row = self.db.execute('SELECT granted FROM external_permits WHERE guild_id=? AND user_id=?',
                              (guild_id,user_id)).fetchone()
        return bool(row and row[0])

    def set_permit(self, guild_id, user_id, granted):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO external_permits VALUES (?,?,?)',
                            (guild_id,user_id,int(granted)))


@lru_cache(maxsize=12)
def background(category):
    with Image.open(ASSETS / 'profile_background.jpg') as source:
        base = ImageOps.fit(source.convert('RGB'),SIZE,method=Image.Resampling.LANCZOS)
    if category == '서버원':
        return base
    # Derive white wisps from the approved background, retaining identical geometry.
    r,g,b = base.split()
    smoke = ImageChops.darker(ImageChops.darker(r,g),b)
    smoke = smoke.point([round(255 * max(0,min(1,(v-70)/185))**2.4) for v in range(256)])
    return ImageOps.colorize(smoke, COLORS.get(category,COLORS['서버원']), '#ffffff')


def font(size):
    return ImageFont.truetype(str(ASSETS / 'NanumGothic-Regular.ttf'),size)


def draw_fit(draw, text, box, size, color, align='left'):
    text = ' '.join(str(text).split())
    left,top,right,bottom = box
    chosen = font(size)
    while size > 18 and draw.textlength(text,font=chosen) > right-left:
        size -= 1
        chosen = font(size)
    while text and draw.textlength(text,font=chosen) > right-left:
        text = text[:-2] + '…'
    width = draw.textlength(text,font=chosen)
    x = (left+right-width)/2 if align == 'center' else left
    draw.text((x,(top+bottom)/2),text,font=chosen,fill=color,anchor='lm')


def render_card(avatar, nickname, server_name, category, seconds, available_hours, permit):
    canvas = background(category).copy()
    dark = category not in ('서버원','활동팀','뉴관팀')
    ink = '#f3f6ff' if dark else '#10264c'
    with Image.open(io.BytesIO(avatar)) as source:
        photo = ImageOps.fit(source.convert('RGB'),(650,650),method=Image.Resampling.LANCZOS)
    canvas.paste(photo,(55,125))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((54,124,705,775),outline='#ecf4ff',width=2)
    draw_fit(draw,nickname,(760,135,1390,200),44,ink,'center')
    draw.line((760,230,1390,230),fill=ink,width=2)
    draw_fit(draw,f'활동시간 : {max(0,int(seconds//3600))}시간',(780,300,1390,365),36,ink)
    draw_fit(draw,f'외활 가능 시간 : {max(0,int(available_hours))}시간',(780,420,1390,485),36,ink)
    draw_fit(draw,f'직책 : {category}',(780,535,1390,600),36,ink)
    draw_fit(draw,'외활권 :',(780,650,925,715),36,ink)
    draw.rectangle((940,656,1360,711),fill='#079b35' if permit else '#d93742',
                   outline='#f4f6fa',width=2)
    draw.line((1085,780,1425,780),fill=ink,width=2)
    draw_fit(draw,server_name,(1085,790,1425,840),27,ink,'center')
    output = io.BytesIO()
    canvas.save(output,format='PNG',optimize=True)
    return output.getvalue()


def target_for(interaction, selected):
    return selected if selected is not None else interaction.user


def register(bot):
    render_lock = asyncio.Semaphore(2)

    @bot.tree.command(name='정보',description='선택한 유저의 정보 카드. 생략하면 내 정보를 표시합니다.')
    @app_commands.guild_only()
    @app_commands.describe(유저='정보를 확인할 서버원 (생략하면 본인)')
    @app_commands.checks.cooldown(1,5,key=lambda i:(i.guild_id,i.user.id))
    async def info(interaction: discord.Interaction, 유저: discord.Member | None = None):
        await interaction.response.defer()
        target = target_for(interaction,유저)
        try:
            member = await interaction.guild.fetch_member(target.id)
            bot.activity_tracker.reconcile()
            db = bot.profile_store
            category = db.category(interaction.guild_id,member.roles)
            available, seconds = bot.away.store.available(interaction.guild_id,member.id)
            permit = db.permit(interaction.guild_id,member.id)
            avatar = await member.display_avatar.with_static_format('png').with_size(1024).read()
            async with render_lock:
                card = await asyncio.to_thread(render_card,avatar,member.display_name,
                    interaction.guild.name,category,seconds,available,permit)
            await interaction.followup.send(file=discord.File(io.BytesIO(card),filename=f'profile-{member.id}.png'),
                allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            await interaction.followup.send('현재 서버에서 해당 유저를 찾을 수 없습니다.',ephemeral=True)
        except (discord.HTTPException,OSError,ValueError):
            logging.getLogger(__name__).exception('정보 카드 생성 실패')
            await interaction.followup.send('정보 카드를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.',ephemeral=True)

    @info.error
    async def info_error(interaction, error):
        if isinstance(error,app_commands.CommandOnCooldown):
            await interaction.response.send_message('정보 조회는 5초에 한 번 사용할 수 있습니다.',ephemeral=True)
        else:
            raise error

    @bot.tree.command(name='역할부여',description='서버장·도화 소유자: 디스코드 역할을 카드와 권한에 연결합니다.')
    @app_commands.guild_only()
    @app_commands.choices(카테고리=[app_commands.Choice(name=c,value=c) for c in CATEGORIES])
    async def assign(interaction: discord.Interaction, 카테고리: app_commands.Choice[str], 역할: discord.Role):
        if 역할.is_default():
            await interaction.response.send_message('@everyone은 지정할 수 없습니다.',ephemeral=True)
            return
        bot.profile_store.assign(interaction.guild_id,카테고리.value,역할.id)
        await interaction.response.send_message(f'{역할.mention} → {카테고리.value} 카드로 연결했습니다.',
            ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    permits = app_commands.Group(name='외활권',description='서버장·총관리자·도화 소유자: 외활권 보유 여부 설정')

    @permits.command(name='부여',description='유저에게 외활권을 부여합니다.')
    @app_commands.guild_only()
    async def grant(interaction: discord.Interaction, 유저: discord.Member):
        bot.profile_store.set_permit(interaction.guild_id,유저.id,True)
        await interaction.response.send_message('외활권을 부여했습니다.',ephemeral=True)

    @permits.command(name='회수',description='유저의 외활권을 회수합니다.')
    @app_commands.guild_only()
    async def revoke(interaction: discord.Interaction, 유저: discord.Member):
        bot.profile_store.set_permit(interaction.guild_id,유저.id,False)
        await interaction.response.send_message('외활권을 회수했습니다.',ephemeral=True)

    bot.tree.add_command(permits)
