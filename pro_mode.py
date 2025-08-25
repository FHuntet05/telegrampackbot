# pro_mode.py (Versión Corregida 5 - Ignora .zip y otros documentos)
import os
import asyncio
import re
from dotenv import load_dotenv

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageService, PeerChannel, Message
from telethon.tl.functions.channels import EditPhotoRequest
from telethon.errors.rpcerrorlist import FloodWaitError, ChannelPrivateError

# Cargar variables de entorno
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
REPLACEMENT_USERNAME = os.getenv("REPLACEMENT_USERNAME", "@estrenos_fh")
MY_CHANNEL_ID = int(os.getenv("CHANNEL_ID")) 

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

async def _send_with_retry(client_action, bot, user_chat_id, action_description):
    """Función wrapper para manejar FloodWaitError en cualquier acción de Telethon."""
    while True:
        try:
            await client_action
            return True
        except FloodWaitError as fwe:
            await bot.send_message(user_chat_id, f"⏳ Telegram ocupado ({action_description}). Esperando {fwe.seconds} segundos...")
            await asyncio.sleep(fwe.seconds + 1) # Añadimos 1 segundo de margen
        except Exception as e:
            await bot.send_message(user_chat_id, f"⚠️ Error inesperado durante '{action_description}': {e}")
            return False


async def run_mirror_task(user_chat_id: int, start_link: str, post_count: int, bot):
    """
    Tarea principal que escanea y publica bloques de [1 Foto -> N Videos].
    """
    async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
        try:
            me = await client.get_me()
            await bot.send_message(user_chat_id, f"🤖 Conectado como '{me.first_name}'. Iniciando proceso...")

            source_channel_id, start_msg_id = parse_private_link(start_link)
            if not source_channel_id or not start_msg_id:
                await bot.send_message(user_chat_id, f"❌ ERROR: El formato del enlace privado no es correcto.")
                return

            try:
                source_channel_entity = await client.get_entity(PeerChannel(source_channel_id))
            except (ValueError, ChannelPrivateError):
                 await bot.send_message(user_chat_id, f"❌ ERROR: No se pudo acceder al canal. Asegúrate de que '{me.first_name}' es miembro.")
                 return

            await bot.send_message(user_chat_id, f"📡 Escaneando el canal '{source_channel_entity.title}' a partir del mensaje {start_msg_id}.")
            
            my_channel_entity = await client.get_entity(MY_CHANNEL_ID)
            processed_blocks_count = 0
            
            # Recopilamos todos los mensajes en una lista para poder procesarlos en bloques
            all_messages = await client.get_messages(source_channel_entity, offset_id=start_msg_id, reverse=True, limit=None)
            
            if not all_messages:
                await bot.send_message(user_chat_id, "No se encontraron mensajes después del punto de partida.")
                return
            
            # Encontramos los índices de todos los mensajes de cambio de foto
            photo_change_indices = [i for i, msg in enumerate(all_messages) if isinstance(msg, MessageService) and msg.action and hasattr(msg.action, 'photo')]

            if not photo_change_indices:
                await bot.send_message(user_chat_id, "No se encontraron cambios de foto de perfil después del punto de partida.")
                return
            
            await bot.send_message(user_chat_id, f"🔍 Se encontraron {len(photo_change_indices)} posibles bloques de contenido. Procesando los primeros {post_count}...")

            for i in range(len(photo_change_indices)):
                if processed_blocks_count >= post_count:
                    break
                
                start_index = photo_change_indices[i]
                # El bloque termina donde empieza el siguiente cambio de foto, o al final de la lista
                end_index = photo_change_indices[i+1] if i + 1 < len(photo_change_indices) else len(all_messages)
                
                block_messages = all_messages[start_index:end_index]
                photo_msg = block_messages[0]
                
                # <<< --- ESTA ES LA LÍNEA MODIFICADA --- >>>
                # Ahora solo recopilamos mensajes que son explícitamente videos.
                content_msgs = [msg for msg in block_messages[1:] if msg.video]

                if not content_msgs:
                    await bot.send_message(user_chat_id, f"⚠️ Bloque iniciado en {photo_msg.id} omitido: no contiene videos.")
                    continue
                
                await bot.send_message(user_chat_id, f"✅ Procesando bloque {processed_blocks_count + 1}/{post_count} (ID: {photo_msg.id}) con {len(content_msgs)} videos.")
                
                photo_temp_path = None
                try:
                    # --- 1. ACTUALIZAR FOTO DE PERFIL ---
                    photo = photo_msg.action.photo
                    photo_temp_path = await client.download_media(photo, file=f"./temp_{photo_msg.id}.jpg")
                    if photo_temp_path:
                        uploaded_file = await client.upload_file(photo_temp_path)
                        action = EditPhotoRequest(channel=my_channel_entity, photo=uploaded_file)
                        success = await _send_with_retry(client(action), bot, user_chat_id, "actualizar foto")
                        if success:
                           await bot.send_message(user_chat_id, f"🖼️ Foto de perfil actualizada.")

                    # --- 2. ENVIAR TODO EL CONTENIDO ---
                    for idx, content_msg in enumerate(content_msgs):
                        await bot.send_message(user_chat_id, f"   - Enviando video {idx + 1}/{len(content_msgs)}...")
                        new_caption = clean_caption(content_msg.text)
                        action = client.send_file(my_channel_entity, content_msg.media, caption=new_caption)
                        await _send_with_retry(action, bot, user_chat_id, f"enviar video {idx+1}")

                    processed_blocks_count += 1
                    await bot.send_message(user_chat_id, f"✅ Bloque {processed_blocks_count}/{post_count} completado.")

                except Exception as e:
                    await bot.send_message(user_chat_id, f"❌ Error grave procesando el bloque {photo_msg.id}: {e}. Abortando este bloque.")
                finally:
                    if photo_temp_path and os.path.exists(photo_temp_path):
                        os.remove(photo_temp_path)

            await bot.send_message(user_chat_id, f"🎉 ¡Tarea completada! Se procesaron {processed_blocks_count} de los {post_count} solicitados.")

        except Exception as e:
            error_msg = str(e)
            if len(error_msg) > 300: error_msg = error_msg[:300] + "..."
            await bot.send_message(user_chat_id, f"❌ ERROR CRÍTICO en el Modo Pro: `{error_msg}`\nLa tarea ha sido detenida.")