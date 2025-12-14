from __future__ import annotations
import discord
import logging

log = logging.getLogger("itx")

async def safe_defer(itx: discord.Interaction, *, ephemeral: bool = True, thinking: bool = False):
    """Tenta defer apenas se ainda não respondeu."""
    try:
        if not itx.response.is_done():
            await itx.response.defer(ephemeral=ephemeral, thinking=thinking)
            log.debug(f"💭 Deferred interação {itx.user} em {itx.channel}")
    except discord.InteractionResponded:
        pass
    except Exception as e:
        log.error(f"Erro ao deferir interação: {e}")

async def safe_reply(
    itx: discord.Interaction,
    content: str | None = None,
    *,
    ephemeral: bool = True,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    file: discord.File | None = None
):
    """Responde a uma interação sem risco de erro de dupla resposta."""
    try:
        if not itx.response.is_done():
            await itx.response.send_message(content=content, embed=embed, view=view, ephemeral=ephemeral, file=file)
        else:
            await itx.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral, file=file)
        log.debug(f"💬 Resposta enviada com sucesso ({itx.user})")
    except discord.InteractionResponded:
        try:
            await itx.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral, file=file)
        except Exception:
            pass
    except Exception as e:
        log.error(f"Erro ao responder interação: {e}")
