# cogs/logs.py
from __future__ import annotations
import datetime as dt
from collections import deque
from typing import Optional, List, Dict, Deque

import discord
from discord.ext import commands

from utils import env

# ========= Helpers de ENV =========
def _bool_env(name: str, default: bool) -> bool:
    raw = str(env.get(name, "")).strip().lower()
    if raw in ("1", "true", "t", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "f", "no", "n", "off"):
        return False
    return default

def _split_ids(raw: str | None) -> list[int]:
    raw = (raw or "").strip()
    if not raw:
        return []
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out

LOG_CHANNEL_ID: int = env.get_int("LOG_BOT_CHANNEL_ID", 0)
IGNORE_CHANNELS: set[int] = set(_split_ids(env.get("LOG_IGNORE_CHANNELS", "")))
IGNORE_BOTS: bool = _bool_env("LOG_IGNORE_BOTS", True)
IGNORE_WEBHOOKS: bool = _bool_env("LOG_IGNORE_WEBHOOKS", True)
RATE_MAX_PER_MIN: int = env.get_int("LOG_RATE_MAX_PER_MINUTE", 40)
RATE_WINDOW_SECONDS: int = env.get_int("LOG_RATE_WINDOW_SECONDS", 60)
VOICE_COOLDOWN_MS: int = env.get_int("LOG_VOICE_COOLDOWN_MS", 1200)

FOOTER_NOME: str = env.footer_nome()
FOOTER_LOGO: str = env.footer_logo()

# ========= Utils =========
def _fmt_dt_utc(d: Optional[dt.datetime]) -> str:
    if not d:
        return "—"
    return d.astimezone(dt.timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

def _truncate(s: Optional[str], limit: int = 1800) -> str:
    s = s or ""
    return (s[: limit - 3] + "...") if len(s) > limit else s

def _set_brand(embed: discord.Embed) -> discord.Embed:
    embed.set_footer(text=FOOTER_NOME, icon_url=(FOOTER_LOGO or None))
    if FOOTER_LOGO:
        try:
            embed.set_thumbnail(url=FOOTER_LOGO)
        except Exception:
            pass
    return embed

def _is_ignored_channel(ch: Optional[discord.abc.GuildChannel]) -> bool:
    if not ch:
        return False
    return int(getattr(ch, "id", 0) or 0) in IGNORE_CHANNELS

# ========= Rate Limiter =========
class _RateLimiter:
    def __init__(self, max_events: int, window_seconds: int):
        self.max_events = max_events
        self.window = dt.timedelta(seconds=window_seconds)
        self._hits: Deque[dt.datetime] = deque()

    def allow(self) -> bool:
        now = dt.datetime.utcnow()
        # limpa fora da janela
        while self._hits and (now - self._hits[0]) > self.window:
            self._hits.popleft()
        if len(self._hits) >= self.max_events:
            return False
        self._hits.append(now)
        return True

# ========= Cog =========
class LogsCog(commands.Cog):
    """Logs de voz, mensagens, membros, canais e threads, com rate-limit e filtros."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._limiters: Dict[int, _RateLimiter] = {}
        self._last_voice_event_at: Dict[tuple[int, int], dt.datetime] = {}

    def _log_channel(self, guild: Optional[discord.Guild]) -> Optional[discord.TextChannel]:
        if not guild or not LOG_CHANNEL_ID:
            return None
        ch = guild.get_channel(LOG_CHANNEL_ID)
        return ch if isinstance(ch, discord.TextChannel) else None

    def _limiter_for(self, guild_id: int) -> _RateLimiter:
        lim = self._limiters.get(guild_id)
        if not lim or lim.max_events != RATE_MAX_PER_MIN or lim.window != dt.timedelta(seconds=RATE_WINDOW_SECONDS):
            lim = _RateLimiter(RATE_MAX_PER_MIN, RATE_WINDOW_SECONDS)
            self._limiters[guild_id] = lim
        return lim

    async def _send_log(self, guild: Optional[discord.Guild], embed: discord.Embed):
        ch = self._log_channel(guild)
        if not ch or not guild:
            return
        if not self._limiter_for(guild.id).allow():
            return
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

    # ========== VOICE ==========
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        guild = member.guild
        if not self._log_channel(guild):
            return
        if IGNORE_BOTS and member.bot:
            return

        changes: List[str] = []
        if before.channel != after.channel:
            if _is_ignored_channel(before.channel) or _is_ignored_channel(after.channel):
                return
            if before.channel is None and after.channel:
                changes.append(f"🎧 **Entrou** em {after.channel.mention}")
            elif before.channel and after.channel is None:
                changes.append(f"👋 **Saiu** de {before.channel.mention}")
            elif before.channel and after.channel:
                changes.append(f"🔁 **Moveu** {before.channel.mention} → {after.channel.mention}")

        def flag(txt: str, old: Optional[bool], new: Optional[bool]):
            if old is None or new is None or old == new:
                return None
            return f"{txt}: {'ON' if new else 'OFF'}"

        fchanges = [
            flag("🎙️ Self Mute", before.self_mute, after.self_mute),
            flag("🔇 Server Mute", before.mute, after.mute),
            flag("🎧 Self Deaf", before.self_deaf, after.self_deaf),
            flag("🛑 Server Deaf", before.deaf, after.deaf),
            flag("📵 Stream", before.self_stream, after.self_stream),
            flag("🎥 Video", before.self_video, after.self_video),
        ]
        changes += [c for c in fchanges if c]

        if not changes:
            return

        # Debounce por usuário
        key = (guild.id, member.id)
        now = dt.datetime.utcnow()
        last = self._last_voice_event_at.get(key)
        if last and (now - last).total_seconds() * 1000.0 < VOICE_COOLDOWN_MS:
            return
        self._last_voice_event_at[key] = now

        embed = discord.Embed(
            title="🔔 Log de Voz",
            description="\n".join(changes),
            color=discord.Color.blurple()
        )
        _set_brand(embed)
        embed.add_field(name="👤 Usuário", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    # ========== MENSAGENS ==========
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        guild = message.guild
        if not guild or not self._log_channel(guild):
            return
        if IGNORE_BOTS and getattr(message.author, "bot", False):
            return
        if IGNORE_WEBHOOKS and getattr(message, "webhook_id", None):
            return
        if _is_ignored_channel(getattr(message, "channel", None)):
            return

        content = message.content if message.content else ""
        if content == "" and not message.attachments:
            content = "(mensagem sem conteúdo ou não cacheada)"

        embed = discord.Embed(
            title="🗑️ Mensagem Apagada",
            description=_truncate(content, 1500),
            color=discord.Color.red()
        )
        _set_brand(embed)
        ch_val = message.channel.mention if hasattr(message.channel, "mention") else "—"
        embed.add_field(name="📍 Canal", value=ch_val, inline=True)
        author_val = f"{getattr(message.author, 'mention', '—')} (`{getattr(message.author, 'id', '—')}`)"
        embed.add_field(name="👤 Autor", value=author_val, inline=True)
        if message.attachments:
            files = "\n".join(f"- {a.filename} ({a.size} bytes)" for a in message.attachments[:8])
            embed.add_field(name=f"📎 Anexos ({len(message.attachments)})", value=_truncate(files, 700), inline=False)
        if message.created_at:
            embed.set_footer(text=f"{FOOTER_NOME} • Criada: {_fmt_dt_utc(message.created_at)}", icon_url=FOOTER_LOGO or None)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: List[discord.Message]):
        if not messages:
            return
        guild = messages[0].guild
        channel = messages[0].channel if messages else None
        if not guild or not self._log_channel(guild):
            return
        if _is_ignored_channel(channel):
            return
        embed = discord.Embed(
            title="🧹 Mensagens Apagadas em Massa",
            description=f"Foram apagadas **{len(messages)}** mensagens em {getattr(channel, 'mention', '#?')}.",
            color=discord.Color.red()
        )
        _set_brand(embed)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        guild = before.guild
        if not guild or not self._log_channel(guild):
            return
        if IGNORE_BOTS and getattr(before.author, "bot", False):
            return
        if IGNORE_WEBHOOKS and getattr(before, "webhook_id", None):
            return
        if _is_ignored_channel(getattr(before, "channel", None)):
            return
        if (before.content or "") == (after.content or ""):
            return

        before_txt = before.content or "(indisponível)"
        after_txt  = after.content or "(indisponível)"

        embed = discord.Embed(
            title="✏️ Mensagem Editada",
            description=f"Em {getattr(before.channel, 'mention', '#?')} por {getattr(before.author, 'mention', '—')}",
            color=discord.Color.orange()
        )
        _set_brand(embed)
        embed.add_field(name="Antes", value=_truncate(before_txt, 900) or "—", inline=False)
        embed.add_field(name="Depois", value=_truncate(after_txt, 900) or "—", inline=False)
        try:
            embed.add_field(name="Jump", value=f"[Ir para a mensagem]({after.jump_url})", inline=False)
        except Exception:
            pass
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    # ========== MEMBROS ==========
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if not self._log_channel(guild):
            return
        if IGNORE_BOTS and after.bot:
            return

        diffs: List[str] = []
        if before.nick != after.nick:
            diffs.append(f"🪪 **Nick**: `{before.nick or '—'}` → `{after.nick or '—'}`")

        before_roles = set(before.roles)
        after_roles  = set(after.roles)
        added = [r.mention for r in (after_roles - before_roles) if r.name != "@everyone"]
        removed = [r.mention for r in (before_roles - after_roles) if r.name != "@everyone"]
        if added:
            diffs.append("➕ **Cargos adicionados:** " + ", ".join(added))
        if removed:
            diffs.append("➖ **Cargos removidos:** " + ", ".join(removed))

        if not diffs:
            return

        embed = discord.Embed(
            title="👤 Atualização de Membro",
            description="\n".join(diffs),
            color=discord.Color.blurple()
        )
        _set_brand(embed)
        embed.add_field(name="Usuário", value=f"{after.mention} (`{after.id}`)", inline=False)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if not self._log_channel(guild):
            return
        if IGNORE_BOTS and member.bot:
            return
        embed = discord.Embed(
            title="✅ Membro Entrou",
            description=f"{member.mention} (`{member.id}`)",
            color=discord.Color.green()
        )
        _set_brand(embed)
        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass
        embed.add_field(name="Conta criada", value=_fmt_dt_utc(member.created_at), inline=True)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        if not self._log_channel(guild):
            return
        if IGNORE_BOTS and getattr(member, "bot", False):
            return
        embed = discord.Embed(
            title="🚪 Membro Saiu",
            description=f"{getattr(member, 'mention', '**Usuário**')} (`{getattr(member, 'id', '—')}`)",
            color=discord.Color.red()
        )
        _set_brand(embed)
        try:
            embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    # ========== CANAIS ==========
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        guild = getattr(channel, "guild", None)
        if not isinstance(guild, discord.Guild) or not self._log_channel(guild) or _is_ignored_channel(channel):
            return
        ref = getattr(channel, "mention", None) or f"`{getattr(channel, 'name', '?')}`"
        embed = discord.Embed(
            title="🆕 Canal Criado",
            description=f"{ref} (`{getattr(channel, 'id', '—')}`)",
            color=discord.Color.green()
        )
        _set_brand(embed)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = getattr(channel, "guild", None)
        if not isinstance(guild, discord.Guild) or not self._log_channel(guild) or _is_ignored_channel(channel):
            return
        embed = discord.Embed(
            title="🗑️ Canal Deletado",
            description=f"`{getattr(channel, 'name', '?')}` (`{getattr(channel, 'id', '—')}`)",
            color=discord.Color.red()
        )
        _set_brand(embed)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        guild = getattr(after, "guild", None)
        if not isinstance(guild, discord.Guild) or not self._log_channel(guild) or _is_ignored_channel(after):
            return
        diffs: List[str] = []
        if getattr(before, "name", None) != getattr(after, "name", None):
            diffs.append(f"📛 **Nome:** `{before.name}` → `{after.name}`")
        if hasattr(before, "topic") and hasattr(after, "topic"):
            if getattr(before, "topic", None) != getattr(after, "topic", None):
                diffs.append(f"📝 **Tópico:** `{before.topic or '—'}` → `{after.topic or '—'}`")
        if hasattr(before, "category") and hasattr(after, "category"):
            if getattr(before, "category", None) != getattr(after, "category", None):
                bcat = before.category.name if getattr(before, "category", None) else "—"
                acat = after.category.name if getattr(after, "category", None) else "—"
                diffs.append(f"📂 **Categoria:** `{bcat}` → `{acat}`")
        if not diffs:
            return
        embed = discord.Embed(
            title="⚙️ Canal Atualizado",
            description="\n".join(diffs),
            color=discord.Color.orange()
        )
        _set_brand(embed)
        embed.add_field(name="Canal", value=getattr(after, "mention", f"`{after.name}`"), inline=False)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    # ========== THREADS ==========
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        guild = thread.guild
        if not self._log_channel(guild) or _is_ignored_channel(thread.parent):
            return
        embed = discord.Embed(
            title="🧵 Thread Criada",
            description=f"{thread.mention} (`{thread.id}`) em {getattr(thread.parent, 'mention', '#?')}",
            color=discord.Color.green()
        )
        _set_brand(embed)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        guild = thread.guild
        if not self._log_channel(guild) or _is_ignored_channel(thread.parent):
            return
        embed = discord.Embed(
            title="🧵 Thread Deletada",
            description=f"`{thread.name}` (`{thread.id}`) em {getattr(thread.parent, 'mention', '#?')}",
            color=discord.Color.red()
        )
        _set_brand(embed)
        embed.timestamp = dt.datetime.utcnow()
        await self._send_log(guild, embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(LogsCog(bot))
