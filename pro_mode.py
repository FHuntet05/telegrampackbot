# pro_mode.py (Versión Corregida)
import os
import asyncio
import re
from dotenv import load_dotenv

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageService, PeerChannel
from telethon.tl.functions.channels import EditPhotoRequest
from telethon.errors.rpcerrorlist import FloodWaitError, ChannelPrivateError

# Cargar variables de entorno
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
REPLACEMENT_USERNAME = os.getenv("REPLACEMENT_USERNAME", "@estrenos_fh")
MY_CHANNEL_ID = int(os.getenv("CHANNEL_ID")) # El ID de tu canal, donde se publicará

# --- NUEVA FUNCIÓN ---
def parse_private_link(link: str) -> tuple[int | None, int | None]:
    """Extrae el ID del canal y del mensaje de un enlace t.me/c/."""
    match = re.match(r"https?://t\.me/c/(\d+)/(\d+)", link)
    if match:
        channel_id = int(match.group(1))
        msg_id = int(match.group(2))
        return channel_id, msg_id
    return None, None

def clean_caption(original_caption: str | None) -> str:
    if not original_caption: return ""
    pattern = r'@\w+|https?://t\.me/\S+'
    return re.sub(pattern, REPLACEMENT_USERNAME, original_caption)

async def run_mirror_task(user_chat_id: int, start_link: str, post_count: int, bot):
    """
    Tarea principal que se conecta como usuario, escanea el canal fuente y publica en el destino.
    """
    async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
        try:
            me = await client.get_me()
            await bot.send_message(user_chat_id, f"🤖 Conectado como '{me.first_name}'. Iniciando proceso...")

            # --- LÓGICA DE ENLACE CORREGIDA ---
            source_channel_id, start_msg_id = parse_private_link(start_link)
            if not source_channel_id or not start_msg_id:
                await bot.send_message(user_chat_id, f"❌ ERROR: El formato del enlace privado no es correcto. Debe ser `t.me/c/ID_CANAL/ID_MENSAJE`.")
                return

            try:
                # Forma más robusta de obtener el canal usando su ID numérico
                source_channel_entity = await client.get_entity(PeerChannel(source_channel_id))
            except (ValueError, ChannelPrivateError):
                 await bot.send_message(user_chat_id, f"❌ ERROR: No se pudo acceder al canal con ID `{source_channel_id}`. Asegúrate de que la cuenta '{me.first_name}' es miembro del canal.")
                 return

            await bot.send_message(user_chat_id, f"📡 Escaneando el canal '{source_channel_entity.title}' a partir del mensaje {start_msg_id}.")
            
            my_channel_entity = await client.get_entity(MY_CHANNEL_ID)
            processed_count = 0

            # Damos más margen para buscar los mensajes, por si hay texto o cosas que no son bloques.
            search_limit = post_count * 10
            await bot.send_message(user_chat_id, f"🔍 Buscando {post_count} bloques dentro de los próximos {search_limit} mensajes...")

            async for message in client.iter_messages(source_channel_entity, offset_id=start_msg_id, reverse=True, limit=search_limit):
                if processed_count >= post_count:
                    await bot.send_message(user_chat_id, "🎯 Límite de bloques a procesar alcanzado.")
                    break
                
                if isinstance(message, MessageService) and message.action and hasattr(message.action, 'photo'):
                    try:
                        photo_update_msg = message
                        async for next_msg in client.iter_messages(source_channel_entity, offset_id=photo_update_msg.id, reverse=True, limit=1):
                            video_msg = next_msg

                        if not video_msg or (not video_msg.video and not video_msg.document):
                            await bot.send_message(user_chat_id, f"⚠️ Bloque en mensaje {photo_update_msg.id} omitido: no se encontró video/documento adjunto.")
                            continue

                        await bot.send_message(user_chat_id, f"✅ Bloque {processed_count + 1}/{post_count} encontrado (ID: {photo_update_msg.id}). Procesando...")
                        
                        photo = photo_update_msg.action.photo
                        photo_path = await client.download_media(photo, file=bytes)
                        
                        if photo_path:
                            uploaded_file = await client.upload_file(photo_path)
                            await client(EditPhotoRequest(channel=my_channel_entity, photo=uploaded_file))
                            await bot.send_message(user_chat_id, f"🖼️ Foto de perfil actualizada.")
                            await asyncio.sleep(5) 

                        new_caption = clean_caption(video_msg.text)
                        await client.send_file(my_channel_entity, video_msg.media, caption=new_caption)
                        await bot.send_message(user_chat_id, f"📹 Contenido enviado.")

                        processed_count += 1
                        await bot.send_message(user_chat_id, f"✅ Bloque {processed_count}/{post_count} completado. Esperando 15 segundos...")
                        await asyncio.sleep(15) 

                    except FloodWaitError as fwe:
                        await bot.send_message(user_chat_id, f"⏳ Límite de Telegram alcanzado. Esperando {fwe.seconds} segundos...")
                        await asyncio.sleep(fwe.seconds)
                    except Exception as e:
                        error_msg = str(e)
                        if len(error_msg) > 300: error_msg = error_msg[:300] + "..."
                        await bot.send_message(user_chat_id, f"⚠️ Error procesando un bloque: `{error_msg}`. Saltando al siguiente.")
                        continue
            
            await bot.send_message(user_chat_id, f"🎉 ¡Tarea completada! Se procesaron {processed_count} de los {post_count} solicitados.")

        except Exception as e:
            error_msg = str(e)
            if len(error_msg) > 300: error_msg = error_msg[:300] + "..."
            await bot.send_message(user_chat_id, f"❌ ERROR CRÍTICO en el Modo Pro: `{error_msg}`\nLa tarea ha sido detenida.")