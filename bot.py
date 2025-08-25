# bot.py
import os
import logging
import re
import asyncio
import traceback
import html
import json
import calendar
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz
from bson import ObjectId
from dateutil.relativedelta import relativedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
from telegram.error import RetryAfter, BadRequest

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore

import database as db
import subtitles as sub_api
import pro_mode # Importamos el nuevo módulo para el Modo Pro

# --- Cargar y Configurar ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
REPLACEMENT_USERNAME = os.getenv("REPLACEMENT_USERNAME", "@estrenos_fh")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
MONGO_URI = os.getenv("MONGO_URI")
TIMEZONE = pytz.timezone(os.getenv("TIMEZONE", "America/Havana"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL") 
PORT = int(os.getenv("PORT", "8443"))
jobstores = {'default': MongoDBJobStore(database="telegramBotDB", collection="jobs", host=MONGO_URI)}
scheduler = AsyncIOScheduler(jobstores=jobstores, timezone=TIMEZONE)
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Teclados Personalizados ---
MAIN_KEYBOARD = ReplyKeyboardMarkup([
    ["📦 Crear Pack"], 
    ["📋 Gestionar Packs"], 
    ["🔎 Buscar Subtítulos"],
    ["🚀 Activar Modo Pro"] # Nuevo botón
], resize_keyboard=True)
EDITING_KEYBOARD = ReplyKeyboardMarkup([["✅ Terminar Creación/Edición"]], resize_keyboard=True)
CANCEL_KEYBOARD = ReplyKeyboardMarkup([["❌ Cancelar"]], resize_keyboard=True)

# --- GESTOR DE ERRORES Y UTILIDADES ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)
    update_str = str(update.to_dict()) if isinstance(update, Update) else str(update)
    error_message = (f"Ocurrió una excepción:\n\n<pre>update = {html.escape(update_str)}</pre>\n\n<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n<pre>{html.escape(tb_string)}</pre>")
    message_parts = [error_message[i:i + 4000] for i in range(0, len(error_message), 4000)]
    for part in message_parts:
        try:
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=part, parse_mode=ParseMode.HTML)
        except BadRequest as e:
            logger.error(f"No se pudo enviar parte del log de error: {e}")
    if isinstance(update, Update) and update.effective_chat:
        try:
            await update.effective_chat.send_message("❌ Ups, algo salió mal. Volviendo al menú principal.", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            logger.error(f"No se pudo enviar el mensaje de error de recuperación al usuario: {e}")
    context.user_data.clear()

def clean_caption(original_caption: str | None) -> str:
    if not original_caption: return ""
    pattern = r'@\w+|https?://t\.me/\S+'
    return re.sub(pattern, REPLACEMENT_USERNAME, original_caption)

# --- LÓGICA DE PUBLICACIÓN CENTRALIZADA Y ROBUSTA ---
async def _publish_pack_logic(bot, pack_name: str, user_chat_id: int):
    pack_content = db.get_pack_for_sending(pack_name)
    if not pack_content:
        await bot.send_message(chat_id=user_chat_id, text=f"❌ Error: El pack '{pack_name}' está vacío o no existe.")
        return

    photo_index = 0
    while photo_index < len(pack_content):
        item = pack_content[photo_index]
        photo_sent = False
        video_index = 0
        
        for attempt in range(5):
            try:
                if not photo_sent:
                    temp_photo_path = None
                    try:
                        photo_file_obj = await bot.get_file(item['photo_file_id'])
                        temp_photo_path = f"./{photo_file_obj.file_unique_id}.jpg"
                        await photo_file_obj.download_to_drive(custom_path=temp_photo_path)
                        with open(temp_photo_path, 'rb') as photo_to_upload:
                            await bot.set_chat_photo(chat_id=CHANNEL_ID, photo=photo_to_upload)
                        await bot.send_message(chat_id=user_chat_id, text=f"({photo_index + 1}/{len(pack_content)}) Foto de perfil actualizada. Enviando adjuntos...")
                        photo_sent = True
                    finally:
                        if temp_photo_path and os.path.exists(temp_photo_path):
                            os.remove(temp_photo_path)
                
                videos = item.get('videos', [])
                while video_index < len(videos):
                    video = videos[video_index]
                    if video.get('caption', '').startswith("SUBTITLE:"):
                        await bot.send_document(chat_id=CHANNEL_ID, document=video['file_id'], caption=video['caption'].replace("SUBTITLE:", "Subtítulo:"))
                    else:
                        await bot.send_video(chat_id=CHANNEL_ID, video=video['file_id'], caption=clean_caption(video.get('caption')))
                    video_index += 1
                    await asyncio.sleep(1.5)

                logger.info(f"Item {photo_index + 1} del pack '{pack_name}' publicado con éxito.")
                break 
            
            except RetryAfter as e:
                logger.warning(f"Flood control detectado. Esperando {e.retry_after}s. Intento {attempt + 1}/5.")
                await bot.send_message(chat_id=user_chat_id, text=f"⏳ Telegram ocupado. Reintentando en {e.retry_after + 1} segundos...")
                await asyncio.sleep(e.retry_after + 1)
            
            except Exception as e:
                logger.error(f"Error irrecuperable publicando item del pack {pack_name}: {e}")
                await bot.send_message(chat_id=user_chat_id, text=f"⚠️ Ocurrió un error grave publicando un item del pack '{pack_name}'. Saltando al siguiente.")
                break 
        else: 
            logger.error(f"No se pudo publicar el item {photo_index + 1} del pack '{pack_name}' después de varios reintentos.")
            await bot.send_message(chat_id=user_chat_id, text=f"❌ Falló la publicación del item {photo_index + 1} del pack '{pack_name}' por límites de Telegram.")
        
        photo_index += 1

    await bot.send_message(chat_id=user_chat_id, text=f"✅ Publicación del pack '{pack_name}' finalizada.", reply_markup=MAIN_KEYBOARD)

async def publish_pack_job(pack_name: str, user_chat_id: int):
    application = Application.builder().token(BOT_TOKEN).build()
    await _publish_pack_logic(application.bot, pack_name, user_chat_id)

async def send_pack_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, pack_name = query.data.split(":", 1)
    await query.edit_message_text(f"🚀 Iniciando publicación del pack '{pack_name}'. Esto puede tardar...")
    await _publish_pack_logic(context.bot, pack_name, update.effective_chat.id)
    try:
        await query.delete_message()
    except Exception:
        pass

# --- GESTORES CENTRALES DE MENSAJES ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get('state')
    
    if text == "📦 Crear Pack":
        await pack_create_start(update, context)
    elif text == "📋 Gestionar Packs":
        await list_packs_command(update, context)
    elif text == "✅ Terminar Creación/Edición":
        await finish_creation_editing(update, context)
    elif text == "❌ Cancelar":
        await cancel_action(update, context)
    elif text == "🔎 Buscar Subtítulos":
        await subtitle_search_independent_start(update, context)
    elif text == "🚀 Activar Modo Pro": # NUEVO
        await start_modo_pro(update, context)
    elif state == 'awaiting_pack_name':
        await pack_await_name(update, context)
    elif state in ['awaiting_subtitle_search', 'awaiting_subtitle_search_independent']:
        await handle_subtitle_search_query(update, context)
    elif state == 'awaiting_source_link': # NUEVO
        await handle_source_link(update, context)
    elif state == 'awaiting_post_count': # NUEVO
        await handle_post_count(update, context)
    elif state in ['creating_pack', 'editing_pack']:
        await update.message.reply_text("Estoy esperando una foto o un video. Si no quieres añadir más, pulsa 'Terminar Creación/Edición'.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') in ['creating_pack', 'editing_pack']:
        await add_photo_to_pack_flow(update, context)
    else:
        await handle_immediate_photo(update, context)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') in ['creating_pack', 'editing_pack'] and context.user_data.get('last_photo_id'):
        await add_video_to_photo_flow(update, context)
    elif context.user_data.get('state') == 'awaiting_videos':
        await pack_add_video_in_edit(update, context)
    else:
        await handle_immediate_video(update, context)
        
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'awaiting_subtitle':
        await add_subtitle_to_photo_flow(update, context)

# --- MODO PRO (NUEVO) ---
async def start_modo_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo de configuración del Modo Pro."""
    context.user_data.clear() # Limpiamos cualquier estado anterior
    context.user_data['state'] = 'awaiting_source_link'
    await update.message.reply_text(
        "🚀 Modo Pro Activado.\n\n"
        "Por favor, ve al canal fuente (privado o público) y reenvíame o copia el enlace del **último video que ya publicaste** en tu canal.\n\n"
        "Este será el punto de partida.",
        reply_markup=CANCEL_KEYBOARD
    )

async def handle_source_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe y valida el enlace del mensaje de inicio."""
    link = update.message.text
    if "t.me" not in link or "/" not in link:
        await update.message.reply_text("❌ El enlace no parece válido. Por favor, envía un enlace de mensaje de Telegram. (Ej: https://t.me/c/123456789/123)")
        return
        
    context.user_data['start_link'] = link
    context.user_data['state'] = 'awaiting_post_count'
    await update.message.reply_text(
        "✅ Enlace recibido.\n\n"
        "Ahora, dime cuántos bloques de contenido (Foto + Video) quieres que procese desde ese punto en adelante.\n\n"
        "Escribe solo un número (ej: `20`).",
        reply_markup=CANCEL_KEYBOARD
    )

async def handle_post_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe el número de posts a procesar y lanza la tarea."""
    try:
        count = int(update.message.text)
        if count <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Por favor, introduce un número entero positivo.")
        return

    start_link = context.user_data['start_link']
    
    await update.message.reply_text(
        f"⏳ ¡Entendido! Iniciando la tarea de procesar {count} bloques.\n\n"
        "Esto puede tardar. Te notificaré sobre el progreso. Ya puedes volver a usar otros comandos.",
        reply_markup=MAIN_KEYBOARD
    )
    
    context.user_data.clear()
    
    # Lanzamos la tarea pesada en segundo plano para no bloquear el bot
    asyncio.create_task(
        pro_mode.run_mirror_task(
            user_chat_id=update.effective_chat.id,
            start_link=start_link,
            post_count=count,
            bot=context.bot # Pasamos la instancia del bot para que pueda enviar notificaciones
        )
    )

# --- MODO INMEDIATO ---
async def handle_immediate_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for attempt in range(3):
        try:
            await update.message.reply_text("Procesando foto (Modo Inmediato)...")
            temp_photo_path = None
            photo_file_obj = await update.message.photo[-1].get_file()
            temp_photo_path = f"./{photo_file_obj.file_unique_id}.jpg"
            await photo_file_obj.download_to_drive(custom_path=temp_photo_path)
            with open(temp_photo_path, 'rb') as photo_to_upload:
                await context.bot.set_chat_photo(chat_id=CHANNEL_ID, photo=photo_to_upload)
            await update.message.reply_text("✅ Foto de perfil actualizada.")
            if temp_photo_path and os.path.exists(temp_photo_path): os.remove(temp_photo_path)
            return
        except RetryAfter as e:
            logger.warning(f"Flood control (foto): Esperando {e.retry_after}s. Intento {attempt + 1}/3.")
            await update.message.reply_text(f"⏳ Telegram ocupado. Reintentando en {e.retry_after+1} segundos...")
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            logger.error(f"Error en modo inmediato (foto): {e}")
            await update.message.reply_text(f"❌ Ocurrió un error inesperado: {e}")
            if temp_photo_path and os.path.exists(temp_photo_path): os.remove(temp_photo_path)
            return
    await update.message.reply_text("❌ No se pudo actualizar la foto de perfil por límites de Telegram.")

async def handle_immediate_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_caption = clean_caption(update.message.caption)
    for attempt in range(3):
        try:
            await context.bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=update.message.chat_id, message_id=update.message.message_id, caption=new_caption)
            await update.message.reply_text("✅ Video enviado al canal (Modo Inmediato).")
            return
        except RetryAfter as e:
            logger.warning(f"Flood control (video): Esperando {e.retry_after}s. Intento {attempt + 1}/3.")
            await update.message.reply_text(f"⏳ Telegram ocupado. Reintentando en {e.retry_after+1} segundos...")
            await asyncio.sleep(e.retry_after + 1)
        except Exception as e:
            logger.error(f"Error en modo inmediato (video): {e}")
            await update.message.reply_text("❌ Ocurrió un error inesperado al enviar el video.")
            return
    await update.message.reply_text("❌ No se pudo enviar el video por límites de Telegram.")


# --- MENÚS Y COMANDOS PRINCIPALES ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("👋 ¡Hola! Soy tu asistente de contenido.", reply_markup=MAIN_KEYBOARD)

async def list_packs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text, reply_markup = await _get_pack_list_markup(user_id=ADMIN_USER_ID)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def _get_pack_list_markup(user_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    jobs = scheduler.get_jobs()
    scheduled_packs = {}
    for job in jobs:
        if job.id.startswith("pack:"):
            try:
                _, pack_name, _ = job.id.split(":", 2)
                local_run_time = job.next_run_time.astimezone(TIMEZONE)
                scheduled_packs[pack_name] = local_run_time.strftime("%d/%m %H:%M")
            except Exception: continue
    packs_per_page = 5
    all_packs = db.list_all_packs(user_id)
    start_index = page * packs_per_page
    end_index = start_index + packs_per_page
    packs_to_show = all_packs[start_index:end_index]
    if not all_packs:
        return "No tienes packs creados.", InlineKeyboardMarkup([[InlineKeyboardButton("Ir al Menú Principal", callback_data="main_menu_from_empty")]])
    text = "Selecciona un pack para gestionar:"
    keyboard = []
    for name in packs_to_show:
        display_name = name + (f" (🗓️ {scheduled_packs[name]})" if name in scheduled_packs else "")
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"pack_select:{name}")])
    pagination_row = []
    if page > 0: pagination_row.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"pack_list_{page-1}"))
    if end_index < len(all_packs): pagination_row.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"pack_list_{page+1}"))
    if pagination_row: keyboard.append(pagination_row)
    keyboard.append([InlineKeyboardButton("⬅️ Volver al Menú", callback_data="main_menu_from_empty")])
    return text, InlineKeyboardMarkup(keyboard)

async def list_packs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('_')[2])
    text, reply_markup = await _get_pack_list_markup(user_id=ADMIN_USER_ID, page=page)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def main_menu_from_empty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.delete_message()
    await query.message.reply_text("Menú principal:", reply_markup=MAIN_KEYBOARD)

async def select_pack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    _, pack_name = query.data.split(":", 1)
    keyboard = [
        [InlineKeyboardButton("🚀 Publicar Ahora", callback_data=f"pack_send_now:{pack_name}")],
        [InlineKeyboardButton("🗓️ Programar", callback_data=f"schedule_start:{pack_name}")],
        [InlineKeyboardButton("✏️ Editar Contenido", callback_data=f"edit_pack_start:{pack_name}")],
        [InlineKeyboardButton("🗑️ Eliminar Pack", callback_data=f"pack_delete_confirm:{pack_name}")],
        [InlineKeyboardButton("⬅️ Volver a la Lista", callback_data="pack_list_0")]]
    await query.edit_message_text(f"Acciones para el pack: *{pack_name}*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def delete_pack_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, pack_name = query.data.split(":", 1)
    keyboard = [[InlineKeyboardButton(f"SÍ, ELIMINAR '{pack_name}'", callback_data=f"pack_delete_do:{pack_name}")], [InlineKeyboardButton("NO, CANCELAR", callback_data=f"pack_select:{pack_name}")]]
    await query.edit_message_text(f"⚠️ ¿Seguro que quieres eliminar *{pack_name}*? Esta acción es irreversible.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def delete_pack_do_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, pack_name = query.data.split(":", 1)
    if db.delete_pack(pack_name, ADMIN_USER_ID):
        for job in scheduler.get_jobs():
            if job.id.startswith(f"pack:{pack_name}:"):
                job.remove()
                logger.info(f"Tarea programada para '{pack_name}' eliminada.")
        await query.answer(f"Pack '{pack_name}' eliminado.")
    else:
        await query.answer(f"❌ No se pudo eliminar.", show_alert=True)
    text, reply_markup = await _get_pack_list_markup(user_id=ADMIN_USER_ID, page=0)
    await query.edit_message_text(text, reply_markup=reply_markup)

# --- FLUJO DE CREACIÓN Y EDICIÓN ---
async def pack_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'awaiting_pack_name'
    await update.message.reply_text("OK. ¿Qué nombre le ponemos al nuevo pack?", reply_markup=CANCEL_KEYBOARD)

async def pack_await_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pack_name = update.message.text.strip()
    if not pack_name:
        await update.message.reply_text("El nombre no puede estar vacío.")
        return
    success, message = db.create_pack(pack_name, ADMIN_USER_ID)
    if not success:
        await update.message.reply_text(f"{message} Elige otro nombre.")
        return
    context.user_data['state'] = 'creating_pack'
    context.user_data['pack_name'] = pack_name
    context.user_data['last_photo_id'] = None
    await update.message.reply_text(
        f"✅ ¡Pack '{pack_name}' creado!\n\nAhora estás en **modo de creación**.\n1. Envíame una foto.\n2. Envía todos los videos para esa foto.\n3. Repite enviando otra foto y sus videos.\n\nPulsa 'Terminar Creación/Edición' cuando hayas añadido todo.",
        reply_markup=EDITING_KEYBOARD)

async def edit_pack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name = query.data.split(":", 1)
    context.user_data['state'] = 'editing_pack'
    context.user_data['pack_name'] = pack_name
    context.user_data['last_photo_id'] = None 
    text, reply_markup = await _get_pack_edit_markup(pack_name)
    await query.message.reply_text(f"✏️ Editando pack *{pack_name}*.", reply_markup=EDITING_KEYBOARD, parse_mode='Markdown')
    await query.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    await query.delete_message()

async def _get_pack_edit_markup(pack_name: str) -> tuple[str, InlineKeyboardMarkup]:
    pack = db.get_pack_details(pack_name, ADMIN_USER_ID)
    text = f"Contenido actual del pack *{pack_name}*:"
    keyboard = []
    if pack and pack.get('content'):
        for i, photo_data in enumerate(pack['content']):
            photo_id_str = str(photo_data['photo_id'])
            num_videos = len(photo_data.get('videos', []))
            label = f"Foto {i+1} ({num_videos} adjuntos)"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"photo_manage:{pack_name}:{photo_id_str}")])
    keyboard.append([InlineKeyboardButton("➕ Agregar Foto", callback_data=f"photo_add_start:{pack_name}")])
    keyboard.append([InlineKeyboardButton("⬅️ Volver a Acciones", callback_data=f"pack_select:{pack_name}")])
    return text, InlineKeyboardMarkup(keyboard)

async def photo_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name = query.data.split(":", 1)
    context.user_data['state'] = 'editing_pack'
    context.user_data['pack_name'] = pack_name
    await query.message.reply_text(f"OK, envía la nueva foto para el pack *{pack_name}*.", reply_markup=EDITING_KEYBOARD, parse_mode="Markdown")
    await query.delete_message()

async def add_photo_to_pack_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pack_name = context.user_data['pack_name']
    photo_file_id = update.message.photo[-1].file_id
    success, photo_id = db.add_photo_to_pack(pack_name, photo_file_id)
    if success:
        context.user_data['last_photo_id'] = photo_id
        await update.message.reply_text("🖼️ Foto añadida. Ahora envíame sus videos y/o subtítulos.", quote=True)
    else:
        await update.message.reply_text("❌ Error al guardar la foto.", quote=True)

async def add_video_to_photo_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pack_name = context.user_data['pack_name']
    photo_id = context.user_data['last_photo_id']
    video_file_id = update.message.video.file_id
    caption = update.message.caption or ""
    if db.add_video_to_photo(pack_name, photo_id, video_file_id, caption):
        await update.message.reply_text("📹 Video añadido.", quote=True)
    else:
        await update.message.reply_text("❌ Error al guardar el video.", quote=True)

async def finish_creation_editing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pack_name = context.user_data.get('pack_name', 'El pack')
    context.user_data.clear()
    await update.message.reply_text(f"✅ ¡Operación finalizada para el pack '{pack_name}'! Has salido del modo de creación/edición.", reply_markup=MAIN_KEYBOARD)

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Operación cancelada. Volviendo al menú principal.", reply_markup=MAIN_KEYBOARD)

async def manage_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    _, pack_name, photo_id_str = query.data.split(':', 2)
    text, reply_markup = await _get_photo_manage_markup(pack_name, photo_id_str)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def _get_photo_manage_markup(pack_name: str, photo_id_str: str) -> tuple[str, InlineKeyboardMarkup]:
    text = f"Gestionando una foto del pack *{pack_name}*."
    keyboard = [
        [InlineKeyboardButton("➕ Agregar Videos", callback_data=f"video_add_start:{pack_name}:{photo_id_str}")],
        [InlineKeyboardButton("📜 Adjuntar Subtítulo (.srt)", callback_data=f"subtitle_add_start:{pack_name}:{photo_id_str}")],
        [InlineKeyboardButton("🔎 Buscar Subtítulo Online", callback_data=f"subtitle_search_start:{pack_name}:{photo_id_str}")],
        [InlineKeyboardButton("🗑️ Eliminar esta Foto", callback_data=f"photo_delete:{pack_name}:{photo_id_str}")],
        [InlineKeyboardButton("⬅️ Volver al Pack", callback_data=f"edit_pack_start:{pack_name}")]]
    return text, InlineKeyboardMarkup(keyboard)

async def video_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name, photo_id_str = query.data.split(':', 2)
    context.user_data['state'] = 'awaiting_videos'
    context.user_data['pack_name'] = pack_name
    context.user_data['last_photo_id'] = ObjectId(photo_id_str)
    keyboard = [[InlineKeyboardButton("✅ Terminé de añadir videos", callback_data=f"video_add_done:{pack_name}:{photo_id_str}")]]
    await query.edit_message_text("OK. Estoy en modo de adición de videos.\n\n**Envíame todos los videos que quieras para esta foto.**\n\nCuando termines, pulsa el botón.",
                                  reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def pack_add_video_in_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pack_name = context.user_data['pack_name']
    photo_id = context.user_data['last_photo_id']
    video_file_id = update.message.video.file_id
    caption = update.message.caption or ""
    if db.add_video_to_photo(pack_name, photo_id, video_file_id, caption):
        await update.message.reply_text("📹 Video añadido.", quote=True)
    else:
        await update.message.reply_text("❌ Error al guardar el video.", quote=True)

async def video_add_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name, photo_id_str = query.data.split(':', 2)
    context.user_data.clear()
    await query.edit_message_text("✅ Videos guardados.")
    await asyncio.sleep(1)
    text, reply_markup = await _get_photo_manage_markup(pack_name, photo_id_str)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def subtitle_add_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name, photo_id_str = query.data.split(':', 2)
    context.user_data['state'] = 'awaiting_subtitle'
    context.user_data['pack_name'] = pack_name
    context.user_data['photo_id'] = photo_id_str
    await query.edit_message_text("OK. Envíame el archivo de subtítulos (.srt, .ass, etc.).",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel_subtitle_add:{pack_name}:{photo_id_str}")]]))

async def add_subtitle_to_photo_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pack_name = context.user_data['pack_name']
    photo_id_str = context.user_data['photo_id']
    photo_id = ObjectId(photo_id_str)
    document = update.message.document
    caption = f"SUBTITLE:{document.file_name}"
    if db.add_video_to_photo(pack_name, photo_id, document.file_id, caption):
        await update.message.reply_text("📜 Subtítulo añadido.", quote=True)
    else:
        await update.message.reply_text("❌ Error al guardar el subtítulo.", quote=True)
    context.user_data.clear()
    text, reply_markup = await _get_photo_manage_markup(pack_name, photo_id_str)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def cancel_subtitle_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name, photo_id_str = query.data.split(":", 2)
    context.user_data.clear()
    text, reply_markup = await _get_photo_manage_markup(pack_name, photo_id_str)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    
async def subtitle_search_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name, photo_id_str = query.data.split(':', 2)
    context.user_data['state'] = 'awaiting_subtitle_search'
    context.user_data['pack_name'] = pack_name
    context.user_data['photo_id'] = photo_id_str
    await query.message.reply_text("OK. ¿Qué película o serie busco? (Ej: `The Matrix` o `Breaking Bad S01E01`)", reply_markup=CANCEL_KEYBOARD)
    await query.delete_message()

async def subtitle_search_independent_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'awaiting_subtitle_search_independent'
    await update.message.reply_text("OK. ¿Qué película o serie busco?", reply_markup=CANCEL_KEYBOARD)

async def handle_subtitle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text
    await update.message.reply_text(f"🔎 Buscando subtítulos para '{query_text}'...")
    subtitles, error_msg = sub_api.search_subtitles(query_text)
    if error_msg:
        await update.message.reply_text(f"❌ Error: {error_msg}", reply_markup=MAIN_KEYBOARD)
        context.user_data.clear()
        return
    if not subtitles:
        await update.message.reply_text("No se encontraron resultados. Intenta con otro nombre.", reply_markup=MAIN_KEYBOARD)
        context.user_data.clear()
        return
    keyboard = []
    state = context.user_data.get('state')
    for sub in subtitles[:5]:
        season = f" S{sub['season']:02d}" if sub['season'] else ""
        episode = f"E{sub['episode']:02d}" if sub['episode'] else ""
        label = f"({sub['language']}) {sub['movie_name']}{season}{episode}"
        if state == 'awaiting_subtitle_search_independent':
            callback_data = f"sub_download_independent:{sub['file_id']}"
        else:
            pack_name = context.user_data['pack_name']
            photo_id_str = context.user_data['photo_id']
            callback_data = f"sub_download_pack:{pack_name}:{photo_id_str}:{sub['file_id']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("❌ Cancelar Búsqueda", callback_data="cancel_subtitle_search")])
    await update.message.reply_text("Resultados encontrados. Selecciona uno para descargar:", reply_markup=InlineKeyboardMarkup(keyboard))

async def subtitle_download_independent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, api_file_id_str = query.data.split(':')
    api_file_id = int(api_file_id_str)
    await query.edit_message_text("📥 Descargando subtítulo...")
    download_link, error_msg = sub_api.request_download_link(api_file_id)
    if error_msg:
        await query.edit_message_text(f"❌ Error: {error_msg}")
        return
    subtitle_content, error_msg = sub_api.download_subtitle_content(download_link)
    if error_msg:
        await query.edit_message_text(f"❌ Error: {error_msg}")
        return
    file_name = f"subtitulo_{api_file_id}.srt"
    await context.bot.send_document(chat_id=update.effective_chat.id, document=subtitle_content, filename=file_name)
    await query.edit_message_text("✅ Subtítulo enviado.")
    await query.message.reply_text("Búsqueda finalizada.", reply_markup=MAIN_KEYBOARD)
    context.user_data.clear()

async def subtitle_download_pack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name, photo_id_str, api_file_id_str = query.data.split(':')
    api_file_id = int(api_file_id_str)
    await query.edit_message_text("📥 Descargando y añadiendo al pack...")
    download_link, error_msg = sub_api.request_download_link(api_file_id)
    if error_msg:
        await query.edit_message_text(f"❌ Error: {error_msg}")
        return
    subtitle_content, error_msg = sub_api.download_subtitle_content(download_link)
    if error_msg:
        await query.edit_message_text(f"❌ Error: {error_msg}")
        return
    file_name = f"{pack_name}_sub_{api_file_id}.srt"
    sent_doc = await context.bot.send_document(chat_id=update.effective_chat.id, document=subtitle_content, filename=file_name)
    telegram_file_id = sent_doc.document.file_id
    photo_id = ObjectId(photo_id_str)
    caption = f"SUBTITLE:{file_name}"
    if db.add_video_to_photo(pack_name, photo_id, telegram_file_id, caption):
        await query.edit_message_text("✅ ¡Subtítulo descargado y añadido al pack!")
    else:
        await query.edit_message_text("❌ Error al guardar el subtítulo en la base de datos.")
    context.user_data.clear()
    await asyncio.sleep(1)
    text, reply_markup = await _get_photo_manage_markup(pack_name, photo_id_str)
    await query.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def cancel_subtitle_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("Búsqueda de subtítulos cancelada.")
    await query.message.reply_text("Volviendo al menú principal.", reply_markup=MAIN_KEYBOARD)

async def delete_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Eliminando foto...")
    _, pack_name, photo_id_str = query.data.split(':', 2)
    db.delete_photo_from_pack(pack_name, photo_id_str)
    text, reply_markup = await _get_pack_edit_markup(pack_name)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

async def create_calendar(year, month, pack_name):
    markup = []
    markup.append([InlineKeyboardButton(f"{datetime(year, month, 1).strftime('%B %Y')}", callback_data="noop")])
    markup.append([InlineKeyboardButton(day, callback_data="noop") for day in ["Lu", "Ma", "Mi", "Ju", "Vi", "Sa", "Do"]])
    my_calendar = calendar.monthcalendar(year, month)
    for week in my_calendar:
        row = []
        for day in week:
            if day == 0: row.append(InlineKeyboardButton(" ", callback_data="noop"))
            else: row.append(InlineKeyboardButton(str(day), callback_data=f"cal_day:{year}:{month}:{day}"))
        markup.append(row)
    prev_month = datetime(year, month, 1) - relativedelta(months=1)
    next_month = datetime(year, month, 1) + relativedelta(months=1)
    markup.append([InlineKeyboardButton("<<", callback_data=f"cal_nav:{prev_month.year}:{prev_month.month}"),
                   InlineKeyboardButton("Cancelar", callback_data=f"cal_cancel:{pack_name}"),
                   InlineKeyboardButton(">>", callback_data=f"cal_nav:{next_month.year}:{next_month.month}")])
    return InlineKeyboardMarkup(markup)

async def schedule_pack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name = query.data.split(":", 1)
    context.user_data['state'] = 'scheduling'
    context.user_data['pack_to_schedule'] = pack_name
    now = datetime.now(TIMEZONE)
    await query.edit_message_text(f"🗓️ Programando pack *{pack_name}*.\n\nLa hora actual del bot es: `{now.strftime('%H:%M')}`\n\nPor favor, selecciona una fecha:",
                                  reply_markup=await create_calendar(now.year, now.month, pack_name), parse_mode='Markdown')

async def calendar_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, year, month = query.data.split(":")
    pack_name = context.user_data.get('pack_to_schedule', 'este pack')
    await query.edit_message_text(f"🗓️ Programando pack *{pack_name}*.\n\nPor favor, selecciona una fecha:",
                                  reply_markup=await create_calendar(int(year), int(month), pack_name), parse_mode='Markdown')

async def calendar_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, year, month, day = query.data.split(":")
    context.user_data['schedule_date'] = {'year': int(year), 'month': int(month), 'day': int(day)}
    keyboard = [[InlineKeyboardButton(f"{h:02d}", callback_data=f"cal_hour:{h}") for h in range(start, start + 6)] for start in range(0, 24, 6)]
    keyboard.append([InlineKeyboardButton("⬅️ Volver al Calendario", callback_data=f"schedule_start:{context.user_data['pack_to_schedule']}")])
    await query.edit_message_text(f"Fecha seleccionada: {day}/{month}/{year}.\n\nAhora, selecciona la hora:", reply_markup=InlineKeyboardMarkup(keyboard))

async def time_hour_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, hour = query.data.split(":")
    context.user_data['schedule_date']['hour'] = int(hour)
    keyboard = [[InlineKeyboardButton(f"{m:02d}", callback_data=f"cal_min:{m}") for m in [0, 15, 30, 45]]]
    date_info = context.user_data['schedule_date']
    keyboard.append([InlineKeyboardButton("⬅️ Volver a Horas", callback_data=f"cal_day:{date_info['year']}:{date_info['month']}:{date_info['day']}")])
    await query.edit_message_text(f"Hora seleccionada: {hour}:XX. \n\nAhora, selecciona los minutos:", reply_markup=InlineKeyboardMarkup(keyboard))

async def time_minute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, minute = query.data.split(":")
    date_info = context.user_data['schedule_date']
    date_info['minute'] = int(minute)
    pack_name = context.user_data['pack_to_schedule']
    try:
        local_dt = TIMEZONE.localize(datetime(year=date_info['year'], month=date_info['month'], day=date_info['day'], hour=date_info['hour'], minute=date_info['minute']))
        if local_dt < datetime.now(TIMEZONE):
            await query.edit_message_text("❌ Esa fecha y hora ya han pasado. Por favor, empieza de nuevo.",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Reintentar", callback_data=f"schedule_start:{pack_name}")]]))
            return
        job_id = f"pack:{pack_name}:{local_dt.timestamp()}"
        job_kwargs = {'pack_name': pack_name, 'user_chat_id': update.effective_chat.id}
        scheduler.add_job(publish_pack_job, trigger='date', run_date=local_dt, id=job_id, name=f"Publish {pack_name}", kwargs=job_kwargs, replace_existing=True)
        await query.edit_message_text(f"✅ Pack *{pack_name}* programado para el *{local_dt.strftime('%d/%m/%Y a las %H:%M')}*.", parse_mode='Markdown')
    except Exception as e:
        await query.edit_message_text(f"❌ Error al programar la tarea: {e}")
    finally:
        context.user_data.clear()
        await query.message.reply_text("Volviendo al menú principal.", reply_markup=MAIN_KEYBOARD)

async def calendar_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, pack_name = query.data.split(":", 1)
    context.user_data.clear()
    await query.edit_message_text("Programación cancelada.")
    await select_pack_callback(update, context)

# --- FUNCIÓN PRINCIPAL Y ARRANQUE ---
def main() -> None:
    try:
        db.setup_database()
        scheduler.start()
        logger.info("Base de datos y Scheduler iniciados correctamente.")
    except Exception as e:
        logger.critical(f"FATAL: Error al iniciar: {e}")
        return

    async def shutdown_scheduler(app: Application):
        if scheduler.running: scheduler.shutdown()
            
    builder = Application.builder().token(BOT_TOKEN)
    builder.post_shutdown(shutdown_scheduler)
    application = builder.build()
    
    application.add_error_handler(error_handler)
    
    admin_filter = filters.User(user_id=ADMIN_USER_ID)

    # Handlers para el teclado principal y texto general
    application.add_handler(CommandHandler("start", start_command, filters=admin_filter))
    application.add_handler(MessageHandler(filters.TEXT & admin_filter, handle_text))
    
    # Handlers para menús inline
    application.add_handler(CallbackQueryHandler(list_packs_callback, pattern="^pack_list_"))
    application.add_handler(CallbackQueryHandler(main_menu_from_empty_callback, pattern="^main_menu_from_empty$"))
    application.add_handler(CallbackQueryHandler(select_pack_callback, pattern="^pack_select:"))
    application.add_handler(CallbackQueryHandler(send_pack_now_callback, pattern="^pack_send_now:"))
    application.add_handler(CallbackQueryHandler(delete_pack_confirm_callback, pattern="^pack_delete_confirm:"))
    application.add_handler(CallbackQueryHandler(delete_pack_do_callback, pattern="^pack_delete_do:"))
    application.add_handler(CallbackQueryHandler(edit_pack_start, pattern="^edit_pack_start:"))
    application.add_handler(CallbackQueryHandler(manage_photo_callback, pattern="^photo_manage:"))
    application.add_handler(CallbackQueryHandler(delete_photo_callback, pattern="^photo_delete:"))
    application.add_handler(CallbackQueryHandler(video_add_start_callback, pattern="^video_add_start:"))
    application.add_handler(CallbackQueryHandler(video_add_done_callback, pattern="^video_add_done:"))
    application.add_handler(CallbackQueryHandler(subtitle_add_start_callback, pattern="^subtitle_add_start:"))
    application.add_handler(CallbackQueryHandler(cancel_subtitle_add_callback, pattern="^cancel_subtitle_add:"))
    application.add_handler(CallbackQueryHandler(subtitle_search_start_callback, pattern="^subtitle_search_start:"))
    application.add_handler(CallbackQueryHandler(subtitle_download_independent_callback, pattern="^sub_download_independent:"))
    application.add_handler(CallbackQueryHandler(subtitle_download_pack_callback, pattern="^sub_download_pack:"))
    application.add_handler(CallbackQueryHandler(cancel_subtitle_search_callback, pattern="^cancel_subtitle_search:"))
    application.add_handler(CallbackQueryHandler(photo_add_start_callback, pattern="^photo_add_start:"))

    # Handlers para el calendario
    application.add_handler(CallbackQueryHandler(schedule_pack_start, pattern="^schedule_start:"))
    application.add_handler(CallbackQueryHandler(calendar_nav_callback, pattern="^cal_nav:"))
    application.add_handler(CallbackQueryHandler(calendar_day_callback, pattern="^cal_day:"))
    application.add_handler(CallbackQueryHandler(time_hour_callback, pattern="^cal_hour:"))
    application.add_handler(CallbackQueryHandler(time_minute_callback, pattern="^cal_min:"))
    application.add_handler(CallbackQueryHandler(calendar_cancel_callback, pattern="^cal_cancel:"))
    
    application.add_handler(CallbackQueryHandler(noop_callback, pattern="^noop$"))

    # Handlers genéricos para media
    application.add_handler(MessageHandler(filters.PHOTO & admin_filter, handle_photo), group=1)
    application.add_handler(MessageHandler(filters.VIDEO & admin_filter, handle_video), group=1)
    application.add_handler(MessageHandler(filters.Document.ALL & admin_filter, handle_document), group=1)
    
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}"
        logger.info(f"Iniciando bot con webhook en Render. URL: {webhook_url}")
        application.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=webhook_url)
    else:
        logger.info("Iniciando bot con polling para desarrollo local.")
        application.run_polling()

if __name__ == "__main__":
    main()