from __future__ import annotations
import datetime as dt
import asyncio
from typing import Optional, List

import discord
from discord.ext import commands
from utils import env  # <--- usa o teu sistema .env


# =================== Utils ===================

def _fmt_dt_utc(d: Optional[dt.datetime]) -> str:
    if not d:
        return "—"
    return d.astimezone(dt.timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

def _human_delta(start: Optional[dt.datetime], end: Optional[dt.datetime] = None) -> str:
    if not start:
        return "—"
    end = end or dt.datetime.now(dt.timezone.utc)
    start = start.astimezone(dt.timezone.utc)
    delta = end - start
    s = int(delta.total_seconds())
    days = s // 86400
    hours = (s % 86400) // 3600
    minutes = (s % 3600) // 60
    parts: List[str] = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if not parts: parts = ["<1m"]
    return " ".join(parts)

def _roles_list(member: discord.Member, max_items: int) -> str:
    roles = [r for r in member.roles if r is not member.guild.default_role]
    roles.sort(key=lambda r: r.position, reverse=True)
    if not roles:
        return "`(sem cargos)`"
    shown = roles[:max(1, max_items)]
    text = ", ".join(r.mention for r in shown)
    if len(roles) > len(shown):
        text += f" … (+{len(roles) - len(shown)})"
    return text

def _boost_str(member: discord.Member) -> str:
    return f"Sim desde {_fmt_dt_utc(member.premium_since)}" if member.premium_since else "Não"

async def _safe_send(destination, /, **kwargs):
    try:
        return await destination.send(**kwargs)
    except discord.HTTPException:
        try:
            await asyncio.sleep(0.6)
            return await destination.send(**kwargs)
        except Exception:
            return None
    except Exception:
        return None


# =================== Entrada / Saída ===================

class EntryExit(commands.Cog):
    """Sistema de boas-vindas e saída baseado em variáveis do .env."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.footer_nome = env.footer_nome()
        self.footer_logo = env.footer_logo()
        self.entrada_id = env.entrada_channel()
        self.saida_id = env.saida_channel()
        self.log_id = env.log_bot_channel()
        self.cargo_auto = env.cargo_auto()

    # ----------- Embeds -----------

    def _embed_public_welcome(self, member: discord.Member) -> discord.Embed:
        desc = (
            f"👋 Bem-vindo(a), {member.mention}!\n\n"
            "**Bem-vindo(a) ao Vhe Code!** 💫\n\n"
            "Esse é o espaço onde a **imaginação vira arte** e suas ideias ganham vida.\n\n"
            "🎟️ Para **fazer um orçamento** ou **tirar dúvidas**, acesse `#tickets` e **abra seu ticket**.\n\n"
            "Deixe a criatividade fluir — o resto é com a gente. 🪄"

        )

        embed = discord.Embed(
            title="✨ Bem-vindo(a) ao Vhe Code 🌟!",
            description=desc,
            color=discord.Color.purple()
        )

        try:
            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)
        except Exception:
            pass

        # imagem/banner
        logo_url = self.footer_logo or None
        if logo_url:
            embed.set_image(url=logo_url)
        elif member.guild.banner:
            embed.set_image(url=member.guild.banner.url)

        embed.set_footer(text=self.footer_nome, icon_url=(self.footer_logo or None))
        return embed

    def _embed_join_log(self, member: discord.Member) -> discord.Embed:
        tipo = "🤖 Bot" if member.bot else "👤 Humano"
        boost = _boost_str(member)
        created_at = _fmt_dt_utc(member.created_at)
        account_age = _human_delta(member.created_at)
        joined_at = _fmt_dt_utc(member.joined_at)
        guild_age = _human_delta(member.joined_at)
        roles_count = max(0, len(member.roles) - 1)

        desc = (
            f"{tipo} **{member.mention}** entrou no servidor.\n"
            f"📅 Conta criada: `{created_at}` ({account_age})\n"
            f"📥 Entrou: `{joined_at}` ({guild_age})\n"
            f"🚀 Boost: {boost}\n"
            f"🏷️ Cargos: {roles_count}\n"
            f"👥 Membros totais: {member.guild.member_count}"
        )

        embed = discord.Embed(
            title="🧾 Log de Entrada",
            description=desc,
            color=discord.Color.green()
        )

        thumb = member.display_avatar.url if member.display_avatar else (self.footer_logo or None)
        if thumb:
            embed.set_thumbnail(url=thumb)
        if self.footer_logo:
            embed.set_image(url=self.footer_logo)
        embed.set_footer(text=self.footer_nome, icon_url=(self.footer_logo or None))
        return embed

    def _embed_leave_public(self, member: discord.Member) -> discord.Embed:
        tipo = "🤖 Bot" if getattr(member, "bot", False) else "👤 Humano"
        created_at = _fmt_dt_utc(getattr(member, "created_at", None))
        joined_at = _fmt_dt_utc(getattr(member, "joined_at", None))

        desc = (
            f"{tipo} **{member.display_name}** saiu do servidor.\n\n"
            f"🗓️ Conta criada: {created_at}\n"
            f"📥 Entrou: {joined_at}\n"
            f"👥 Agora somos {member.guild.member_count} membros."
        )

        embed = discord.Embed(title="🚪 Membro saiu", description=desc, color=discord.Color.red())
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=self.footer_nome, icon_url=(self.footer_logo or None))
        return embed

    # ----------- Envio -----------

    async def _send_public_welcome(self, member: discord.Member):
        canal = member.guild.get_channel(self.entrada_id)
        if not isinstance(canal, discord.TextChannel):
            return
        embed = self._embed_public_welcome(member)
        await _safe_send(canal, content=member.mention, embed=embed)

    async def _send_join_log(self, member: discord.Member):
        canal = member.guild.get_channel(self.log_id)
        if not isinstance(canal, discord.TextChannel):
            return
        embed = self._embed_join_log(member)
        await _safe_send(canal, embed=embed)

    async def _send_goodbye(self, member: discord.Member):
        canal = member.guild.get_channel(self.saida_id)
        if not isinstance(canal, discord.TextChannel):
            return
        embed = self._embed_leave_public(member)
        await _safe_send(canal, content=(member.mention if hasattr(member, "mention") else None), embed=embed)

    # ----------- Eventos -----------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # cargo automático
        cargo = member.guild.get_role(self.cargo_auto)
        if cargo:
            try:
                await member.add_roles(cargo, reason="Entrada: cargo automático")
            except Exception:
                pass

        await self._send_public_welcome(member)
        await asyncio.sleep(0.3)
        await self._send_join_log(member)

        # DM opcional
        try:
            dm_embed = discord.Embed(
                title=f"Bem-vindo(a) a {member.guild.name}! ✨",
                description=(
                    f"Olá {member.mention}, seja bem-vindo(a) à **Vhe Code 🌟**!\n\n"
                    "🎟️ Para tirar dúvidas ou pedir orçamento, abra um ticket.\n"
                    "💅 Seu estilo, nossa inspiração!"
                ),
                color=discord.Color.blurple()
            )
            if member.display_avatar:
                dm_embed.set_thumbnail(url=member.display_avatar.url)
            dm_embed.set_footer(text=self.footer_nome, icon_url=self.footer_logo or None)
            await member.send(embed=dm_embed)
        except Exception:
            pass  # ignorar bloqueio de DMs

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._send_goodbye(member)


# ----------------- Setup -----------------
async def setup(bot: commands.Bot):
    await bot.add_cog(EntryExit(bot))
