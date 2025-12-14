# utils/env.py
import os
import logging
import discord
from dotenv import load_dotenv

log = logging.getLogger("env")

# Carrega .env
load_dotenv()

def _s(val: str | None, default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip()

def _safe_int(val: str | None, default: int = 0) -> int:
    try:
        return int(_s(val, ""))
    except (TypeError, ValueError):
        return default

def _split_ids(raw: str | None) -> list[int]:
    raw = _s(raw, "")
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    out: list[int] = []
    for p in parts:
        if not p:
            continue
        try:
            out.append(int(p))
        except ValueError:
            log.warning(f"[env] ID inválido ignorado: {p!r}")
    return out

# ========= Funções genéricas =========
def get(name: str, default=None):
    """Pega um valor genérico do .env"""
    return os.getenv(name, default)

def get_int(name: str, default: int = 0) -> int:
    """Pega um valor numérico do .env"""
    return _safe_int(os.getenv(name), default)

# ========= Específicos do bot =========
def token() -> str:
    return _s(os.getenv("DISCORD_TOKEN"), "")

def guild_id() -> int:
    val = _safe_int(os.getenv("GUILD_ID"), 0)
    log.info(f"[env] GUILD_ID = {val}")
    return val

def footer_nome() -> str:
    val = _s(os.getenv("FOOTER_NOME"), "© 2025 Vhe Code  ✨ — Todos os direitos reservados.")
    log.info(f"[env] FOOTER_NOME = {val}")
    return val

def footer_logo() -> str:
    val = _s(os.getenv("FOOTER_LOGO_URL"), "")
    log.info(f"[env] FOOTER_LOGO_URL = {val}")
    return val

def role_admin() -> list[int]:
    ids = _split_ids(os.getenv("ROLE_ADMIN"))
    log.info(f"[env] ROLE_ADMIN = {ids}")
    return ids

def category_ids() -> dict[str, int]:
    cats = {
        "suporte": _safe_int(os.getenv("CATEGORY_SUPORTE"), 0),
        "roupas": _safe_int(os.getenv("CATEGORY_ROUPAS"), 0),
        "cordoes": _safe_int(os.getenv("CATEGORY_COROES"), 0),
        "carros": _safe_int(os.getenv("CATEGORY_CARROS"), 0),
        "design": _safe_int(os.getenv("CATEGORY_DESIGN"), 0),
        "cursos": _safe_int(os.getenv("CATEGORY_CURSOS"), 0),
    }
    for k, v in cats.items():
        log.info(f"[env] CATEGORY_{k.upper()} = {v}")
    return cats

def ticket_panel_channel() -> int:
    cid = _safe_int(os.getenv("TICKET_PANEL_CHANNEL"), 0)
    log.info(f"[env] TICKET_PANEL_CHANNEL = {cid}")
    return cid

def terms_channel_id() -> int:
    cid = _safe_int(os.getenv("TERMS_CHANNEL_ID"), 0)
    log.info(f"[env] TERMS_CHANNEL_ID = {cid}")
    return cid

def terms_log_channel_id() -> int:
    cid = _safe_int(os.getenv("TERMS_LOG_CHANNEL_ID"), 0)
    log.info(f"[env] TERMS_LOG_CHANNEL_ID = {cid}")
    return cid

def transcript_log_channel_id() -> int:
    cid = _safe_int(os.getenv("TRANSCRIPT_LOG_CHANNEL_ID"), 0)
    log.info(f"[env] TRANSCRIPT_LOG_CHANNEL_ID = {cid}")
    return cid

def ftp_password() -> str:
 
    import os
    return os.getenv("FTP_PASSWORD", "")

def entrada_channel() -> int:
    return int(os.getenv("ENTRADA_CANAL_ID", "0"))

def saida_channel() -> int:
    return int(os.getenv("SAIDA_CANAL_ID", "0"))

def log_bot_channel() -> int:
    return int(os.getenv("LOG_BOT_CHANNEL_ID", "0"))

def cargo_auto() -> int:
    return int(os.getenv("CARGO_AUTO", "0"))

# ========= PIX / Pagamentos =========
def pix_key() -> str:
    val = _s(os.getenv("PIX_KEY"), "")
    log.info(f"[env] PIX_KEY = {val}")
    return val

def pix_qr_url() -> str:
    val = _s(os.getenv("PIX_QR_URL"), "")
    log.info(f"[env] PIX_QR_URL = {val}")
    return val

def pix_amount() -> str:
    val = _s(os.getenv("PIX_AMOUNT"), "")
    log.info(f"[env] PIX_AMOUNT = {val}")
    return val


async def ephemeral_ok(itx: discord.Interaction, text: str):
    """Responde de forma ephemeral segura."""
    if itx.response.is_done():
        await itx.followup.send(text, ephemeral=True)
    else:
        await itx.response.send_message(text, ephemeral=True)