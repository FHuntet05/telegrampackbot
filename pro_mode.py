# pro_mode.py (Versión Corregida 7 - Flujo Eficiente con Iteradores)
import os
import asyncio
import re
from dotenv import load_dotenv

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageService, PeerChannel
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

async def run_mirror_task(user_chat_id: int, start_link: str, post_count: int, bot):
    """
    Tarea principal que procesa mensajes en tiempo real para máxima eficiencia.
    """
    processed_blocks_count = 0
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
            except (ValueError, ChannelPrivateError):
                 await bot.send_message(user_chat_id, f"❌ MISIÓN ABORTADA: Acceso denegado al canal.")
                 return

            # Usamos un iterador para procesar mensajes uno por uno
            async for message in client.iter_messages(source_channel_entity, offset_id=start_msg_id, reverse=True):
                if processed_blocks_count >= post_count:
                    break # Salimos del bucle principal cuando hemos procesado lo solicitado

                # Si encontramos un cambio de foto, procesamos el bloque que le sigue
                if isinstance(message, MessageService) and message.action and hasattr(message.action, 'photo'):
                    photo_msg = message
                    content_msgs = []
                    
                    # Iterador secundario para recolectar los videos de ESTE bloque
                    async for content_msg in client.iter_messages(source_channel_entity, offset_id=photo_msg.id, reverse=True):
                        # Si encontramos el siguiente cambio de foto, hemos terminado de recolectar para el bloque actual
                        if isinstance(content_msg, MessageService) and content_msg.action and hasattr(content_msg.action, 'photo'):
                            break
                        # Recolectamos solo los videos
                        if content_msg.video:
                            content_msgs.append(content_msg)
                    
                    if not content_msgs:
                        continue # Bloque sin videos, lo ignoramos

                    block_title = "Sin Título"
                    if content_msgs[0].text:
                        block_title = content_msgs[0].text.split('\n')[0].strip()[:40] + "..."

                    photo_temp_path = None
                    try:
                        photo = photo_msg.action.photo
                        photo_temp_path = await client.download_media(photo, file=f"./temp_{photo_msg.id}.jpg")
                        
                        if photo_temp_path:
                            uploaded_file = await client.upload_file(photo_temp_path)
                            action = client(EditPhotoRequest(channel=my_channel_entity, photo=uploaded_file))
                            if not await _send_with_retry(action, bot, user_chat_id):
                                total_errors += 1
                                summary_details.append(f"❌ *{block_title}*: Error al actualizar foto.")
                                continue

                        videos_sent_this_block = 0
                        for content_msg in content_msgs:
                            new_caption = clean_caption(content_msg.text)
                            action = client.send_file(my_channel_entity, content_msg.media, caption=new_caption)
                            if await _send_with_retry(action, bot, user_chat_id):
                                total_videos_sent += 1
                                videos_sent_this_block += 1
                            else:
                                total_errors += 1
                        
                        summary_details.append(f"✅ *{block_title}*: {videos_sent_this_block}/{len(content_msgs)} videos enviados.")
                        processed_blocks_count += 1

                    except Exception as e:
                        total_errors += 1
                        summary_details.append(f"❌ *{block_title}*: Error crítico: {str(e)[:50]}")
                    finally:
                        if photo_temp_path and os.path.exists(photo_temp_path):
                            os.remove(photo_temp_path)
        
        except Exception as e:
            await bot.send_message(user_chat_id, f"❌ MISIÓN ABORTADA: Error crítico general: {e}")
            return
    
    # --- Informe final ---
    final_summary = (
        f"🎉 **Misión Completada** 🎉\n\n"
        f"📄 **Resumen de Operaciones:**\n"
        f"- 🏙️ Bloques Procesados: *{processed_blocks_count} de {post_count} solicitados*\n"
        f"- 📹 Videos Totales Enviados: *{total_videos_sent}*\n"
        f"- ⚠️ Errores Encontrados: *{total_errors}*\n\n"
        f"🔍 **Informe Detallado por Bloque:**\n"
        f"{'\n'.join(summary_details) if summary_details else 'No se procesaron bloques con éxito.'}"
    )
    await bot.send_message(user_chat_id, final_summary, parse_mode=ParseMode.MARKDOWN)