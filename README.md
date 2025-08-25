# Bot de Gestión de Packs para Telegram (con MongoDB y listo para Render)

Este bot de Telegram permite gestionar contenido para un canal de dos maneras: publicando al instante o creando "packs" de contenido (fotos + videos) para ser publicados bajo demanda. Utiliza MongoDB Atlas para una persistencia de datos robusta.

## ✨ Características

-   Doble Modo de Operación: Modo Inmediato y Modo Pack.
-   Gestión de Packs: /new_pack, /done, /cancel, /list_packs, /send_pack, /delete_pack.
-   Persistencia en MongoDB: Tus packs se guardan de forma segura en una base de datos en la nube y no se pierden.
-   Reemplazo de Menciones: Sustituye @usuarios y enlaces t.me por @estrenos_fh.

## 🚀 Despliegue (MongoDB Atlas + Render)

Sigue estos 3 grandes pasos para tener tu bot funcionando.

### Paso 1: Configurar la Base de Datos en MongoDB Atlas

Necesitamos una base de datos en la nube. Usaremos el plan gratuito de MongoDB Atlas.

1.  Crea una cuenta: Ve a cloud.mongodb.com y regístrate.
2.  Crea un nuevo Proyecto: Dale un nombre a tu proyecto (ej. "BotTelegram").
3.  Crea una Base de Datos (Build a Database):
    -   Elige el plan M0 FREE. Es más que suficiente.
    -   Puedes dejar el proveedor de la nube y la región por defecto.
    -   En "Cluster Name", puedes dejar el nombre Cluster0 o cambiarlo.
    -   Haz clic en Create. El despliegue del cluster tardará unos minutos.
4.  Crea un Usuario para la Base de Datos:
    -   En el menú de la izquierda, ve a Database Access.
    -   Haz clic en Add New Database User.
    -   Elige "Password" como método de autenticación.
    -   Dale un nombre de usuario (ej. bot_user).
    -   IMPORTANTE: Genera una contraseña segura y guárdala en un lugar seguro. La necesitarás pronto.
    -   En "Database User Privileges", selecciona Read and write to any database.
    -   Haz clic en Add User.
5.  Permite el Acceso desde Cualquier IP:
    -   En el menú de la izquierda, ve a Network Access.
    -   Haz clic en Add IP Address.
    -   Selecciona ALLOW ACCESS FROM ANYWHERE. Esto generará la IP 0.0.0.0/0.
    -   Confirma la selección. Esto es necesario para que tu bot desde Render pueda conectarse.
6.  Obtén la Cadena de Conexión (URI):
    -   Vuelve a la vista de Database y haz clic en el botón Connect de tu cluster.
    -   Selecciona la opción Drivers.
    -   Asegúrate de que Python y la versión del driver estén seleccionados.
    -   Copia la cadena de conexión que te proporciona. Se verá así:
       
        mongodb+srv://bot_user:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
        
    -   Guarda esta cadena. Es la MONGO_URI.

### Paso 2: Preparar y Subir tu Código a GitHub

1.  Asegúrate de tener todos los archivos (bot.py, database.py, render.yaml, requirements.txt, etc.) en una carpeta local.
2.  Crea un nuevo repositorio en tu cuenta de GitHub.
3.  Sube todos los archivos a ese repositorio.

### Paso 3: Desplegar en Render

1.  Crea una Cuenta en Render: Ve a render.com y regístrate.
2.  Crea un Nuevo "Blueprint":
    -   En tu dashboard de Render, haz clic en New + y selecciona Blueprint.
    -   Conecta tu cuenta de GitHub y selecciona el repositorio de tu bot.
    -   Render leerá tu render.yaml y te mostrará el servicio web que va a crear. Dale un nombre al grupo y haz clic en Apply.
3.  Configura las Variables de Entorno (¡El paso más importante!):
    -   El primer despliegue fallará. Ve a la pestaña Environment de tu nuevo servicio.
    -   En la sección Environment Variables, haz clic en Add Environment Variable y añade una por una:
    -   -   BOT_TOKEN: El token de tu bot de Telegram.
        -   CHANNEL_ID: El ID de tu canal.
        -   ADMIN_USER_ID: Tu ID de usuario de Telegram.
        -   MONGO_URI: Aquí pegas la cadena de conexión de MongoDB Atlas. RECUERDA reemplazar <password> con la contraseña real que guardaste en el Paso 1.
4.  Redespliega el Servicio:
    -   Ve a la pestaña Events de tu servicio y haz clic en Deploy latest commit.
    -   Ahora Render reconstruirá tu aplicación con todas las variables de entorno correctas.
5.  Verifica:
    -   Ve a la pestaña Logs. Espera a que aparezca el mensaje Conexión a MongoDB establecida correctamente y Iniciando bot....
    -   Habla con tu bot en Telegram. ¡Debería estar funcionando!
