# Claude Usage

[English](README.md) · **Español**

Widget de escritorio para Windows que muestra **cuánto has usado de tu plan de Claude** (sesión de 5 h y límite semanal) y cuánto te queda, siempre a la vista.

> Proyecto no oficial, sin relación con Anthropic.

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

## Instalación

1. Descarga **`ClaudeUsage-Setup.exe`** desde [Releases](../../releases/latest).
2. Ejecútalo y pulsa **Instalar**. Si aparece SmartScreen, elige *Más información → Ejecutar de todas formas*; sale porque el ejecutable no está firmado.
3. La app aparece en el menú Inicio como **Claude Usage**.

**Requisito:** [Claude Code](https://claude.com/claude-code) instalado y con sesión iniciada en el PC (ejecuta `claude` una vez).

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
| `setup_installer.py` | El instalador; admite instalación silenciosa con `--silent [--dir RUTA] [--no-desktop] [--no-autostart] [--no-launch]` |
| `build.ps1` | Compila ambos ejecutables |
| `packaging/` | Metadatos de versión de los `.exe` |

## Licencia

[MIT](LICENSE)
