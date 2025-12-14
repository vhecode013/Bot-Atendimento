# cogs/transcript_html_core.py
from __future__ import annotations

import datetime as dt
import html
import logging
import base64
from typing import List, Dict
import re
import aiohttp

log = logging.getLogger("transcript_html")

def discord_mentions_to_text(content: str, guild=None) -> str:
    """Converte menções do Discord (<@>, <@&>, <#>) em texto legível."""
    if not content:
        return ""

    def repl_user(match):
        uid = match.group(1)
        if guild:
            m = guild.get_member(int(uid))
            if m:
                return f"@{m.display_name}"
        return f"@{uid}"

    def repl_role(match):
        rid = match.group(1)
        if guild:
            r = guild.get_role(int(rid))
            if r:
                return f"@{r.name}"
        return f"@&{rid}"

    def repl_channel(match):
        cid = match.group(1)
        if guild:
            c = guild.get_channel(int(cid))
            if c:
                return f"#{c.name}"
        return f"#{cid}"

    content = re.sub(r"<@!?(\d+)>", repl_user, content)
    content = re.sub(r"<@&(\d+)>", repl_role, content)
    content = re.sub(r"<#(\d+)>", repl_channel, content)
    return content

# =========================
# Helpers básicos
# =========================
def escape(s: str) -> str:
    return html.escape(s or "", quote=True)

def _strip_q(url: str) -> str:
    return (url or "").split("?", 1)[0].lower()

def is_image(url: str) -> bool:
    u = _strip_q(url)
    return u.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"))

def is_video(url: str) -> bool:
    u = _strip_q(url)
    return u.endswith((".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi"))

def is_audio(url: str) -> bool:
    u = _strip_q(url)
    return u.endswith((".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"))

def guess_mime(url: str, fallback: str = "application/octet-stream") -> str:
    u = _strip_q(url)
    if u.endswith(".png"): return "image/png"
    if u.endswith(".jpg") or u.endswith(".jpeg"): return "image/jpeg"
    if u.endswith(".gif"): return "image/gif"
    if u.endswith(".webp"): return "image/webp"
    return fallback

# Markdown “lite” para título/descrição
def md_lite(text: str) -> str:
    if not text:
        return ""
    t = escape(text)
    # inline code
    t = t.replace("`", "\uE001")
    seg = t.split("\uE001")
    for i in range(1, len(seg), 2):
        seg[i] = f"<code>{seg[i]}</code>"
    t = "".join(seg)
    # bold
    t = t.replace("**", "\uE002")
    seg = t.split("\uE002")
    for i in range(1, len(seg), 2):
        seg[i] = f"<strong>{seg[i]}</strong>"
    t = "".join(seg)
    # italics
    t = t.replace("*", "\uE003")
    seg = t.split("\uE003")
    for i in range(1, len(seg), 2):
        seg[i] = f"<em>{seg[i]}</em>"
    t = "".join(seg)
    # underline
    t = t.replace("__", "\uE004")
    seg = t.split("\uE004")
    for i in range(1, len(seg), 2):
        seg[i] = f"<u>{seg[i]}</u>"
    t = "".join(seg)
    return t.replace("\n", "<br>")

# =========================
# Baixar e embutir imagem em base64
# =========================
async def image_to_base64(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    mime = guess_mime(url, "image/png")
                    encoded = base64.b64encode(data).decode("utf-8")
                    return f"data:{mime};base64,{encoded}"
    except Exception as e:
        log.warning(f"[img-b64] Falha ao embutir {url}: {e}")
    return url  # fallback

# =========================
# Gerador principal do HTML
# =========================
async def generate_transcript_html(
    channel_name: str,
    messages: List[Dict],
    header_img: str = "https://cdn.discordapp.com/embed/avatars/1.png"
) -> str:
    log.info(f"[transcript_html] Gerando transcript para canal: {channel_name}")
    now = dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    header_img_src = await image_to_base64(header_img) if is_image(header_img) else header_img

    html_msgs = []
    for m in messages:
        author = escape(m.get("author", "Usuário"))
        timestamp = escape(m.get("time", ""))
        content_raw = m.get("content", "")
        content_clean = discord_mentions_to_text(content_raw)
        content_html = md_lite(content_clean)
        avatar = m.get("avatar") or "https://cdn.discordapp.com/embed/avatars/0.png"
        avatar_b64 = await image_to_base64(avatar) if is_image(avatar) else avatar
        role_html = m.get("role_html", "")

        # anexos
        att_parts: List[str] = []
        for att in m.get("attachments", []):
            if is_image(att):
                att_src = await image_to_base64(att)
                att_parts.append(f'<div class="att"><img src="{att_src}" alt="imagem" loading="lazy"/></div>')
            elif is_video(att):
                att_parts.append(
                    f'<div class="att"><video controls playsinline preload="metadata">'
                    f'<source src="{att}" type="video/mp4"></video></div>'
                )
            elif is_audio(att):
                att_parts.append(f'<div class="att"><audio controls src="{att}"></audio></div>')
            else:
                name = escape(att.split("/")[-1])
                att_parts.append(
                    f'<div class="file-card"><div class="file-name">{name}</div>'
                    f'<div class="file-btn"><a href="{att}" target="_blank" rel="noopener">Download</a></div></div>'
                )
        att_html = "".join(att_parts)

        # embeds
        emb_out: List[str] = []
        for emb in m.get("embeds", []) or []:
            color = emb.get("color") or "#5865F2"
            title = md_lite(emb.get("title", "") or "")
            desc = md_lite(emb.get("description", "") or "")
            image = emb.get("image")
            thumb = emb.get("thumbnail")
            fields = emb.get("fields") or []
            footer_text = emb.get("footer_text") or ""
            footer_icon = emb.get("footer_icon")

            emb_frag = [
                f'<div class="embed" style="border-left:4px solid {color};background:#2f3136;'
                f'border-radius:8px;padding:10px;margin-top:10px;">'
            ]

            if title:
                emb_frag.append(f'<div class="emb-title">{title}</div>')
            if desc:
                emb_frag.append(f'<div class="emb-desc">{desc}</div>')

            if fields:
                emb_frag.append('<div class="emb-fields">')
                for f in fields:
                    name = md_lite(f.get("name", "") or "")
                    value = md_lite(f.get("value", "") or "")
                    inline = bool(f.get("inline"))
                    style = "flex:1 1 calc(50% - 8px)" if inline else "flex:1 1 100%"
                    emb_frag.append(
                        f'<div class="emb-field" style="{style}">'
                        f'<div class="f-name">{name}</div>'
                        f'<div class="f-val">{value}</div>'
                        '</div>'
                    )
                emb_frag.append('</div>')

            if thumb:
                emb_frag.append(f'<img src="{thumb}" alt="thumb" class="emb-thumb">')
            if image and is_image(image):
                img_src = await image_to_base64(image)
                emb_frag.append(f'<img src="{img_src}" alt="embed image" class="emb-image">')

            if footer_text or footer_icon:
                footer_icon_html = ""
                if footer_icon and is_image(footer_icon):
                    footer_icon_html = f'<img src="{footer_icon}" class="footer-icon">'
                emb_frag.append(f'<div class="emb-footer">{footer_icon_html}{footer_text}</div>')

            emb_frag.append('</div>')
            emb_out.append("".join(emb_frag))

        emb_html = "".join(emb_out)

        html_msgs.append(f"""
        <div class="msg">
            <div class="avatar"><img src="{avatar_b64}" alt="avatar"></div>
            <div class="msg-body">
                <div class="msg-header">
                    <span class="author">{author}</span>
                    {role_html or ""}
                    <span class="timestamp">{timestamp}</span>
                </div>
                {f'<div class="text">{content_html}</div>' if content_html else ''}
                {emb_html}
                {att_html}
            </div>
        </div>
        """.strip())

    # CSS — incluindo limite de tamanho para imagens e vídeos
    css = """
    :root {
      --bg:#2b2d31; --panel:#23272a; --card:#313338; --chip:#2f3136;
      --muted:#b5bac1; --text:#dbdee1; --title:#fff; --accent:#5865F2; --line:#202225;
    }
    *{box-sizing:border-box}
    body{margin:0;padding:0;background:var(--bg);color:var(--text);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Ubuntu,Arial,"Noto Sans","Apple Color Emoji","Segoe UI Emoji";}
    header{display:flex;flex-direction:column;align-items:center;gap:8px;padding:28px 16px;
           background:var(--panel);border-bottom:1px solid var(--line)}
    header img{width:92px;height:92px;border-radius:50%;border:3px solid var(--accent);object-fit:cover}
    header h1{margin:4px 0 0;color:var(--title);font-size:24px;font-weight:800}
    header p{margin:0;color:var(--muted);font-size:13px}
    .chatlog{width:92%;max-width:980px;margin:26px auto;display:flex;flex-direction:column;gap:14px}
    .msg{display:flex;gap:12px;background:var(--card);border:1px solid #2b2d31;border-radius:12px;
         padding:12px 14px;box-shadow:0 6px 22px rgba(0,0,0,.25)}
    .avatar img{width:42px;height:42px;border-radius:50%;border:2px solid var(--line);object-fit:cover}
    .msg-body{flex:1;min-width:0}
    .msg-header{display:flex;align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap}
    .author{font-weight:700;color:#e6e6e6}
    .timestamp{color:var(--muted);font-size:.9em}
    .role{display:inline-block;font-size:12px;padding:2px 6px;border-radius:6px;background:transparent;margin-left:6px}
    .text{margin-top:6px;white-space:normal;word-break:break-word;overflow-wrap:anywhere}
    .text code{background:#1f2124;border:1px solid #2a2d31;padding:2px 5px;border-radius:4px}
    .embed{margin-top:8px;background:var(--chip);border:1px solid #2a2d31;border-left:4px solid var(--accent);
           border-radius:8px;padding:10px}
    .emb-title{color:#fff;font-weight:700;margin-bottom:4px}
    .emb-desc{color:#ddd}
    .emb-fields{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
    .emb-field{flex:1 1 100%;min-width:220px;background:#2b2d31;border:1px solid #2a2d31;border-radius:6px;padding:8px}
    .emb-field.inline{flex:1 1 calc(50% - 8px)}
    .emb-thumb{width:120px;height:120px;object-fit:cover;border-radius:8px;border:1px solid #2a2d31}
    .emb-image{max-width:100%;height:auto;border-radius:8px;border:1px solid #2a2d31;margin-top:8px}
    .footer-icon{width:16px;height:16px;border-radius:4px;vertical-align:middle;margin-right:6px}
    .emb-footer{margin-top:6px;color:#b9bbbe;display:flex;align-items:center;gap:6px}
    .att img, .att video {max-width:500px;max-height:400px;width:auto;height:auto;
                          border-radius:8px;object-fit:contain;display:block;margin:6px auto;
                          box-shadow:0 0 8px rgba(0,0,0,0.4);}
    .att video{border:1px solid #2a2d31}
    .att audio{width:100%;margin-top:8px}
    .file-card{background:#2b2d31;border:1px solid #2a2d31;border-radius:8px;padding:8px;
               display:flex;justify-content:space-between;align-items:center;margin-top:8px}
    .file-name{color:#e8e8e8;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .file-btn a{color:#fff;background:#5865F2;padding:4px 10px;border-radius:6px;text-decoration:none;font-size:13px}
    footer{text-align:center;color:var(--muted);font-size:12px;padding:20px}
    """

    html_final = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<title>Transcript — {escape(channel_name)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<style>{css}</style>
</head>
<body>
<header>
  <img src="{header_img_src}" alt="logo">
  <h1>Transcript — 💬 • {escape(channel_name)}</h1>
  <p>Gerado em {now}</p>
</header>
<div class="chatlog">
{''.join(html_msgs)}
</div>
<footer>© 2025 Vhe Code — Sistema de Transcripts Automático</footer>
</body>
</html>"""
    return html_final
