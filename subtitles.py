# subtitles.py
import os
import requests
import logging

API_KEY = os.getenv("OPENSUBTITLES_API_KEY")
API_URL = "https://api.opensubtitles.com/api/v1"

auth_token = None

APP_NAME_FOR_API = "FHFManagerBot v1.0"

COMMON_HEADERS = {
    'Api-Key': API_KEY,
    'Content-Type': 'application/json',
    'User-Agent': APP_NAME_FOR_API
}

def get_auth_token():
    global auth_token
    if auth_token:
        return auth_token

    OPENSUBTITLES_USERNAME = "Feft05"
    OPENSUBTITLES_PASSWORD = "Cuba230405?"

    if not API_KEY:
        logging.error("OPENSUBTITLES_API_KEY no está configurada.")
        return None

    headers = COMMON_HEADERS.copy()

    if OPENSUBTITLES_USERNAME != "TU_USUARIO_DE_OPENSUBTITLES" and OPENSUBTITLES_PASSWORD != "TU_CONTRASEÑA_DE_OPENSUBTITLES":
        payload = { "username": OPENSUBTITLES_USERNAME, "password": OPENSUBTITLES_PASSWORD }
        try:
            logging.info("Intentando obtener token de OpenSubtitles con usuario y contraseña...")
            response = requests.post(f"{API_URL}/login", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get('token'):
                auth_token = data['token']
                logging.info("Token de OpenSubtitles obtenido correctamente (autenticado).")
                return auth_token
        except requests.exceptions.RequestException as e:
            logging.warning(f"Fallo en login con usuario/pass (puede ser normal si no se configuran): {e}")

    logging.info("Intentando obtener token de OpenSubtitles solo con API Key (anónimo)...")
    try:
        response = requests.post(f"{API_URL}/login", headers=headers, json={})
        response.raise_for_status()
        data = response.json()
        if data.get('token'):
            auth_token = data['token']
            logging.info("Token de OpenSubtitles obtenido correctamente (anónimo).")
            return auth_token
        else:
            logging.error(f"Error CRÍTICO al obtener token anónimo. Respuesta: {data}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error de red CRÍTICO al intentar obtener token anónimo: {e}")
        return None

# <<< CORRECCIÓN AQUÍ >>>
def search_subtitles(query: str, language_code: str = 'es'):
    """Busca subtítulos por nombre y idioma, garantizando siempre devolver una tupla."""
    token = get_auth_token()
    # Si get_auth_token() falla, devuelve None. Ahora lo manejamos correctamente.
    if not token:
        return None, "Error de autenticación con la API de subtítulos. Revisa las credenciales en subtitles.py y la API Key en Render."

    headers = COMMON_HEADERS.copy()
    headers['Authorization'] = f'Bearer {token}'
    params = {'query': query, 'languages': language_code}
    
    try:
        response = requests.get(f"{API_URL}/subtitles", headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        
        if not data.get('data'):
            return [], "No se encontraron subtítulos para esa búsqueda."

        subtitles = []
        for sub_data in data['data']:
            attrs = sub_data.get('attributes', {})
            files = attrs.get('files', [])
            if files:
                subtitles.append({
                    'id': sub_data.get('id'),
                    'language': attrs.get('language'),
                    'movie_name': attrs.get('feature_details', {}).get('movie_name'),
                    'season': attrs.get('feature_details', {}).get('season_number'),
                    'episode': attrs.get('feature_details', {}).get('episode_number'),
                    'file_id': files[0].get('file_id')
                })
        return subtitles, None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error de red al buscar subtítulos: {e}")
        return None, "Error de red al buscar subtítulos."

def request_download_link(file_id: int):
    token = get_auth_token()
    if not token:
        return None, "Error de autenticación con la API de subtítulos."
    headers = COMMON_HEADERS.copy()
    headers['Authorization'] = f'Bearer {token}'
    payload = {'file_id': file_id}
    try:
        response = requests.post(f"{API_URL}/download", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get('link'):
            return data['link'], None
        else:
            if "download count" in data.get('message', '').lower():
                 return None, f"Límite de descargas alcanzado. Mensaje de la API: {data.get('message')}"
            return None, f"No se pudo obtener el enlace de descarga. Respuesta: {data}"
    except requests.exceptions.RequestException as e:
        logging.error(f"Error de red al solicitar descarga: {e}")
        return None, "Error de red al solicitar el enlace de descarga."

def download_subtitle_content(download_link: str):
    headers = {'User-Agent': APP_NAME_FOR_API}
    try:
        response = requests.get(download_link, headers=headers)
        response.raise_for_status()
        return response.content, None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error al descargar el contenido del subtítulo: {e}")
        return None, "No se pudo descargar el archivo de subtítulos."
