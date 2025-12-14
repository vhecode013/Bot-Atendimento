# cogs/tickets.py
from __future__ import annotations
import asyncio
import datetime as dt
import logging
from typing import Optional, List, Dict

import discord
from discord.ext import commands
from discord import app_commands

from utils import env
from cogs.transcript_html_core import generate_transcript_html
from utils.ftp_uploader import upload_to_hostgator
import aiohttp
import os
from cogs.transcript_html_core import is_image  
import re


log = logging.getLogger("tickets")

async def _send_ticket_log(bot: commands.Bot, guild: discord.Guild, embed: discord.Embed):
    """Envia log de ticket encerrado — usa canal de transcript se definido, senão LogsCog padrão."""
    from utils import env

    transcript_channel_id = env.get_int("TRANSCRIPT_LOG_CHANNEL_ID", 0)
    log_channel = guild.get_channel(transcript_channel_id) if transcript_channel_id else None

    # tenta usar canal de transcript
    if log_channel and isinstance(log_channel, discord.TextChannel):
        try:
            await log_channel.send(embed=embed)
            return
        except Exception:
            pass

    # fallback — usa LogsCog
    logs_cog = bot.get_cog("LogsCog")
    if logs_cog and hasattr(logs_cog, "_send_log"):
        await logs_cog._send_log(guild, embed)
    else:
        log_channel_id = env.get_int("LOG_BOT_CHANNEL_ID", 0)
        ch = guild.get_channel(log_channel_id)
        if isinstance(ch, discord.TextChannel):
            await ch.send(embed=embed)


# ================== ENV ==================
FOOTER_NOME: str = env.footer_nome()
FOOTER_LOGO: str = env.footer_logo()

ROLE_ADMIN: List[int] = env.role_admin()

CATEGORY_IDS: Dict[str, int] = env.category_ids()  # suporte/roupas/cordoes/carros/design/cursos
PANEL_CHANNEL_ID: int = env.ticket_panel_channel()

TERMS_CHANNEL_ID: int = env.terms_channel_id()          # canal com texto completo dos termos (para link)
TERMS_LOG_CHANNEL_ID: int = env.terms_log_channel_id()  # canal de log de aceite/negação dos termos
TRANSCRIPT_LOG_CHANNEL_ID: int = env.transcript_log_channel_id()  # ✅ canal de logs de transcript

# ============ Helpers ============



def discord_mentions_to_text(content: str, guild: discord.Guild) -> str:
    """Converte <@id>, <@&id>, <#id> para @Nome, @Cargo, #canal."""
    if not content:
        return ""

    def repl_user(m):
        uid = int(m.group(1))
        mem = guild.get_member(uid)
        return f"@{mem.display_name}" if mem else f"@{uid}"

    def repl_role(m):
        rid = int(m.group(1))
        role = guild.get_role(rid)
        return f"@{role.name}" if role else f"@&{rid}"

    def repl_chan(m):
        cid = int(m.group(1))
        ch = guild.get_channel(cid)
        return f"#{ch.name}" if ch else f"#{cid}"

    content = re.sub(r"<@!?(\d+)>", repl_user, content)
    content = re.sub(r"<@&(\d+)>", repl_role, content)
    content = re.sub(r"<#(\d+)>", repl_chan, content)
    return content

def _now_unix() -> int:
    # usa UTC; o Discord renderiza no fuso do usuário
    return int(dt.datetime.now(dt.timezone.utc).timestamp())

def _is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if not ROLE_ADMIN:
        return False
    rids = {r.id for r in member.roles}
    return any(rid in rids for rid in ROLE_ADMIN)

def _admin_mentions(guild: discord.Guild) -> str:
    tags: List[str] = []
    for rid in ROLE_ADMIN:
        r = guild.get_role(rid)
        if isinstance(r, discord.Role):
            try:
                tags.append(r.mention)
            except Exception:
                pass
    return " ".join(tags)

def _topic_kv(topic: Optional[str], key: str) -> str:
    if not topic or f"{key}:" not in (topic or ""):
        return ""
    try:
        part = topic.split(f"{key}:")[1]
        return part.split("|")[0]
    except Exception:
        return ""

def _parse_member(guild: discord.Guild, raw: str) -> Optional[discord.Member]:
    raw = (raw or "").strip()
    if raw.startswith("<@") and raw.endswith(">"):
        raw = raw.replace("<@", "").replace("<@!", "").replace(">", "")
    if raw.isdigit():
        return guild.get_member(int(raw))
    return None

async def _ephemeral_ok(itx: discord.Interaction, text: str):
    """Envia uma resposta ephemeral com fallback seguro."""
    try:
        # Evita erro caso a interação tenha expirado
        if (discord.utils.utcnow() - itx.created_at).total_seconds() > 3:
            return  # interação velha, ignora silenciosamente

        # Se a interação já foi respondida, usa followup
        if itx.response.is_done():
            await itx.followup.send(text, ephemeral=True)
        else:
            await itx.response.send_message(text, ephemeral=True)

    except discord.errors.NotFound:
        # Token de interação expirado ou inválido
        try:
            await itx.followup.send(text, ephemeral=True)
        except Exception:
            pass  # evita crash silencioso
    except Exception as e:
        print(f"⚠️ Erro em _ephemeral_ok: {e}")


def _brand(embed: discord.Embed) -> discord.Embed:
    embed.set_footer(text=FOOTER_NOME, icon_url=(FOOTER_LOGO or None))
    return embed

# ================== Views Persistentes ==================

# ---- Modal de Assunto
class AssuntoModal(discord.ui.Modal, title="Abrir Ticket — Assunto"):
    def __init__(self, category_key: str):
        super().__init__(timeout=300)
        self.category_key = category_key
        self.assunto = discord.ui.TextInput(
            label="Assunto do atendimento",
            placeholder="Descreva resumidamente o motivo do ticket",
            max_length=120
        )
        self.add_item(self.assunto)

    async def on_submit(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True, thinking=True)
        guild = itx.guild
        user = itx.user

        if not isinstance(guild, discord.Guild) or not isinstance(user, discord.Member):
            return await _ephemeral_ok(itx, "❌ Use dentro de um servidor.")

        cat_id = CATEGORY_IDS.get(self.category_key) or 0
        category = guild.get_channel(cat_id)
        if not isinstance(category, discord.CategoryChannel):
            return await _ephemeral_ok(itx, "⚠️ Categoria não configurada corretamente.")

        # permissões iniciais (somente ver histórico até aceitar os termos)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=False, attach_files=False, embed_links=False, read_message_history=True
            ),
        }
        # admins
        for rid in ROLE_ADMIN:
            r = guild.get_role(rid)
            if isinstance(r, discord.Role):
                overwrites[r] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True
                )

        safe_name = user.name.replace(" ", "-").lower()
        ch_name = f"📩・{self.category_key}-{safe_name}"[:95]

        # Criar canal com metadados no topic
        assunto_txt = str(self.assunto.value).strip()
        topic = f"opener:{user.id}|categoria:{self.category_key}|assunto:{assunto_txt}"
        try:
            ch = await guild.create_text_channel(
                name=ch_name, category=category, overwrites=overwrites, topic=topic
            )
        except Exception as e:
            log.exception("Erro criando canal de ticket: %s", e)
            return await _ephemeral_ok(itx, f"❌ Falha ao criar o ticket: `{e}`")

        # 1) Painel “Ticket Aberto”
        opened_desc = (
            f"👤 **Autor:** {user.mention}\n"
            f"📂 **Categoria:** `{self.category_key}`\n"
            f"🧾 **Assunto:** `{assunto_txt}`\n"
            f"⏰ **Abertura:** <t:{_now_unix()}:f>\n\n"
            f"Bem-vindo(a)! Use o menu abaixo para **gerenciar** o ticket."
        )
        opened = discord.Embed(
            title="🎟️ Ticket Aberto — Vhe Code 🌟",
            description=opened_desc,
            color=discord.Color.purple()
        )
        _brand(opened)

        view_controls = TicketActionsView(custom_id=f"ticket_actions:{ch.id}", category_key=self.category_key)

        staff_ping = _admin_mentions(guild)
        content_ping = f"{user.mention} {staff_ping}".strip()
        await ch.send(content=content_ping, embed=opened, view=view_controls)

        # 2) Notificar equipe por DM
        try:
            notified = set()
            for rid in ROLE_ADMIN:
                role = guild.get_role(rid)
                if not role:
                    continue
                for member in role.members:
                    if member.id in notified:
                        continue
                    notified.add(member.id)
                    try:
                        dm_embed = discord.Embed(
                            title="🎟️ Novo ticket aberto",
                            description=(
                                f"**Usuário:** {user.mention}\n"
                                f"**Assunto:** `{assunto_txt}`\n"
                                f"**Categoria:** `{self.category_key}`\n"
                                f"**Canal:** {ch.mention}"
                            ),
                            color=discord.Color.blurple()
                        )
                        _brand(dm_embed)
                        view = discord.ui.View(timeout=120)
                        view.add_item(discord.ui.Button(
                            label="Ir para o ticket",
                            url=ch.jump_url,
                            style=discord.ButtonStyle.link
                        ))
                        await member.send(embed=dm_embed, view=view)
                    except Exception:
                        pass
        except Exception:
            pass

       # 3) Termos
        termos_lines = [
            "📝 **Termos de Uso — Vhe Code**",
            "",
            "Ao comprar no Vhe Code, você concorda com nossas regras e políticas.",
            "• Entregas em até 7 dias úteis via canais oficiais (Discord/e-mail).",
            "• Produção inicia após envio do comprovante.",
            "• Instalação no servidor é responsabilidade do cliente.",
            "• É proibida revenda, edição, redistribuição ou remoção da marca.",
            "• Arquivos ficam armazenados por 30 dias após a entrega.",
            "• Alterações após entrega são cobradas à parte.",
            "• Cancelamentos após início da produção não têm reembolso total.",
            f"🔗 Leia os termos completos no canal <#{TERMS_CHANNEL_ID}>." if TERMS_CHANNEL_ID else "🔗 Leia os termos completos no canal de termos."
        ]

        # remove possíveis linhas None
        termos_lines = [line for line in termos_lines if line]

        termos = discord.Embed(
            title="📝 Termos de Uso — Vhe Code",
            description="\n".join(termos_lines),
            color=discord.Color.blurple()
        )
        _brand(termos)


        termos_view = TermsView(custom_id=f"terms:{ch.id}:{user.id}")
        await ch.send(embed=termos, view=termos_view)

        # 4) DM do usuário
        try:
            emb_dm = discord.Embed(
                title="🎫 Seu ticket foi aberto",
                description=f"**Categoria:** `{self.category_key}`\n**Assunto:** `{assunto_txt}`\n\nAcompanhe pelo canal: {ch.mention}",
                color=discord.Color.purple()
            )
            _brand(emb_dm)
            btn = discord.ui.View(timeout=120)
            btn.add_item(discord.ui.Button(label="Ir para o ticket", url=ch.jump_url, style=discord.ButtonStyle.link))
            await user.send(embed=emb_dm, view=btn)
        except Exception:
            pass

        await _ephemeral_ok(itx, f"✅ Ticket criado: {ch.mention}")

# ---- Select de Categorias (painel público)
class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Suporte | Dúvidas", emoji="🛠️", description="Atendimento geral de suporte."),
            discord.SelectOption(label="Roupas | Neon", emoji="👕", description="Solicitação de roupas personalizadas."),
            discord.SelectOption(label="Cordões | Colares", emoji="💎", description="Pedido de cordões e acessórios."),
            discord.SelectOption(label="Carros", emoji="🚗", description="Atendimento de veículos personalizados."),
            discord.SelectOption(label="Design", emoji="🎨", description="Artes e identidade visual."),
            discord.SelectOption(label="Cursos", emoji="📘", description="Treinamentos e mentorias."),
        ]
        super().__init__(placeholder="Selecione uma categoria de ticket...", options=options, custom_id="ticket_select")

    async def callback(self, interaction: discord.Interaction):
        label = self.values[0]
        category_map = {
            "Suporte | Dúvidas": "suporte",
            "Roupas | Neon": "roupas",
            "Cordões | Colares": "cordoes",
            "Carros": "carros",
            "Design": "design",
            "Cursos": "cursos"
        }
        key = category_map.get(label)
        if not key:
            return await _ephemeral_ok(interaction, "⚠️ Categoria inválida.")
        await interaction.response.send_modal(AssuntoModal(key))

class TicketPanelView(discord.ui.View):
    def __init__(self, *, custom_id: str):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())
        self._custom_id = custom_id  # mantém referência

# ---- Ações dentro do ticket
class TicketActionsSelect(discord.ui.Select):
    def __init__(self, *, category_key: str):
        opts = [
            discord.SelectOption(label="Adicionar membro", emoji="➕", description="Adicionar alguém ao ticket."),
            discord.SelectOption(label="Remover membro", emoji="➖", description="Remover alguém do ticket."),
            discord.SelectOption(label="Notificar solicitante (DM)", emoji="🔔", description="Enviar DM com link do ticket."),
            discord.SelectOption(label="Fechar ticket", emoji="🛑", description="Encerrar e arquivar."),
        ]
        super().__init__(placeholder="Ações do ticket…", options=opts, custom_id="ticket_actions_select")
        self.category_key = category_key

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return await _ephemeral_ok(interaction, "❌ Use dentro do canal do ticket.")
        opener_id_raw = _topic_kv(ch.topic, "opener")
        opener: Optional[discord.Member] = None
        if opener_id_raw.isdigit():
            opener = interaction.guild.get_member(int(opener_id_raw))

        is_admin = _is_admin(interaction.user)
        is_opener = isinstance(opener, discord.Member) and (interaction.user.id == opener.id)

        if choice == "Adicionar membro":
            if not (is_admin or is_opener):
                return await _ephemeral_ok(interaction, "❌ Somente o autor do ticket ou equipe pode usar esta ação.")
            await interaction.response.send_modal(AddUserModal(self.category_key))
            return

        if choice == "Remover membro":
            if not (is_admin or is_opener):
                return await _ephemeral_ok(interaction, "❌ Somente o autor do ticket ou equipe pode usar esta ação.")
            await interaction.response.send_modal(RemoveUserModal(self.category_key))
            return

        if choice == "Notificar solicitante (DM)":
            if not is_admin:
                return await _ephemeral_ok(interaction, "❌ Apenas equipe pode usar esta ação.")
            await _notify_opener(interaction)
            return

        if choice == "Fechar ticket":
            if not is_admin:
                return await _ephemeral_ok(interaction, "❌ Apenas equipe pode usar esta ação.")
            await interaction.response.send_modal(CloseReasonModal(self.category_key))
            return

class TicketActionsView(discord.ui.View):
    def __init__(self, *, custom_id: str, category_key: str):
        super().__init__(timeout=None)
        self.add_item(TicketActionsSelect(category_key=category_key))
        self._custom_id = custom_id

# ---- Termos
class TermsView(discord.ui.View):
    def __init__(self, *, custom_id: str):
        super().__init__(timeout=None)
        self.add_item(AcceptButton(custom_id=f"{custom_id}:accept"))
        self.add_item(DenyButton(custom_id=f"{custom_id}:deny"))
        self._custom_id = custom_id

class AcceptButton(discord.ui.Button):
    def __init__(self, *, custom_id: str):
        super().__init__(label="Aceitar", style=discord.ButtonStyle.success, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return await _ephemeral_ok(interaction, "❌ Use no canal do ticket.")
        opener_id_raw = _topic_kv(ch.topic, "opener")
        if not opener_id_raw.isdigit():
            return await _ephemeral_ok(interaction, "⚠️ Não consegui identificar o solicitante.")
        opener = interaction.guild.get_member(int(opener_id_raw))
        if not isinstance(opener, discord.Member):
            return await _ephemeral_ok(interaction, "⚠️ Solicitante não está mais no servidor.")

        # apenas o autor (ou equipe) pode aceitar
        if interaction.user.id != opener.id and not _is_admin(interaction.user):
            return await _ephemeral_ok(interaction, "❌ Somente o autor do ticket pode aceitar os termos.")

        # liberar permissões
        try:
            await ch.set_permissions(opener, view_channel=True, send_messages=True, attach_files=True, embed_links=True)
        except Exception:
            pass

        # remover botões
        try:
            for item in list(self.view.children):  # type: ignore
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await interaction.message.edit(view=self.view)
        except Exception:
            pass

        # Confirma
        emb = discord.Embed(
            title="✅ Termos aceitos",
            description=f"{opener.mention} aceitou os termos em <t:{_now_unix()}:f>.",
            color=discord.Color.green()
        )
        _brand(emb)
        await ch.send(embed=emb)

        # DM
        try:
            dm = discord.Embed(
                title="📜 Termos aceitos — Vhe Code 🌟",
                description="Seus termos de uso foram **aceitos**. Bom atendimento! ✨",
                color=discord.Color.green()
            )
            _brand(dm)
            await opener.send(embed=dm)
        except Exception:
            pass

        # Log
        if TERMS_LOG_CHANNEL_ID:
            tlog = interaction.guild.get_channel(TERMS_LOG_CHANNEL_ID)
            if isinstance(tlog, discord.TextChannel):
                lg = discord.Embed(
                    title="🟢 Termos — Aceito",
                    description=f"Usuário: {opener.mention} (`{opener.id}`)\nCanal: {ch.mention}\nQuando: <t:{_now_unix()}:f>",
                    color=discord.Color.green()
                )
                _brand(lg)
                await tlog.send(embed=lg)

        await _ephemeral_ok(interaction, "✔ Termos aceitos. Você já pode enviar mensagens.")

class DenyButton(discord.ui.Button):
    def __init__(self, *, custom_id: str):
        super().__init__(label="Negar", style=discord.ButtonStyle.danger, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return await _ephemeral_ok(interaction, "❌ Use no canal do ticket.")
        opener_id_raw = _topic_kv(ch.topic, "opener")
        opener = interaction.guild.get_member(int(opener_id_raw)) if opener_id_raw.isdigit() else None

        # Log como negado
        if TERMS_LOG_CHANNEL_ID:
            tlog = interaction.guild.get_channel(TERMS_LOG_CHANNEL_ID)
            if isinstance(tlog, discord.TextChannel):
                lg = discord.Embed(
                    title="🔴 Termos — Negado",
                    description=f"Usuário: {getattr(opener, 'mention', '—')} (`{getattr(opener, 'id', '—')}`)\nCanal: {ch.mention}\nQuando: <t:{_now_unix()}:f>",
                    color=discord.Color.red()
                )
                _brand(lg)
                await tlog.send(embed=lg)

        # DM motivo do encerramento
        try:
            if isinstance(opener, discord.Member):
                dm = discord.Embed(
                    title="❌ Ticket encerrado",
                    description="Atendimento encerrado por **discordância dos termos de serviço** da Vhe Code 🌟.",
                    color=discord.Color.red()
                )
                _brand(dm)
                await opener.send(embed=dm)
        except Exception:
            pass

        await _ephemeral_ok(interaction, "🚪 Termos negados. Encerrando o ticket…")
        await asyncio.sleep(1.0)
        try:
            await ch.delete(reason="Termos negados pelo solicitante")
        except Exception:
            pass

# ---- Modais para ações
class AddUserModal(discord.ui.Modal, title="Adicionar membro ao ticket"):
    def __init__(self, category_key: str):
        super().__init__(timeout=180)
        self.category_key = category_key
        self.user_input = discord.ui.TextInput(
            label="ID ou menção do usuário",
            placeholder="Ex.: 123456789012345678 ou @Fulano",
            required=True,
            max_length=64
        )
        self.add_item(self.user_input)

    async def on_submit(self, itx: discord.Interaction):
        ch = itx.channel
        if not isinstance(ch, discord.TextChannel):
            return await _ephemeral_ok(itx, "❌ Use dentro do canal do ticket.")
        opener_id_raw = _topic_kv(ch.topic, "opener")
        opener = itx.guild.get_member(int(opener_id_raw)) if opener_id_raw.isdigit() else None

        if not (_is_admin(itx.user) or (isinstance(opener, discord.Member) and itx.user.id == opener.id)):
            return await _ephemeral_ok(itx, "❌ Apenas autor do ticket ou equipe pode adicionar.")

        member = _parse_member(itx.guild, str(self.user_input.value))
        if not isinstance(member, discord.Member):
            return await _ephemeral_ok(itx, "⚠️ Usuário inválido.")

        try:
            await ch.set_permissions(member, view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True)
        except Exception as e:
            return await _ephemeral_ok(itx, f"❌ Falha ao adicionar: `{e}`")

        emb = discord.Embed(
            title="➕ Membro adicionado",
            description=f"{itx.user.mention} adicionou {member.mention} ao ticket.",
            color=discord.Color.green()
        )
        _brand(emb)
        await ch.send(embed=emb)
        await _ephemeral_ok(itx, "✅ Adicionado.")

class RemoveUserModal(discord.ui.Modal, title="Remover membro do ticket"):
    def __init__(self, category_key: str):
        super().__init__(timeout=180)
        self.category_key = category_key
        self.user_input = discord.ui.TextInput(
            label="ID ou menção do usuário",
            placeholder="Ex.: 123456789012345678 ou @Fulano",
            required=True,
            max_length=64
        )
        self.add_item(self.user_input)

    async def on_submit(self, itx: discord.Interaction):
        ch = itx.channel
        if not isinstance(ch, discord.TextChannel):
            return await _ephemeral_ok(itx, "❌ Use dentro do canal do ticket.")
        opener_id_raw = _topic_kv(ch.topic, "opener")
        opener = itx.guild.get_member(int(opener_id_raw)) if opener_id_raw.isdigit() else None
        if not (_is_admin(itx.user) or (isinstance(opener, discord.Member) and itx.user.id == opener.id)):
            return await _ephemeral_ok(itx, "❌ Apenas autor do ticket ou equipe pode remover.")

        member = _parse_member(itx.guild, str(self.user_input.value))
        if not isinstance(member, discord.Member):
            return await _ephemeral_ok(itx, "⚠️ Usuário inválido.")
        try:
            await ch.set_permissions(member, overwrite=None)
        except Exception as e:
            return await _ephemeral_ok(itx, f"❌ Falha ao remover: `{e}`")

        emb = discord.Embed(
            title="➖ Membro removido",
            description=f"{itx.user.mention} removeu {member.mention} do ticket.",
            color=discord.Color.orange()
        )
        _brand(emb)
        await ch.send(embed=emb)
        await _ephemeral_ok(itx, "✅ Removido.")

class CloseReasonModal(discord.ui.Modal, title="Encerrar Ticket — Motivo"):
    def __init__(self, category_key: str):
        super().__init__(timeout=300)
        self.category_key = category_key
        self.reason = discord.ui.TextInput(
            label="Motivo do encerramento",
            placeholder="Explique resumidamente o motivo",
            max_length=200
        )
        self.add_item(self.reason)

    async def on_submit(self, itx: discord.Interaction):
        await _process_close(itx, self.category_key, str(self.reason.value or "").strip())

# ============ Ações utilitárias ============
async def _notify_opener(itx: discord.Interaction):
    ch = itx.channel
    if not isinstance(ch, discord.TextChannel):
        return await _ephemeral_ok(itx, "❌ Use dentro do canal do ticket.")
    opener_id_raw = _topic_kv(ch.topic, "opener")
    categoria = _topic_kv(ch.topic, "categoria") or "ticket"
    assunto = _topic_kv(ch.topic, "assunto") or "—"

    if not opener_id_raw.isdigit():
        return await _ephemeral_ok(itx, "❌ Não consegui identificar o solicitante.")
    opener = itx.guild.get_member(int(opener_id_raw))
    if not isinstance(opener, discord.Member):
        return await _ephemeral_ok(itx, "❌ Solicitante não está mais no servidor.")

    try:
        emb = discord.Embed(
            title="🔔 Atualização do seu Ticket",
            description=(f"Seu ticket **{categoria}** está em atendimento.\n"
                         f"**Assunto:** `{assunto}`\n"
                         f"Acompanhe aqui: {ch.jump_url}"),
            color=discord.Color.blurple()
        )
        _brand(emb)
        btn = discord.ui.View(timeout=120)
        btn.add_item(discord.ui.Button(label="Ir para o ticket", url=ch.jump_url, style=discord.ButtonStyle.link))
        await opener.send(embed=emb, view=btn)
    except Exception as e:
        log.warning("Falha ao DM opener: %s", e)
        return await _ephemeral_ok(itx, "⚠️ Não consegui enviar **DM** (provável bloqueio).")

    ok = discord.Embed(
        title="📨 Solicitante notificado",
        description=f"O atendente {itx.user.mention} notificou {opener.mention} por DM.",
        color=discord.Color.green()
    )
    _brand(ok)
    await ch.send(embed=ok)
    await _ephemeral_ok(itx, "✅ Notificado por DM.")

# ============ ENCERRAMENTO (com transcript + log) ============
# ================== FILA DE FECHAMENTO (seguro + logs + posição) ==================
close_queue: asyncio.Queue = asyncio.Queue()
current_processing: Optional[str] = None

async def _process_close(itx: discord.Interaction, category_key: str, reason: str):
    """Adiciona o ticket na fila de fechamento e envia logs com posição."""
    pos = close_queue.qsize() + 1
    await close_queue.put((itx, category_key, reason))

    log.info(f"🕒 Ticket '{itx.channel.name}' adicionado à fila (posição {pos})")

    # Log visual no canal de transcripts, se existir
    if TRANSCRIPT_LOG_CHANNEL_ID:
        ch_log = itx.guild.get_channel(TRANSCRIPT_LOG_CHANNEL_ID)
        if isinstance(ch_log, discord.TextChannel):
            emb = discord.Embed(
                title="🕒 Ticket adicionado à fila",
                description=f"**Canal:** {itx.channel.mention}\n"
                            f"**Encerrado por:** {itx.user.mention}\n"
                            f"**Posição na fila:** `{pos}`\n"
                            f"**Motivo:** `{reason or '—'}`",
                color=discord.Color.orange(),
                timestamp=dt.datetime.now()
            )
            _brand(emb)
            await ch_log.send(embed=emb)

    await _ephemeral_ok(
        itx,
        f"🕒 Ticket adicionado à fila de fechamento.\n"
        f"**Posição na fila:** `{pos}`\n"
        f"Aguarde enquanto processamos outros transcripts..."
    )

async def close_worker(bot: commands.Bot):
    """Processa tickets da fila um por vez, com logs e contador."""
    global current_processing
    log.info("🧩 Worker de fechamento iniciado com sucesso.")
    while True:
        itx, category_key, reason = await close_queue.get()
        try:
            ch = getattr(itx, "channel", None)
            current_processing = ch.name if ch else "Desconhecido"
            fila_restante = close_queue.qsize()

            log.info(f"🚀 Processando '{current_processing}' (restantes: {fila_restante})")

            # Log início
            if TRANSCRIPT_LOG_CHANNEL_ID and itx.guild:
                ch_log = itx.guild.get_channel(TRANSCRIPT_LOG_CHANNEL_ID)
                if isinstance(ch_log, discord.TextChannel):
                    e = discord.Embed(
                        title="🚀 Iniciando fechamento de ticket",
                        description=f"**Canal:** {ch.mention}\n**Responsável:** {itx.user.mention}",
                        color=discord.Color.blurple(),
                        timestamp=dt.datetime.now()
                    )
                    _brand(e)
                    await ch_log.send(embed=e)

            await _process_close_real(itx, category_key, reason)

            # Log finalização
            if TRANSCRIPT_LOG_CHANNEL_ID and itx.guild:
                ch_log = itx.guild.get_channel(TRANSCRIPT_LOG_CHANNEL_ID)
                if isinstance(ch_log, discord.TextChannel):
                    e2 = discord.Embed(
                        title="✅ Ticket finalizado da fila",
                        description=f"**Canal:** `{current_processing}`\n**Encerrado por:** {itx.user.mention}",
                        color=discord.Color.green(),
                        timestamp=dt.datetime.now()
                    )
                    _brand(e2)
                    await ch_log.send(embed=e2)

        except Exception as e:
            log.exception(f"Erro no fechamento da fila: {e}")
        finally:
            current_processing = None
            close_queue.task_done()
        await asyncio.sleep(3)

# ================== FUNÇÃO REAL DE FECHAMENTO ==================

async def _process_close_real(itx: discord.Interaction, category_key: str, reason: str):
    import datetime as dt
    import tempfile
    import re
    from cogs.transcript_html_core import generate_transcript_html
    from utils.ftp_uploader import upload_to_hostgator

    bot = itx.client
    ch = itx.channel
    guild = itx.guild
    if not isinstance(ch, discord.TextChannel) or not guild:
        return await _ephemeral_ok(itx, "❌ Use dentro do canal do ticket.")
    if not _is_admin(itx.user):
        return await _ephemeral_ok(itx, "❌ Apenas equipe pode encerrar.")

    opener_id_raw = _topic_kv(ch.topic, "opener")
    opener = guild.get_member(int(opener_id_raw)) if opener_id_raw and opener_id_raw.isdigit() else None

    transcript_url = None
    try:
        mensagens_coletadas = []
        async for msg in ch.history(limit=None, oldest_first=True):
            if msg.author.bot and not msg.content and not msg.embeds and not msg.attachments:
                continue

            # ===== CARGO VISUAL =====
            role_html = ""
            if isinstance(msg.author, discord.Member):
                roles = [r for r in msg.author.roles if r.name != "@everyone"]
                if roles:
                    top_role = max(roles, key=lambda r: r.position)
                    role_color = f"#{top_role.color.value:06x}" if top_role.color.value != 0 else "#b9bbbe"
                    role_html = (
                        f'<span class="role" style="color:{role_color};background-color:{role_color}22;'
                        f'border:1px solid {role_color}55;padding:2px 6px;border-radius:5px;'
                        f'font-size:12px;font-weight:600;margin-left:6px;">'
                        f'{discord.utils.escape_markdown(top_role.name)}</span>'
                    )

            # ===== MENÇÕES E TEMPO =====
            def clean_mentions(content: str) -> str:
                if not content:
                    return ""
                content = re.sub(r"<@!?(\d+)>", lambda m: f"@{guild.get_member(int(m.group(1))) or m.group(1)}", content)
                content = re.sub(r"<@&(\d+)>", lambda m: f"@{guild.get_role(int(m.group(1))) or m.group(1)}", content)
                content = re.sub(r"<#(\d+)>", lambda m: f"#{guild.get_channel(int(m.group(1))) or m.group(1)}", content)
                content = re.sub(
                    r"<t:(\d+):[a-zA-Z]>",
                    lambda m: dt.datetime.fromtimestamp(int(m.group(1))).strftime("%d/%m/%Y %H:%M:%S"),
                    content
                )
                return content

            content_fixed = clean_mentions(msg.content or "")
            attachments_urls = [a.url for a in msg.attachments]

            # ===== EMBEDS =====
            embed_data = []
            for emb in msg.embeds:
                d = emb.to_dict()
                fields = [
                    {"name": f.get("name") or "", "value": f.get("value") or "", "inline": bool(f.get("inline"))}
                    for f in d.get("fields", []) or []
                ]
                embed_data.append({
                    "title": d.get("title"),
                    "description": d.get("description"),
                    "color": f"#{d.get('color'):06x}" if d.get("color") else "#5865F2",
                    "image": (d.get("image") or {}).get("url"),
                    "thumbnail": (d.get("thumbnail") or {}).get("url"),
                    "fields": fields,
                    "footer_text": (d.get("footer") or {}).get("text"),
                    "footer_icon": (d.get("footer") or {}).get("icon_url"),
                })

            avatar_url = (
                str(msg.author.display_avatar.url)
                if getattr(msg.author, "display_avatar", None)
                else "https://cdn.discordapp.com/embed/avatars/0.png"
            )

            mensagens_coletadas.append({
                "time": msg.created_at.strftime("%d/%m/%Y %H:%M:%S"),
                "author": str(getattr(msg.author, "display_name", getattr(msg.author, "name", "Usuário"))),
                "content": content_fixed,
                "attachments": attachments_urls,
                "embeds": embed_data,
                "avatar": avatar_url,
                "role_html": role_html
            })

        # ===== GERAR HTML =====
        header_img = str(guild.icon.url) if guild.icon else "https://cdn.discordapp.com/embed/avatars/1.png"
        html = await generate_transcript_html(ch.name, mensagens_coletadas, header_img)

        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
        temp.write(html.encode("utf-8"))
        temp.close()

        filename = f"{dt.datetime.now():%Y-%m-%d_%H-%M-%S}-{ch.name}.html"
        transcript_url = await upload_to_hostgator(temp.name, filename)
    except Exception as e:
        log.error(f"Erro ao gerar transcript: {e}")

    # ====== LOG CENTRALIZADO (usa LogsCog) ======
    emb = discord.Embed(
        title="📁 Ticket Encerrado",
        description=(
            f"**Canal:** {ch.mention}\n"
            f"**Encerrado por:** {itx.user.mention}\n"
            f"**Motivo:** `{reason or '—'}`\n"
            f"**Data:** <t:{int(dt.datetime.now().timestamp())}:f>"
        ),
        color=discord.Color.red()
    )
    if transcript_url:
        emb.add_field(name="🔗 Transcript", value=f"[Abrir Transcript]({transcript_url})", inline=False)
    _brand(emb)
    await _send_ticket_log(bot, guild, emb)  # 🔥 cai no mesmo canal do logs.py

    # ====== AVISAR USUÁRIO (DM) ======
    if isinstance(opener, discord.Member):
        try:
            dm = discord.Embed(
                title="🧾 Ticket encerrado",
                description=(
                    f"Seu ticket **{ch.name}** foi encerrado por {itx.user.mention}.\n"
                    f"**Motivo:** `{reason or '—'}`"
                ),
                color=discord.Color.red()
            )
            _brand(dm)
            if transcript_url:
                view = discord.ui.View()
                view.add_item(discord.ui.Button(label="📄 Abrir Transcript", url=transcript_url, style=discord.ButtonStyle.link))
                await opener.send(embed=dm, view=view)
            else:
                await opener.send(embed=dm)
        except Exception as e:
            log.warning(f"Falha ao enviar DM ao usuário: {e}")

    # ====== MENSAGEM FINAL NO CANAL ======
    try:
        done = discord.Embed(
            title="✅ Ticket encerrado",
            description=f"Encerrado por {itx.user.mention}.\n**Motivo:** `{reason or '—'}`",
            color=discord.Color.red()
        )
        _brand(done)
        view_done = discord.ui.View()
        if transcript_url:
            view_done.add_item(discord.ui.Button(label="📄 Abrir Transcript", url=transcript_url, style=discord.ButtonStyle.link))
        await ch.send(embed=done, view=view_done)
    except Exception:
        pass

    await asyncio.sleep(3)
    try:
        await ch.delete(reason=f"Ticket fechado por {itx.user} | motivo: {reason or '—'}")
    except Exception as e:
        log.error(f"Erro ao deletar canal: {e}")


# ================== Slash Commands (extras) ==================
class TicketSlash(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _can_use_add_remove(self, itx: discord.Interaction, opener: Optional[discord.Member]) -> bool:
        return _is_admin(itx.user) or (isinstance(opener, discord.Member) and opener.id == itx.user.id)

    @app_commands.command(name="add", description="Adicionar um membro ao ticket atual.")
    @app_commands.guild_only()
    async def add(self, itx: discord.Interaction, usuario: str):
        ch = itx.channel
        if not isinstance(ch, discord.TextChannel):
            return await _ephemeral_ok(itx, "❌ Use dentro do canal do ticket.")
        opener_id_raw = _topic_kv(ch.topic, "opener")
        opener = itx.guild.get_member(int(opener_id_raw)) if opener_id_raw.isdigit() else None
        if not self._can_use_add_remove(itx, opener):
            return await _ephemeral_ok(itx, "❌ Apenas autor do ticket ou equipe pode adicionar.")
        member = _parse_member(itx.guild, usuario)
        if not isinstance(member, discord.Member):
            return await _ephemeral_ok(itx, "⚠️ Usuário inválido.")
        try:
            await ch.set_permissions(member, view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True)
        except Exception as e:
            return await _ephemeral_ok(itx, f"❌ Falha ao adicionar: `{e}`")
        emb = discord.Embed(
            title="➕ Membro adicionado",
            description=f"{itx.user.mention} adicionou {member.mention} ao ticket.",
            color=discord.Color.green()
        )
        _brand(emb)
        await ch.send(embed=emb)
        await _ephemeral_ok(itx, "✅ Adicionado.")

    @app_commands.command(name="remove", description="Remover um membro do ticket atual.")
    @app_commands.guild_only()
    async def remove(self, itx: discord.Interaction, usuario: str):
        ch = itx.channel
        if not isinstance(ch, discord.TextChannel):
            return await _ephemeral_ok(itx, "❌ Use dentro do canal do ticket.")
        opener_id_raw = _topic_kv(ch.topic, "opener")
        opener = itx.guild.get_member(int(opener_id_raw)) if opener_id_raw.isdigit() else None
        if not self._can_use_add_remove(itx, opener):
            return await _ephemeral_ok(itx, "❌ Apenas autor do ticket ou equipe pode remover.")
        member = _parse_member(itx.guild, usuario)
        if not isinstance(member, discord.Member):
            return await _ephemeral_ok(itx, "⚠️ Usuário inválido.")
        try:
            await ch.set_permissions(member, overwrite=None)
        except Exception as e:
            return await _ephemeral_ok(itx, f"❌ Falha ao remover: `{e}`")
        emb = discord.Embed(
            title="➖ Membro removido",
            description=f"{itx.user.mention} removeu {member.mention} do ticket.",
            color=discord.Color.orange()
        )
        _brand(emb)
        await ch.send(embed=emb)
        await _ephemeral_ok(itx, "✅ Removido.")

    @app_commands.command(name="notify", description="(Equipe) Notificar o solicitante por DM.")
    @app_commands.guild_only()
    async def notify(self, itx: discord.Interaction):
        if not _is_admin(itx.user):
            return await _ephemeral_ok(itx, "❌ Apenas equipe.")
        await _notify_opener(itx)

    @app_commands.command(name="close", description="(Equipe) Fechar o ticket atual.")
    @app_commands.guild_only()
    async def close(self, itx: discord.Interaction, motivo: Optional[str] = None):
        if not _is_admin(itx.user):
            return await _ephemeral_ok(itx, "❌ Apenas equipe.")
        await _process_close(itx, _topic_kv(getattr(itx.channel, "topic", ""), "categoria") or "ticket", motivo or "—")

# ================== Painel público + setup ==================
class TicketSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(env.guild_id())
        channel_id = env.ticket_panel_channel()
        channel = guild.get_channel(channel_id) if guild else None

        # Inicia o worker da fila
        if not hasattr(self.bot, "_close_worker_started"):
            self.bot.loop.create_task(close_worker(self.bot))
            self.bot._close_worker_started = True
            log.info("🧩 Worker de fechamento iniciado com sucesso.")

        if channel:
            try:
                async for msg in channel.history(limit=5):
                    if msg.author == self.bot.user and msg.embeds:
                        # Já tem painel postado, não precisa reenviar
                        return
                await self.enviar_painel_automático(channel)
            except Exception as e:
                log.error(f"Falha ao tentar enviar painel automático: {e}")

    async def enviar_painel_automático(self, canal: discord.TextChannel):
        """Envia o painel público de abertura de tickets."""
        try:
            embed = discord.Embed(
                title="🎟️ Vhe Code 🌟 | Atendimento via Ticket",
                description=(
                "**Bem-vindo(a) ao Vhe Code!** 💫\n\n"
                "Esse é o espaço onde a **imaginação vira arte** e suas ideias ganham vida.\n\n"
                "🎟️ Para **fazer um orçamento** ou **tirar dúvidas**, acesse `#tickets` e **abra seu ticket**.\n\n"
                "Deixe a criatividade fluir — o resto é com a gente. 🪄"

                ),
                color=discord.Color.purple()
            )

            # 🖼️ Logo ao lado do texto (thumbnail)
            if FOOTER_LOGO:
                embed.set_thumbnail(url=FOOTER_LOGO)

            # Rodapé padrão
            embed.set_footer(text=FOOTER_NOME, icon_url=FOOTER_LOGO or None)

            # 🎟️ View com menu de categorias
            view = TicketPanelView(custom_id="ticket_panel_view")

            await canal.send(embed=embed, view=view)
            log.info(f"✅ Painel de tickets enviado com sucesso em {canal.name}")

        except Exception as e:
            log.error(f"❌ Falha ao enviar painel automático: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(env.guild_id())
        channel_id = env.ticket_panel_channel()
        channel = guild.get_channel(channel_id) if guild else None

        # Inicia o worker da fila
        if not hasattr(self.bot, "_close_worker_started"):
            self.bot.loop.create_task(close_worker(self.bot))
            self.bot._close_worker_started = True
            log.info("🧩 Worker de fechamento iniciado com sucesso.")

        if channel:
            try:
                async for msg in channel.history(limit=5):
                    if msg.author == self.bot.user and msg.embeds:
                        # Já tem painel postado, não precisa reenviar
                        return
                await self.enviar_painel_automático(channel)
            except Exception as e:
                log.error(f"Falha ao tentar enviar painel automático: {e}")
                
    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(env.guild_id())
        channel_id = env.ticket_panel_channel()
        channel = guild.get_channel(channel_id) if guild else None

        # Inicia o worker da fila
        if not hasattr(self.bot, "_close_worker_started"):
            self.bot.loop.create_task(close_worker(self.bot))
            self.bot._close_worker_started = True
            log.info("🧩 Worker de fechamento iniciado com sucesso.")

        if channel:
            try:
                async for msg in channel.history(limit=5):
                    if msg.author == self.bot.user and msg.embeds:
                        # Já tem painel postado, não precisa reenviar
                        return
                await self.enviar_painel_automático(channel)
            except Exception as e:
                log.error(f"Falha ao tentar enviar painel automático: {e}")



# ================== REGISTRO FINAL ==================
async def setup(bot: commands.Bot):
    """Carrega views persistentes e registra comandos."""
    # Views persistentes
    bot.add_view(TicketPanelView(custom_id="ticket_panel_persistent"))
    bot.add_view(TicketActionsView(custom_id="ticket_actions:any", category_key="suporte"))
    bot.add_view(TermsView(custom_id="terms:any:any"))

    # Cogs principais
    await bot.add_cog(TicketSystem(bot))
    await bot.add_cog(TicketSlash(bot))

    # 🔁 Sync manual na guild
    try:
        guild_id = env.guild_id()
        if guild_id:
            guild = discord.Object(id=guild_id)
            for cmd in bot.tree.get_commands():
                bot.tree.add_command(cmd, guild=guild)
            await bot.tree.sync(guild=guild)
            log.info(f"✅ Comandos do Ticket sincronizados manualmente para a guild {guild_id}")
    except Exception as e:
        log.exception(f"❌ Falha ao registrar comandos do Ticket: {e}")
