from __future__ import annotations
import logging
import discord
from discord.ext import commands
from discord import app_commands, Interaction
from utils import env

log = logging.getLogger("pagamentos")

GUILD_ID = env.guild_id()
GUILD_OBJ = discord.Object(id=GUILD_ID) if GUILD_ID else None


class Pagamentos(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # =========================================================
    # /pagamento — PIX
    # =========================================================
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="pagamento", description="Publica o embed de pagamento via PIX.")
    @app_commands.describe(valor="Valor do pagamento (caso não esteja configurado no .env)")
    async def pagamento(self, itx: Interaction, valor: str | None = None):
        await itx.response.defer(ephemeral=True)
        admin_roles = env.role_admin()
        user_roles = [r.id for r in getattr(itx.user, "roles", [])]
        if not any(r in user_roles for r in admin_roles):
            return await itx.followup.send("❌ Você não tem permissão para usar este comando.", ephemeral=True)

        valor_final = valor or env.pix_amount() or "A definir com o atendimento."
        chave = env.pix_key()
        qr = env.pix_qr_url()

        embed = discord.Embed(
            title="💳 Pagamento via PIX",
            description="Finalize seu pedido realizando o pagamento abaixo:",
            color=discord.Color.blurple()
        )
        embed.add_field(name="💵 Valor", value=f"```{valor_final}```", inline=False)
        if chave:
            embed.add_field(name="🔑 Chave PIX", value=f"```{chave}```", inline=False)
        if qr:
            embed.add_field(name="🖼️ QR Code", value="Aponte a câmera ou use o app do seu banco:", inline=False)
            embed.set_image(url=qr)
        embed.add_field(
            name="📩 Observação",
            value="Após o pagamento, envie o comprovante neste mesmo canal para agilizar seu atendimento.",
            inline=False
        )
        embed.set_footer(text=env.footer_nome(), icon_url=env.footer_logo())
        if env.footer_logo():
            embed.set_thumbnail(url=env.footer_logo())

        msg = await itx.channel.send(embed=embed)
        await itx.followup.send(f"✅ **PIX publicado!** [Ver mensagem]({msg.jump_url})", ephemeral=True)

  
    # =========================================================
    # /pago — confirma pagamento
    # =========================================================
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="pago", description="Confirma o pagamento e edita a última mensagem de pagamento.")
    async def pago(self, itx: Interaction):
        await itx.response.defer(ephemeral=True)
        admin_roles = env.role_admin()
        user_roles = [r.id for r in getattr(itx.user, "roles", [])]
        if not any(r in user_roles for r in admin_roles):
            return await itx.followup.send("❌ Você não tem permissão para usar este comando.", ephemeral=True)

        found_msg = None
        async for msg in itx.channel.history(limit=20):
            if msg.author == itx.client.user and msg.embeds:
                title = msg.embeds[0].title or ""
                if "Pagamento" in title:
                    found_msg = msg
                    break
        if not found_msg:
            return await itx.followup.send("⚠️ Nenhuma mensagem de pagamento encontrada neste canal.", ephemeral=True)

        embed = discord.Embed(
            title="✅ Pagamento Confirmado",
            description=(
                "Seu pagamento foi **confirmado com sucesso!** 🎉\n\n"
                "Agradecemos pela confiança no **Vhe Code** 💎\n"
                "Nossa equipe dará continuidade ao seu atendimento em breve."
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text=env.footer_nome(), icon_url=env.footer_logo())
        if env.footer_logo():
            embed.set_thumbnail(url=env.footer_logo())

        await found_msg.edit(embed=embed)
        await itx.followup.send("✅ Mensagem de pagamento atualizada para *Pagamento Confirmado!*", ephemeral=True)

    # =========================================================
    # /valor — tabela de valores
    # =========================================================
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="valor", description="Mostra a tabela de valores Vhe Code.")
    async def valor(self, itx: Interaction):
        await itx.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="💸 Tabela de Valores — Vhe Code",
            color=discord.Color.purple(),
        description=(
                "✨ **TABELA DE VALORES Vhe Code:**\n"
                "• Peça avulsa: `R$ 35,00`\n"
                "• Retexturização após conversão: `R$ 30,00`\n"
                "• Roupas Neon: `R$ 50,00`\n"
                "• Adicional de textura (mesma peça, apenas trocando nome): `R$ 10,00`\n"
                "• Cordões / Colares Personalizados: `R$ 150,00`\n"
                "• Gráficos Vhe Code: `R$ 50,00`\n"
                "• Instalação: `R$ 25,00`\n"
                "• Design: `em breve`\n"
                "• Carros: `em breve`\n\n"
                "🎁 **Pacotes Promocionais de Roupas:**\n"
                "• 6 peças: `R$ 180,00`\n"
                "• 12 peças: `R$ 360,00`\n"
                "• 18 peças: `R$ 540,00`\n"
                "• 24 peças: `R$ 720,00`\n\n"
                "💳 **Formas de Pagamento:**\n"
                "• Pagamento via PIX e Wise\n"
                "• Para valores acima de R$100,00: pagamento em 2 partes (50% + 50%)\n"
                "• Para valores abaixo de R$100,00: pagamento integral antecipado"
            )
        )
        embed.set_footer(text=env.footer_nome(), icon_url=env.footer_logo())
        if env.footer_logo():
            embed.set_thumbnail(url=env.footer_logo())
        msg = await itx.channel.send(embed=embed)
        await itx.followup.send(f"📦 **Tabela publicada!** [Ver mensagem]({msg.jump_url})", ephemeral=True)

    # =========================================================
    # /pedido — instruções de solicitação
    # =========================================================
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="pedido", description="Envia as instruções para solicitação de arte de roupas.")
    async def pedido(self, itx: Interaction):
        await itx.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="🧵 Solicitação de Arte para Roupas — Vhe Code",
            color=discord.Color.blurple(),
            description=(
                "Para criarmos sua arte com perfeição, envie as seguintes informações:\n\n"
                "📍 **Cidade:**\n"
                "👕 **Quantidade e tipo de peças:** (Ex: 1 jaqueta masc, 1 calça fem...)\n"
                "🎨 **Cores desejadas:** (base e detalhes)\n"
                "💬 **Elementos ou frases:** (Ex: leão nas costas, frase no peito...)\n"
                "✍️ **Nome e posição:** (Ex: nome na manga ou costas...)\n"
                "📸 **Fotos e inspirações:** envie referências ou prints de ideias.\n\n"
                "⏰ **Prazo de entrega:** 7 dias úteis (podendo ser antes conforme demanda)\n"
                "💰 **Produção:** inicia após o envio do comprovante de pagamento.\n\n"
                "💖 Obrigada por escolher o **Vhe Code**, onde seu estilo ganha vida! ✨"
            )
        )
        embed.set_footer(text=env.footer_nome(), icon_url=env.footer_logo())
        if env.footer_logo():
            embed.set_thumbnail(url=env.footer_logo())
        msg = await itx.channel.send(embed=embed)
        await itx.followup.send(f"🧵 **Instruções publicadas!** [Ver mensagem]({msg.jump_url})", ephemeral=True)


# =========================================================
# SETUP
# =========================================================
async def setup(bot: commands.Bot):
    await bot.add_cog(Pagamentos(bot))
    log.info("✅ Cog 'Pagamentos' carregada — sincronizando comandos na guild…")
    try:
        if GUILD_OBJ:
            synced = await bot.tree.sync(guild=GUILD_OBJ)
            log.info(f"🏠 Sync (pagamentos) para guild {GUILD_ID}: {[c.name for c in synced]}")
    except Exception as e:
        log.exception("❌ Falha ao sincronizar comandos Pagamentos:", exc_info=e)
