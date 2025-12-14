# ==========================================================
# utils/ftp_uploader.py — versão final para HostGator
# usuário FTP já inicia em /public_html/transcripts
# ==========================================================

import os
import asyncio
import logging
from typing import Optional

log = logging.getLogger("transcript")

try:
    import aioftp
    HAS_AIOFTP = True
except Exception:
    HAS_AIOFTP = False
    import ftplib


def _clean_filename(name: str) -> str:
    """Garante nome válido e extensão .html"""
    base = os.path.basename(str(name)).strip().replace("\\", "/")
    base = base.replace("/", "_")
    if not base.lower().endswith(".html"):
        base += ".html"
    return base


def _public_url(filename: str) -> str:
    """Gera URL pública do arquivo"""
    base = os.getenv("HOSTGATOR_BASE_URL", "").rstrip("/")
    return f"{base}/transcripts/{_clean_filename(filename)}" if base else f"/transcripts/{_clean_filename(filename)}"


# ==========================================================
# Upload com aioftp — simples e direto
# ==========================================================
async def _upload_aioftp(local_path: str, remote_filename: str) -> str:
    host = os.getenv("HOSTGATOR_FTP_HOST")
    user = os.getenv("HOSTGATOR_FTP_USER")
    pwd = os.getenv("HOSTGATOR_FTP_PASS")

    if not (host and user and pwd):
        raise RuntimeError("HOSTGATOR_FTP_HOST/USER/PASS não configuradas.")

    fname = _clean_filename(remote_filename)

    # 💡 O usuário FTP já está em /public_html/transcripts/
    remote_path = fname

    async with aioftp.Client.context(host, user=user, password=pwd) as client:
        log.info(f"[aioftp] Fazendo upload direto: {remote_path}")
        async with client.upload_stream(remote_path) as stream:
            with open(local_path, "rb") as f:
                await stream.write(f.read())

    url = _public_url(fname)
    log.info(f"[aioftp] Upload concluído: {url}")
    return url


# ==========================================================
# Upload com ftplib — fallback se aioftp não estiver disponível
# ==========================================================
def _upload_ftplib(local_path: str, remote_filename: str) -> str:
    host = os.getenv("HOSTGATOR_FTP_HOST")
    user = os.getenv("HOSTGATOR_FTP_USER")
    pwd = os.getenv("HOSTGATOR_FTP_PASS")

    if not (host and user and pwd):
        raise RuntimeError("HOSTGATOR_FTP_HOST/USER/PASS não configuradas.")

    fname = _clean_filename(remote_filename)

    ftp = ftplib.FTP()
    try:
        ftp.connect(host, 21, timeout=30)
        ftp.login(user, pwd)
        ftp.set_pasv(True)

        # ⚠️ NÃO muda de pasta (já começa em /public_html/transcripts)
        with open(local_path, "rb") as f:
            ftp.storbinary(f"STOR {fname}", f)

    finally:
        try:
            ftp.quit()
        except Exception:
            pass

    url = _public_url(fname)
    log.info(f"[ftplib] Upload concluído: {url}")
    return url


# ==========================================================
# Função principal
# ==========================================================
async def upload_to_hostgator(local_path: str, remote_filename: str) -> Optional[str]:
    """Faz upload direto do arquivo HTML para o diretório base do FTP"""
    if not os.path.isfile(local_path):
        log.error(f"Arquivo local não existe: {local_path}")
        return None

    fname = _clean_filename(remote_filename)
    try:
        if HAS_AIOFTP:
            return await _upload_aioftp(local_path, fname)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _upload_ftplib(local_path, fname))
    except Exception as e:
        log.exception(f"Falha no upload: {e}")
        return None
