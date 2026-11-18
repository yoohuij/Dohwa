import asyncio
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv
from activity import ActivityTracker, format_activity_time
from away import AwayFeature, register as register_away
from profile_card import ProfileStore, register as register_profile


class DohwaCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        bot = self.client
        command = interaction.command.qualified_name if interaction.command else None
        if command in ('외활요청', '외활시간측정', '정보') and interaction.guild_id is not None:
            return True
        if interaction.user.id == bot.owner_id or bot.has_role_category(interaction, '서버장'):
            return True
        if command in ('외활권 부여', '외활권 회수'):
            if bot.has_role_category(interaction, '총관리자'):
                return True
            message = "서버장, 총관리자 또는 도화 소유자만 사용할 수 있습니다."
        else:
            message = "서버장 또는 도화 소유자만 사용할 수 있습니다."
        await interaction.response.send_message(message, ephemeral=True)
        return False


class DohwaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.away = None
        self.activity_tracker = None
        self.profile_store = None
        self.owner_id = None
        self.syncing_guilds = set()
        self.tree = DohwaCommandTree(self)

    async def setup_hook(self):
        self.activity_tracker = ActivityTracker(self)
        self.profile_store = ProfileStore(self.activity_tracker.store.db)
        info = await self.application_info()
        self.owner_id = info.team.owner_id if info.team else info.owner.id
        self.away = AwayFeature(self)
        await self.tree.sync()

    def has_role_category(self, interaction, *categories):
        if interaction.guild_id is None or self.profile_store is None:
            return False
        roles = getattr(interaction.user, 'roles', ())
        return self.profile_store.has_role_category(interaction.guild_id, roles, categories)

    async def on_voice_state_update(self, member, before, after):
        if self.activity_tracker:
            self.activity_tracker.reconcile()

    async def on_message(self, message):
        if self.activity_tracker:
            self.activity_tracker.message(message)

    async def on_disconnect(self):
        if self.activity_tracker:
            self.activity_tracker.disconnect()

    async def on_resumed(self):
        if self.activity_tracker:
            await self.activity_tracker.ready()

    async def close(self):
        if self.away:
            self.away.close()
            self.away = None
        if self.activity_tracker:
            self.activity_tracker.close()
            self.activity_tracker = None
        self.profile_store = None
        await super().close()

    async def on_ready(self):
        await self.activity_tracker.ready()
        self.away.start()
        logging.getLogger("dohwa").info("도화 로그인 완료: %s (%s)", self.user, self.user.id)


bot = DohwaBot()


@bot.tree.command(name="핑", description="·")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"도화가 정상 작동 중입니다. 응답 속도: {round(bot.latency * 1000)}ms",
        ephemeral=True,
    )


@bot.tree.command(name="외활시간추가감소", description="서버장·도화 소유자: 유저의 외활 가능 시간을 조정합니다.")
@app_commands.guild_only()
@app_commands.describe(시간="양수는 추가, 음수는 감소. 단위는 시간입니다.")
async def adjust_away_time(interaction: discord.Interaction, 유저: discord.Member, 시간: int):
    await interaction.response.defer(ephemeral=True)
    bot.activity_tracker.reconcile()
    try:
        available = bot.away.store.adjust_hours(interaction.guild_id, 유저.id, 시간)
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    await interaction.followup.send(
        f"{유저.mention} 외활 가능 시간 {시간:+d}시간. 현재 {available}시간.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="활동시간추가감소", description="서버장·도화 소유자: 유저가 사용 가능한 활동시간을 조정합니다.")
@app_commands.guild_only()
@app_commands.describe(시간="양수는 추가, 음수는 감소. 단위는 시간입니다.")
async def adjust_activity_time(interaction: discord.Interaction, 유저: discord.Member, 시간: int):
    await interaction.response.defer(ephemeral=True)
    bot.activity_tracker.reconcile()
    try:
        balance = bot.away.store.adjust_activity_hours(interaction.guild_id, 유저.id, 시간)
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    usable = format_activity_time(balance.usable_seconds)
    await interaction.followup.send(
        f"{유저.mention} 사용 가능 활동시간 {시간:+d}시간. 현재 {usable}, 외활 가능 {balance.available_hours}시간.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )

@bot.tree.command(name="권한동기화", description="모든 방의 개별 권한을 부모 카테고리 권한으로 덮어씁니다.")
@app_commands.guild_only()
async def sync_permissions(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("서버에서 실행해 주세요.", ephemeral=True)
        return
    if guild.id in bot.syncing_guilds:
        await interaction.response.send_message("이 서버의 권한 동기화가 이미 진행 중입니다.", ephemeral=True)
        return
    bot.syncing_guilds.add(guild.id)
    succeeded = unchanged = excluded = 0
    failures = []
    stopped = ""
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channels = await guild.fetch_channels()
            async def apply():
                nonlocal succeeded, unchanged, excluded, stopped
                for original in channels:
                    if isinstance(original, discord.CategoryChannel):
                        continue
                    try:
                        channel = await guild.fetch_channel(original.id)
                        if channel.category_id is None:
                            excluded += 1
                            continue
                        # Fetch the parent explicitly, so its latest overwrites are used.
                        category = await guild.fetch_channel(channel.category_id)
                        if channel.overwrites == category.overwrites:
                            unchanged += 1
                            continue
                        await channel.edit(
                            overwrites=category.overwrites,
                            reason=f"도화 권한동기화: 요청자 {interaction.user.id}",
                        )
                        succeeded += 1
                        await asyncio.sleep(0.5)
                    except discord.NotFound:
                        failures.append((original.id, "채널 또는 카테고리가 삭제됨"))
                    except discord.Forbidden:
                        failures.append((original.id, "권한 부족"))
                    except discord.HTTPException as error:
                        failures.append((original.id, f"HTTP {error.status}"))
                        if error.status == 429:
                            stopped = "요청 제한으로 중단했습니다."
                            break
            # Leave time to report before the interaction token expires.
            await asyncio.wait_for(apply(), timeout=780)
        except asyncio.TimeoutError:
            stopped = "처리 시간 제한으로 중단했습니다. 다시 실행하면 남은 방을 처리합니다."
        except discord.HTTPException as error:
            stopped = f"채널 목록 조회 실패: HTTP {error.status}"
        summary = (
            f"권한 동기화 결과\n"
            f"변경: {succeeded}개 · 이미 동일: {unchanged}개\n"
            f"카테고리 없음: {excluded}개 · 실패: {len(failures)}개"
        )
        if stopped:
            summary += "\n" + stopped
        if failures:
            summary += "\n" + "\n".join(f"<#{cid}>: {reason}" for cid, reason in failures[:15])
            if len(failures) > 15:
                summary += f"\n외 {len(failures) - 15}개 (호스팅 로그 참조)"
            logging.getLogger("dohwa").warning("권한 동기화 실패: %s", failures)
        await interaction.edit_original_response(content=summary, allowed_mentions=discord.AllowedMentions.none())
    finally:
        bot.syncing_guilds.discard(guild.id)


register_away(bot)
register_profile(bot)


def main():
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == "여기에_봇_토큰을_입력하세요":
        raise SystemExit(".env 또는 호스팅 환경 변수의 DISCORD_TOKEN에 도화봇 토큰을 입력해주세요.")
    try:
        bot.run(token)
    except discord.LoginFailure:
        raise SystemExit("도화봇 토큰이 올바르지 않습니다. DISCORD_TOKEN을 확인해주세요.") from None


if __name__ == "__main__":
    main()

