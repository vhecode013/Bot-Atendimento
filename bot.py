# bot.py
from __future__ import annotations
import asyncio
import logging
import signal
from typing import List, Optional
from dotenv import load_dotenv
load_dotenv()

import discord
from discord.ext import commands, tasks
from discord import app_commands

from utils import env

# ---------------- LOGGING GLOBAL ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("vhecode")

fh = logging.FileHandler("bot_debug.log", encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(fh)

# 🔇 Reduz verbosidade do discord.py
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.ERROR)
logging.getLogger("discord.client").setLevel(logging.WARNING)

# ---------------- INTENTS ----------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True

COGS: List[str] = [
    "cogs.tickets",
    "cogs.logs",
    "cogs.transcript",
    "cogs.entradasaida",
    "cogs.pagamentos",
]

GUILD_ID = env.guild_id()
PREFER_GUILD_ONLY = bool(GUILD_ID)

# ==================== BOT PRINCIPAL ====================
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            help_command=None,
        )
        self._activities = [
            discord.Activity(type=discord.ActivityType.watching, name="Vhe Code 🌟"),
            discord.Game(name="Vhe Code 🌟")
        ]
        self._idx = 0
        self.synced_once = False

    # ---------- Task com log seguro ----------
    def create_task(self, coro, *, name: Optional[str] = None):
        t = asyncio.create_task(coro, name=name)

        def _done(task: asyncio.Task):
            try:
                exc = task.exception()
                if exc:
                    log.exception(f"💥 Task falhou: {name or task.get_name()}", exc_info=exc)
            except asyncio.CancelledError:
                log.info(f"🧹 Task cancelada: {name or task.get_name()}")
            except Exception:
                log.exception("❗ Falha obtendo exceção da task")

        t.add_done_callback(_done)
        return t

    # ---------- Tratamento de erros globais ----------
    def _loop_exc_handler(self, loop, context):
        msg = context.get("message") or "asyncio loop exception"
        exc = context.get("exception")
        if exc:
            log.exception(f"❗ {msg}", exc_info=exc)
        else:
            log.error(f"❗ {msg} ctx={context}")

    # ---------- Sinais de encerramento ----------
    def _install_signals(self):
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: self.create_task(
                        self._graceful_shutdown(s),
                        name=f"shutdown_{s.name}"
                    )
                )
        except NotImplementedError:
            pass

    async def _graceful_shutdown(self, sig):
        log.warning(f"⚠️ Recebi {getattr(sig,'name',sig)} — desligando…")
        try:
            await self.change_presence(status=discord.Status.invisible)
            await self.close()
        except Exception:
            pass

    # ==================== SYNC COMMANDS ====================
    async def _sync_tree(self, delay: int = 3):
        """Sincroniza comandos com backoff inteligente e sem duplicação."""
        if self.synced_once:
            log.info("🔁 Sincronização ignorada (já feita neste ciclo).")
            return
        await asyncio.sleep(delay)

        backoff = 2.5
        for attempt in range(5):
            try:
                if PREFER_GUILD_ONLY and GUILD_ID:
                    guild_obj = discord.Object(id=GUILD_ID)
                    synced = await self.tree.sync(guild=guild_obj)
                    log.info(f"🏠 Sync guild {GUILD_ID}: {len(synced)} comandos")
                else:
                    synced = await self.tree.sync()
                    log.info(f"🌐 Sync global: {len(synced)} comandos")

                self.synced_once = True
                break

            except discord.errors.HTTPException as e:
                if e.status == 429:
                    log.warning(f"⏳ Rate limit — aguardando {backoff:.2f}s antes de tentar novamente...")
                    await asyncio.sleep(backoff)
                    backoff *= 1.7
                else:
                    log.exception("❌ Erro HTTP ao sincronizar comandos", exc_info=e)
                    break
            except Exception as e:
                log.exception("❌ Erro inesperado no sync_tree", exc_info=e)
                break

    # ==================== PRESENÇA ROTATIVA ====================
    @tasks.loop(minutes=10)
    async def _presence_rotator(self):
        if not self._activities:
            return
        act = self._activities[self._idx % len(self._activities)]
        await self.change_presence(status=discord.Status.online, activity=act)
        self._idx += 1

    @_presence_rotator.before_loop
    async def _presence_rotator_before(self):
        await self.wait_until_ready()

    # ==================== SETUP HOOK ====================
    async def setup_hook(self):
        asyncio.get_running_loop().set_exception_handler(self._loop_exc_handler)
        self._install_signals()

        for ext in COGS:
            try:
                await self.load_extension(ext)
                log.info(f"✔ Cog carregada: {ext}")
            except Exception:
                log.exception(f"✖ Falha ao carregar cog {ext}")

        if not self._presence_rotator.is_running():
            self._presence_rotator.start()

        self.create_task(self._sync_tree(delay=4), name="delayed_sync")

    async def on_ready(self):
        u = self.user
        log.info(f"✅ Logado como {u} ({u.id})")
        try:
            await self.change_presence(status=discord.Status.online, activity=self._activities[0])
        except Exception:
            pass


# ==================== /SYNCADMIN ====================
class AdminSync(commands.Cog):
    def __init__(self, bot: MyBot):
        self.bot = bot

    @app_commands.command(name="syncadmin", description="(Admin) Limpa e ressincroniza os comandos.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def syncadmin(self, itx: discord.Interaction):
        log.info(f"🔁 Syncadmin acionado por {itx.user} ({itx.user.id})")
        await itx.response.defer(ephemeral=True, thinking=True)
        await self.bot._sync_tree(delay=0)
        await itx.followup.send("✅ Sincronização concluída com sucesso.", ephemeral=True)


async def setup_admin_sync(bot: MyBot):
    await bot.add_cog(AdminSync(bot))


# ==================== ENTRADA ====================
async def amain():
    tok = env.token()
    if not tok:
        log.error("DISCORD_TOKEN ausente no .env")
        return

    bot = MyBot()
    await setup_admin_sync(bot)

    try:
        await bot.start(tok)
    except discord.LoginFailure:
        log.exception("Token inválido. Cheque DISCORD_TOKEN no .env.")
    finally:
        await bot.close()


if __name__ == "__main__":
    log.info("🚀 Iniciando Vhe Code 🌟 Bot…")
    asyncio.run(amain())
