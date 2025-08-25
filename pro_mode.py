# pro_mode.py
import os
import asyncio
import re
from dotenv import load_dotenv

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageService
from telethon.tl.functions.channels import EditPhotoRequest
from telethon.errors.rpcerrorlist import FloodWaitError

# Cargar variables de entorno
load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
REPLACEMENT_USERNAME = os.getenv("REPLACEMENT_USERNAME", "@estrenos_fh")
MY_CHANNEL_ID = int(os.getenv("CHANNEL_ID")) # El ID de tu canal, donde se publicará

# Función auxiliar para limpiar captions, similar a la de bot.py
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
            
            # 1. Obtener el punto de partida desde el enlace (funciona para canales públicos y privados)
            # Telethon es inteligente y puede resolver el enlace si tu cuenta tiene acceso
            source_channel_entity = await client.get_entity(start_link)
            start_msg_id = int(start_link.split('/')[-1])
            await bot.send_message(user_chat_id, f"📡 Escaneando el canal '{source_channel_entity.title}' a partir del mensaje {start_msg_id}.")
            
            my_channel_entity = await client.get_entity(MY_CHANNEL_ID)
            processed_count = 0

            # 2. Iterar mensajes DESPUÉS del punto de partida
            # reverse=True hace que vaya de mensajes viejos a nuevos, que es lo que necesitamos
            async for message in client.iter_messages(source_channel_entity, offset_id=start_msg_id, reverse=True, limit=post_count * 5): # Leemos un poco más por si hay mensajes que no son bloques
                if processed_count >= post_count:
                    await bot.send_message(user_chat_id, "🎯 Límite de posts alcanzado.")
                    break
                
                # 3. Identificar el patrón: Un mensaje de servicio de foto de perfil
                # Este mensaje indica el inicio de un nuevo bloque de contenido.
                if isinstance(message, MessageService) and message.action and hasattr(message.action, 'photo'):
                    try:
                        photo_update_msg = message
                        # El video debería ser el mensaje inmediatamente siguiente
                        async for next_msg in client.iter_messages(source_channel_entity, offset_id=photo_update_msg.id, reverse=True, limit=1):
                            video_msg = next_msg

                        if not video_msg or (not video_msg.video and not video_msg.document):
                            await bot.send_message(user_chat_id, f"⚠️ Bloque en mensaje {photo_update_msg.id} omitido: no se encontró video/documento adjunto.")
                            continue

                        await bot.send_message(user_chat_id, f"✅ Bloque {processed_count + 1}/{post_count} encontrado. Procesando...")
                        
                        # PASO A: Descargar la foto de perfil del MENSAJE DE SERVICIO y actualizarla en MI canal
                        # La foto está dentro del propio mensaje de servicio
                        photo = photo_update_msg.action.photo
                        photo_path = await client.download_media(photo, file=bytes)
                        
                        if photo_path:
                            # Subimos la foto y luego la usamos para actualizar el perfil del canal
                            uploaded_file = await client.upload_file(photo_path)
                            await client(EditPhotoRequest(channel=my_channel_entity, photo=uploaded_file))
                            await bot.send_message(user_chat_id, f"🖼️ Foto de perfil actualizada.")
                            await asyncio.sleep(5) # Pausa de cortesía

                        # PASO B: Reenviar el video/documento a MI canal con el caption limpio
                        new_caption = clean_caption(video_msg.text)
                        
                        # Usamos client.send_file que es más genérico y funciona para videos y documentos
                        await client.send_file(my_channel_entity, video_msg.media, caption=new_caption)
                        await bot.send_message(user_chat_id, f"📹 Contenido enviado.")

                        processed_count += 1
                        await bot.send_message(user_chat_id, f"✅ Bloque {processed_count}/{post_count} completado. Esperando 15 segundos...")
                        await asyncio.sleep(15) # Pausa larga entre bloques para no saturar la API

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