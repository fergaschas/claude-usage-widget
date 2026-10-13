# Claude Usage

[English](README.md) · **Español**

[![Última versión](https://img.shields.io/github/v/release/fergaschas/claude-usage-widget?label=release)](https://github.com/fergaschas/claude-usage-widget/releases/latest)
[![Descargas](https://img.shields.io/github/downloads/fergaschas/claude-usage-widget/total)](https://github.com/fergaschas/claude-usage-widget/releases)
[![Licencia: MIT](https://img.shields.io/github/license/fergaschas/claude-usage-widget)](LICENSE)
![Plataforma: Windows](https://img.shields.io/badge/platform-Windows-0078D6)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)

Widget de escritorio para Windows que muestra **cuánto has usado de tu plan de Claude** (sesión de 5 h y límite semanal) y cuánto te queda, siempre a la vista.

> Proyecto no oficial, sin relación con Anthropic.

**Versión actual: 2.5.2** · [Descargar `ClaudeUsage-Setup.exe`](../../releases/latest/download/ClaudeUsage-Setup.exe) · [Novedades](#historial-de-versiones)

<p>
  <img src="docs/widget-large.png" alt="Estilo Grande" height="260">
  <img src="docs/widget-rings.png" alt="Estilo Anillos" height="190">
</p>
<img src="docs/widget-compact.png" alt="Estilo Compacto" height="80">

## Funciones

- **Porcentaje de uso real** de cada límite de tu plan (sesión de 5 h, semanal y, si aplica, Opus/Sonnet), con cuenta atrás hasta el reinicio.
- **Previsión de ritmo**: calcula a qué velocidad consumes y avisa si vas a llegar al límite antes del reinicio. Una línea blanca en cada barra marca el tiempo transcurrido de la ventana.
- **Widget flotante** sin bordes y siempre encima, en tres estilos (Grande, Anillos, Compacto) y con opacidad ajustable. Se arrastra a cualquier sitio y recuerda su posición.
- **Bandeja del sistema** con los porcentajes en el tooltip, y atajo global **Ctrl+Alt+U** para mostrar u ocultar el widget.
- **Notificaciones de Windows** al llegar al 75 %, al 90 % y al 100 %, y cuando un límite se reinicia.
- **Ventana completa** (doble clic en el widget) con un gráfico del historial de 24 h o 7 días y los tokens de Claude Code por día, modelo y proyecto.
- **Exportación del historial a CSV**, arranque con Windows y un instalador que no necesita permisos de administrador.
- **Español e inglés**: usa el idioma de Windows y se puede cambiar en cualquier momento desde el menú (*Idioma / Language*).
- **Actualizaciones desde la app**: comprueba en GitHub si hay versiones nuevas y muestra *Nueva versión X disponible · Actualizar*. Con un clic descarga el instalador, verifica su suma SHA-256 y reinicia la app ya actualizada, conservando el historial, los ajustes, los accesos directos y el inicio con Windows. También puedes usar *Buscar actualizaciones* en el menú.

## Instalación

1. Descarga **[`ClaudeUsage-Setup.exe`](../../releases/latest/download/ClaudeUsage-Setup.exe)** (última versión) o elige una en [Releases](../../releases).
2. Ejecútalo y pulsa **Instalar**. Si aparece SmartScreen, elige *Más información → Ejecutar de todas formas*; sale porque el ejecutable no está firmado.
3. La primera vez que la abras, un asistente conecta la app con tu cuenta de Claude: comprueba que [Claude Code](https://claude.com/claude-code) está instalado (y puede instalarlo por ti), abre el navegador para que inicies sesión en Claude y confirma que puede leer tu uso. Todo se actualiza solo a medida que avanzas.

Puedes volver a abrir el asistente cuando quieras desde el menú (*Cuenta de Claude…*) para ver qué cuenta está conectada o cambiarla. Necesitas una suscripción de Claude (Pro, Max, Team…), porque los límites de uso solo existen en las suscripciones.

**¿Ya la tienes instalada?** Desde la versión 2.5, la app se actualiza sola desde dentro. Si tienes la 2.4 o anterior, ejecuta el nuevo instalador una vez: detecta tu instalación, ofrece *Actualizar* y conserva el historial y los ajustes.

Para desinstalarla: *Configuración → Aplicaciones → Aplicaciones instaladas → Claude Usage*.

## Cómo funciona

- **Límites del plan:** la app lee el token OAuth que Claude Code guarda en `%USERPROFILE%\.claude\.credentials.json` y consulta el mismo endpoint que usa el comando `/usage` (`api.anthropic.com/api/oauth/usage`). El token solo se envía a Anthropic. El endpoint no está documentado y podría cambiar.
- **Tokens:** se calculan leyendo los registros locales de Claude Code (`%USERPROFILE%\.claude\projects\**\*.jsonl`). Solo cuentan el uso de Claude Code en ese PC, no el chat de claude.ai.
- **Datos locales:** el historial y los ajustes se guardan en la carpeta `data\`, junto al ejecutable.

No usa dependencias externas: solo la librería estándar de Python (tkinter, ctypes y winreg).

## Ejecutar desde el código

```bash
pythonw ClaudeUsage.pyw
```

## Compilar los ejecutables

Necesitas Python 3.10 o superior. El script crea un entorno temporal con PyInstaller y deja `ClaudeUsage.exe` y `ClaudeUsage-Setup.exe` en `dist\`:

```bash
powershell -ExecutionPolicy Bypass -File build.ps1
```

| Archivo | Qué es |
|---|---|
| `ClaudeUsage.pyw` | La aplicación (widget, bandeja, notificaciones, desinstalación con `--uninstall`) |
| `setup_installer.py` | El instalador; admite instalación silenciosa con `--silent [--dir RUTA] [--no-desktop] [--no-autostart] [--no-launch]`, `--update` (lo usa el actualizador de la app; conserva el acceso directo y el inicio con Windows actuales) y `--lang es\|en` para forzar su idioma |
| `build.ps1` | Compila ambos ejecutables |
| `packaging/` | Metadatos de versión de los `.exe` |

### Publicar una versión nueva

El actualizador de la app lee la última Release de GitHub, así que cada versión debe seguir este formato:

1. Sube la versión en `APP_VERSION` (`ClaudeUsage.pyw`), `VERSION` (`setup_installer.py`) y los dos archivos de `packaging/`.
2. Compila con `build.ps1`.
3. Crea una Release en GitHub con la etiqueta **`vX.Y.Z`** (por ejemplo `v2.5.1`) y adjunta **`dist\ClaudeUsage-Setup.exe`** con ese nombre exacto. GitHub publica su suma SHA-256, que la app verifica antes de instalar.

## Historial de versiones

| Versión | Cambios |
|---|---|
| **2.5.2** | Arreglo para PCs que se quedaban atascados en *Anthropic limita las consultas*: el asistente de conexión ya no vuelve a consultar cada 10 segundos, las esperas crecen (5, 10, 20, 30 min) y nada se las salta salvo el botón de actualizar. El widget muestra la hora del reintento. Nuevo registro (*Abrir registro* en el menú), incluido también en *Copiar diagnóstico*. |
| **2.5.1** | Correcciones de las actualizaciones desde la app: `v2.5` y `v2.5.0` cuentan como la misma versión, y la app ya no se queda en *Instalando la actualización…* si el instalador no llega a arrancar. |
| **2.5** | **Actualizaciones desde la app** con verificación SHA-256. Mejor detección de Claude Code: la copia incluida en la app de escritorio de Claude, nvm, scoop, bun, volta, WSL y `CLAUDE_CONFIG_DIR`. Nuevo botón *Copiar diagnóstico*. |
| **2.4** | **Asistente de conexión** en el primer uso: instala Claude Code si hace falta, inicia sesión en Claude y comprueba el uso. Aviso *Sin conexión con Claude · Conectar* en el widget. |
| **2.3** | Interfaz en **español e inglés**, seleccionable desde el menú. |
| **2.2** | Primera versión pública. |

## Enlaces

- **Web:** [última versión](https://github.com/fergaschas/claude-usage-widget/releases/latest), con el instalador y las novedades.
- **Temas:** [claude](https://github.com/topics/claude) · [claude-code](https://github.com/topics/claude-code) · [anthropic](https://github.com/topics/anthropic) · [windows](https://github.com/topics/windows) · [widget](https://github.com/topics/widget) · [desktop-widget](https://github.com/topics/desktop-widget) · [usage-tracker](https://github.com/topics/usage-tracker) · [rate-limits](https://github.com/topics/rate-limits) · [system-tray](https://github.com/topics/system-tray) · [python](https://github.com/topics/python) · [tkinter](https://github.com/topics/tkinter)

## Licencia

[MIT](LICENSE)
