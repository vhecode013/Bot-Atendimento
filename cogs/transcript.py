import discord
from discord.ext import commands
import datetime as dt
import tempfile
import logging
import re
from utils.ftp_uploader import upload_to_hostgator
from cogs.transcript_html_core import generate_transcript_html
import aiohttp
import os
import asyncio

log = logging.getLogger("transcript")

# ===================== Utils =====================

def _sanitize(name: str) -> str:
    # só letras/números/_/-, troca qualquer separador por _
    return re.sub(r"[^A-Za-z0-9_-]", "_", name or "transcript")

def _strip_q(u: str) -> str:
    return (u or "").split("?", 1)[0]

def _safe_ext_from(url_or_name: str, fallback: str = ".png") -> str:
    ext = os.path.splitext(_strip_q(url_or_name))[1].lower()
    return ext if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif") else fallback

def _looks_like_image(filename: str) -> bool:
    fn = (filename or "").lower()
    return any(fn.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"))

async def _mirror_image(url: str, filename: str) -> str:
    """Baixa a URL e envia pro HostGator. Retorna a URL pública, ou a original se falhar."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=45) as resp:
                if resp.status == 200:
                    ext = _safe_ext_from(url)
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                    tmp.write(await resp.read())
                    tmp.close()
                    new_url = await upload_to_hostgator(tmp.name, filename)
                    try:
                        os.remove(tmp.name)
                    except Exception:
                        pass
                    if new_url:
                        return new_url
                else:
                    log.warning(f"Download falhou {resp.status} para {url}")
    except Exception as e:
        log.warning(f"Mirror falhou para {url}: {e}")
    return url  # fallback

# ===================== Cog =====================

class TranscriptCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        log.info("🧩 Cog Transcript carregada com HostGator ativo")

    async def generate_transcript(self, channel: discord.TextChannel):
        log.info(f"Gerando transcript de {channel.name} ({channel.id})...")

        # 🔹 Header (ícone da guild) espelhado
        header_img = "https://cdn.discordapp.com/embed/avatars/1.png"
        try:
            if channel.guild and channel.guild.icon:
                header_img = await _mirror_image(str(channel.guild.icon.url), f"guild_{channel.guild.id}.png")
        except Exception as e:
            log.warning(f"Header mirror falhou: {e}")

        messages = []
        avatar_cache: dict[int, str] = {}
        count = 0

        # 🔹 Histórico completo, mas com "lotes" de 100 p/ evitar abuso
        async for msg in channel.history(limit=None, oldest_first=True):
            # Ignora mensagens vazias de bot sem anexos
            if msg.author.bot and not msg.content and not msg.attachments:
                continue

            # 🔸 Avatar via CDN + cache
            if msg.author.id not in avatar_cache:
                avatar_cache[msg.author.id] = (
                    str(msg.author.avatar.url)
                    if getattr(msg.author, "avatar", None)
                    else "https://cdn.discordapp.com/embed/avatars/0.png"
                )
            avatar_url = avatar_cache[msg.author.id]

            # 🔸 Anexos — espelha apenas imagens, demais mantêm URL original
            attachments_urls = []
            for a in msg.attachments:
                try:
                    is_img = (a.content_type and a.content_type.startswith("image")) or _looks_like_image(a.filename)
                    if is_img:
                        ext = _safe_ext_from(a.filename)
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                        await a.save(tmp.name)
                        # upload em FILA (sem paralelismo) para não floodar
                        new_url = await upload_to_hostgator(tmp.name, f"att_{a.id}_{a.filename}")
                        attachments_urls.append(new_url or a.url)
                        try:
                            os.remove(tmp.name)
                        except Exception:
                            pass
                        # pequeno respiro entre anexos (evita avalanche em hosts mais lentos)
                        await asyncio.sleep(0.05)
                    else:
                        attachments_urls.append(a.url)
                except Exception as e:
                    log.warning(f"Falha ao processar anexo {getattr(a, 'filename', a.url)}: {e}")
                    attachments_urls.append(a.url)

            messages.append({
                "time": msg.created_at.strftime("%d/%m/%Y %H:%M:%S"),
                "author": f"{msg.author.display_name}",
                "content": msg.content,
                "attachments": attachments_urls,
                "avatar": avatar_url
            })

            count += 1
            # 🔸 Delay por LOTE de 100 mensagens (modo ultra-seguro)
            if count % 100 == 0:
                await asyncio.sleep(0.8)

        # 🔹 Gera HTML e salva localmente
        html = generate_transcript_html(channel.name, messages, header_img)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
        tmp.write(html.encode("utf-8"))
        tmp.close()
        log.info(f"Transcript local: {tmp.name} ({len(messages)} msgs)")
        return tmp.name

    async def generate_and_upload(self, channel: discord.TextChannel) -> str | None:
        local_path = await self.generate_transcript(channel)
        safe = _sanitize(channel.name)
        filename = f"{safe}_{dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.html"
        # envia SEM jamais incluir public_html/transcripts no nome
        url = await upload_to_hostgator(local_path, filename)
        return url

    @discord.app_commands.command(name="testetranscript", description="Gera manualmente um transcript deste canal.")
    async def testetranscript(self, itx: discord.Interaction):
        # Mantém exatamente o comportamento de mensagens que você já tinha
        await itx.response.defer(thinking=True, ephemeral=True)
        if not isinstance(itx.channel, discord.TextChannel):
            return await itx.followup.send("❌ Use em canal de texto.", ephemeral=True)
        url = await self.generate_and_upload(itx.channel)
        if url:
            await itx.followup.send(f"✅ Transcript gerado!\n🔗 {url}", ephemeral=True)
        else:
            await itx.followup.send("❌ Falha ao gerar/enviar transcript.", ephemeral=True)

# ===================== Setup =====================

async def setup(bot: commands.Bot):
    await bot.add_cog(TranscriptCog(bot))
