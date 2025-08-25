# pro_mode.py (Versión Corregida 8 - Lógica de Acumulación con un solo iterador)
import os
import asyncio
import re
from dotenv import load_dotenv

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageService, PeerChannel, Message
from telethon.tl.functions.channels import EditPhotoRequest
from telethon.errors.rpcerrorlist import FloodWaitError, ChannelPrivateError
from telegram.constants import ParseMode

# Cargar variables de entorno
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
REPLACEMENT_USERNAME = os.getenv("REPLACEMENT_USERNAME", "@estrenos_fh")
MY_CHANNEL_ID = int(os.getenv("CHANNEL_ID")) 

def parse_private_link(link: str) -> tuple[int | None, int | None]:
    match = re.match(r"https?://t\.me/c/(\d+)/(\d+)", link)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None

def clean_caption(original_caption: str | None) -> str:
    if not original_caption: return ""
    pattern = r'@\w+|https?://t\.me/\S+'
    return re.sub(pattern, REPLACEMENT_USERNAME, original_caption)

async def _send_with_retry(client_action, bot, user_chat_id):
    """Función wrapper silenciosa para manejar FloodWaitError."""
    try:
        await client_action
        return True
    except FloodWaitError as fwe:
        if fwe.seconds > 10:
             await bot.send_message(user_chat_id, f"⏳ Telegram está ocupado. El bot esperará automáticamente {fwe.seconds} segundos y continuará.")
        await asyncio.sleep(fwe.seconds + 1)
        try:
            await client_action
            return True
        except Exception:
            return False
    except Exception:
        return False

async def _process_block(block: dict, bot, user_chat_id, client, my_channel_entity) -> tuple[int, int, str]:
    """Función aislada para procesar un solo bloque de contenido."""
    videos_sent = 0
    errors = 0
    
    block_title = "Sin Título"
    if block["videos"] and block["videos"][0].text:
        block_title = block["videos"][0].text.split('\n')[0].strip()[:40] + "..."
        
    photo_temp_path = None
    try:
        photo_msg = block["photo_msg"]
        photo_temp_path = await client.download_media(photo_msg.action.photo, file=f"./temp_{photo_msg.id}.jpg")
        
        if photo_temp_path:
            uploaded_file = await client.upload_file(photo_temp_path)
            action = client(EditPhotoRequest(channel=my_channel_entity, photo=uploaded_file))
            if not await _send_with_retry(action, bot, user_chat_id):
                errors += 1
                return 0, errors, f"❌ *{block_title}*: Error al actualizar foto."

        for video_msg in block["videos"]:
            new_caption = clean_caption(video_msg.text)
            action = client.send_file(my_channel_entity, video_msg.media, caption=new_caption)
            if await _send_with_retry(action, bot, user_chat_id):
                videos_sent += 1
            else:
                errors += 1
        
        return videos_sent, errors, f"✅ *{block_title}*: {videos_sent}/{len(block['videos'])} videos enviados."
    
    except Exception as e:
        return videos_sent, errors + 1, f"❌ *{block_title}*: Error crítico: {str(e)[:50]}"
    finally:
        if photo_temp_path and os.path.exists(photo_temp_path):
            os.remove(photo_temp_path)


async def run_mirror_task(user_chat_id: int, start_link: str, post_count: int, bot):
    """
    Tarea principal que usa una lógica de acumulación con un solo iterador para máxima eficiencia y respeto a la API.
    """
    total_blocks_processed = 0
    total_videos_sent = 0
    total_errors = 0
    summary_details = []

    async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
        try:
            me = await client.get_me()
            await bot.send_message(user_chat_id, f"🤖 Agente '{me.first_name}' activado. Iniciando escaneo eficiente para {post_count} bloques. Recibirás un informe al finalizar.")

            source_channel_id, start_msg_id = parse_private_link(start_link)
            if not source_channel_id:
                await bot.send_message(user_chat_id, "❌ MISIÓN ABORTADA: Formato de enlace incorrecto.")
                return

            try:
                source_channel_entity = await client.get_entity(PeerChannel(source_channel_id))
                my_channel_entity = await client.get_entity(MY_CHANNEL_ID)
            except Exception as e:
                 await bot.send_message(user_chat_id, f"❌ MISIÓN ABORTADA: No se pudo acceder a los canales: {e}")
                 return

            current_block = {}
            # Usamos un solo iterador
            async for message in client.iter_messages(source_channel_entity, offset_id=start_msg_id, reverse=True):
                if total_blocks_processed >= post_count:
                    break

                if isinstance(message, MessageService) and message.action and hasattr(message.action, 'photo'):
                    # Encontramos el inicio de un nuevo bloque. Primero, procesamos el bloque anterior si tenía videos.
                    if current_block.get("videos"):
                        sent, errs, summary = await _process_block(current_block, bot, user_chat_id, client, my_channel_entity)
                        total_videos_sent += sent
                        total_errors += errs
                        summary_details.append(summary)
                        total_blocks_processed += 1
                    
                    # Iniciamos el nuevo bloque
                    current_block = {"photo_msg": message, "videos": []}
                
                elif message.video and current_block:
                    # Si estamos dentro de un bloque, acumulamos el video
                    current_block.setdefault("videos", []).append(message)
            
            # Al final del bucle, puede quedar un último bloque sin procesar. Lo procesamos aquí.
            if total_blocks_processed < post_count and current_block.get("videos"):
                sent, errs, summary = await _process_block(current_block, bot, user_chat_id, client, my_channel_entity)
                total_videos_sent += sent
                total_errors += errs
                summary_details.append(summary)
                total_blocks_processed += 1
        
        except Exception as e:
            await bot.send_message(user_chat_id, f"❌ MISIÓN ABORTADA: Error crítico general: {e}")
            return
    
    # --- Informe final ---
    final_summary = (
        f"🎉 **Misión Completada** 🎉\n\n"
        f"📄 **Resumen de Operaciones:**\n"
        f"- 🏙️ Bloques Procesados: *{total_blocks_processed} de {post_count} solicitados*\n"
        f"- 📹 Videos Totales Enviados: *{total_videos_sent}*\n"
        f"- ⚠️ Errores Encontrados: *{total_errors}*\n\n"
        f"🔍 **Informe Detallado por Bloque:**\n"
        f"{'\n'.join(summary_details) if summary_details else 'No se procesaron bloques con éxito.'}"
    )
    await bot.send_message(user_chat_id, final_summary, parse_mode=ParseMode.MARKDOWN)