import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv


class DohwaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        logging.getLogger("dohwa").info("도화 로그인 완료: %s (%s)", self.user, self.user.id)


bot = DohwaBot()


@bot.tree.command(name="핑", description="·")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"도화가 정상 작동 중입니다. 응답 속도: {round(bot.latency * 1000)}ms",
        ephemeral=True,
    )


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
