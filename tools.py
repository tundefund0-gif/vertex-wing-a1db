from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are TermuxAgent — AI assistant with Android device access via Termux-MCP (~105+ tools).

## CRITICAL RULES
1. **TASK MEMORY**: Complete ALL steps. User says "create & start web app" → do BOTH.
2. **PRONOUNS**: "start it" = start the most recently mentioned project. NOT a random directory.
3. **ADAPTIVE STYLE**: Match your response to the context:
   - **Casual / chatty** ("yo", "hey", "what's up") → be playful, use relaxed tone, emojis OK
   - **Simple requests** ("list files", "what's my IP") → be concise, 1-3 lines, just the facts
   - **Complex tasks** ("create a web app", "explain how this works") → be thorough, explain steps, show output details, write long text
   - **Troubleshooting** ("it's broken", "error occurred") → be professional, systematic, diagnose step by step
   - **Professional** ("generate a report", "analyze this") → be formal, structured, precise
   - Default to concise unless the request clearly needs depth.
   - NEVER greet mid-session. NEVER repeat what the user said back to them.
4. **TOOL DISCIPLINE**: Use the EXACT dedicated tool. NEVER `run_command` when a dedicated tool exists.

## TOOL SELECTION (pick by user intent)

**Files**: read→`read_file`  write→`write_file`  list→`list_directory`  search→`search_files`
  delete→`delete`  diff→`diff`  patch→`patch`  mkdir→`run_command("mkdir -p")`

**Execute code**: general→`run_command`  Flask/Node/custom server→`run_command with nohup`  static file server→`web_server`
  install known→`smart_install`  discover→`smart_pkg`

**System**: quick stats→`get_system_info`  full health→`health_diagnostic`  processes→`process_list`
  kill→`process_kill`  env→`get_environment`

**Hardware**: battery→`get_battery`  GPS→`get_location`  WiFi→`get_wifi_info/scan_wifi`
  photo→`camera_photo`  cameras→`camera_info`  screenshot→`take_screenshot`  screen record→`screen_record`
  sensor→`read_sensor`  fingerprint→`fingerprint_auth`  vibrate→`vibrate`  torch→`torch`
  brightness→`brightness`  volume→`volume`  IR→`infrared_transmit`  hotspot→`wifi_hotspot`

**Communication**: notify→`send_notification`  SMS→`send_sms/read_sms_inbox`  TTS→`tts_speak`
  speech→`speech_to_text`  toast→`show_toast`  dialog→`show_dialog`  share→`share`
  clipboard→`clipboard_get/set`  call→`make_phone_call`  contacts→`list_contacts`  apps→`list_installed_apps`

**Network**: browser→`open_url`  download→`download_file`  IP→`get_public_ip`
  weather→`get_weather`  speed→`speedtest`  hotspot→`wifi_hotspot`

**Media**: image→`image_process`  video→`video_process`  OCR→`extract_text`
  QR gen→`generate_qrcode`  scan barcode→`scan_barcode`  mic→`microphone_record`
  wallpaper→`set_wallpaper`  media control→`media_player`  ringtone→`set_ringtone`

**Git**: standard→`git_operation`  AI→`smart_git`  PR→`git_pr`

**Schedule**: cron→`cron_add/list/remove`  recipe→`recipe_save/run/list`  context→`context_set/get`

**Backup**: local→`backup/restore`  cloud→`cloud_sync`  migrate→`migrate_environment`

**SSH/Services**: setup→`ssh_wizard`  services→`service_guard`  ports→`port_manage`

**Dev**: env setup→`setup_dev_env`  profile→`load_profile`  review→`review_code`
  script→`generate_script`  regex→`test_regex`  db→`create_database/query_database`
  translate→`translate_text`  tutorial→`tutorial`

**Fix/Diagnose**: diagnose→`run_diagnostics`  explain error→`explain_error`  log→`analyze_log`
  permissions→`fix_permissions`  config→`fix_config`  storage→`audit_storage`
  deps→`dependency_tree`  optimize→`optimize_device`  disk→`disk_usage`

**Shell**: explain cmd→`explain_command`  history insight→`history_insight`  aliases→`quick_cmd`
  history→`history_list/save/clear`

**Android specific**: app info→`app_info`  app launch→`launch_app`  app uninstall→`uninstall_app`
  shortcut→`add_homescreen_shortcut`  hotspot→`wifi_hotspot`

**Other**: ping→`ping_server`  cancel→`cancel_command`  telephony→`get_telephony_info`
  cell→`get_cell_info`  pick file→`storage_get`  notify remove→`remove_notification`

## MANDATORY
- `run_command` = ONLY when no dedicated tool matches. Never for reading files.
- `web_server` = ONLY for `python3 -m http.server`. Never for Flask/Django/Node.
- Batch shell ops: `mkdir -p && cat > file && python3 -m venv && pip install` in single `run_command`.
- Warn before destructive ops (delete, force-kill).
- Tool failed? → diagnose with `run_command`, retry once.
- Persistent `cd` state between commands.
- Android: use `pkg`, `termux-*` APIs.
- [Auto-continue] = keep going, do NOT repeat completed steps.
"""


TOOL_DEFS = []

def tool(name: str, endpoint: str, description: str,
         properties: dict | None = None, required: list[str] | None = None,
         method: str = "POST", category: str = "other") -> None:
    TOOL_DEFS.append({
        "name": name,
        "endpoint": endpoint,
        "method": method,
        "description": description,
        "properties": properties or {},
        "required": required or [],
        "category": category,
    })


# ── Shell & Filesystem ───────────────────────────────────────────────────────

tool("run_command", "/run",
     "Execute any shell command on the Android device via Termux. Use this for: running scripts/executables, starting long-running servers (use nohup ... &), compiling code, installing packages, piping commands, chaining operations with &&. Maintains persistent cd state. This is your GENERAL-PURPOSE tool for anything not covered by a dedicated tool.",
     {"cmd": {"type": "string", "description": "Shell command to execute on the Android device"}},
     ["cmd"], category="shell")

tool("list_directory", "/ls",
     "List contents of a directory on the Android device's filesystem. Use this to explore the device's directories. NOTE: Only use this when the user explicitly asks to see what's in a directory on the Android device, NOT when they want to start a server or run code.",
     {"path": {"type": "string", "description": "Directory path on the Android device", "default": "."},
      "detailed": {"type": "boolean", "description": "Show detailed listing with sizes and permissions", "default": False},
      "bare": {"type": "boolean", "description": "Show bare listing (names only)", "default": False},
      "no_dotfiles": {"type": "boolean", "description": "Hide dotfiles", "default": False}},
     category="files")

tool("read_file", "/read",
     "Read a file from the Android device filesystem. Use this for viewing file CONTENTS. Do NOT use run_command for this.",
     {"path": {"type": "string", "description": "File path to read"}},
     ["path"], category="files")

tool("write_file", "/write",
     "Write content to a file on the Android device. Automatically creates parent directories. For multi-file projects, consider using run_command with cat << 'EOF' to save iterations.",
     {"path": {"type": "string", "description": "File path"},
      "content": {"type": "string", "description": "Content to write"}},
     ["path", "content"], category="files")

tool("mkdir", "/mkdir",
     "Create a single directory (mkdir -p). Prefer run_command for creating multiple directories at once or when bashing with other setup steps.",
     {"path": {"type": "string", "description": "Directory path to create"}},
     ["path"], category="files")

tool("delete", "/delete",
     "Delete a file or directory. Requires explicit confirmation for recursive deletes.",
     {"path": {"type": "string", "description": "Path to delete"},
      "recursive": {"type": "boolean", "description": "Delete recursively", "default": False},
      "confirmed": {"type": "boolean", "description": "Must be true to proceed with deletion", "default": False}},
     ["path", "confirmed"], category="files")

tool("search_files", "/search",
     "Find files by name pattern. Glob patterns supported (e.g. '*.txt', '**/*.py').",
     {"path": {"type": "string", "description": "Directory to search in", "default": "."},
      "pattern": {"type": "string", "description": "File name pattern (e.g., '*.txt', 'config*')", "default": "*"}},
     category="files")

tool("cancel_command", "/cancel",
     "Cancel the currently running command on the device.",
     {}, category="shell")

tool("disk_usage", "/disk-usage",
     "Check disk usage: free space, storage stats, and large directories.",
     {"path": {"type": "string", "description": "Path to check (default: home)", "default": "~"}},
     category="system")


# ── System Monitor & Management ──────────────────────────────────────────────

tool("get_system_info", "/system-info",
     "Get QUICK live system stats: CPU%, RAM, disk, temperature, uptime as JSON. For a comprehensive health check (packages, API, storage, network), use health_diagnostic instead.",
     {}, category="system")

tool("process_list", "/process-list",
     "List running processes sorted by CPU usage.",
     {"limit": {"type": "integer", "description": "Max processes to show", "default": 20}},
     category="system")

tool("process_kill", "/process-kill",
     "Terminate a process by PID.",
     {"pid": {"type": "integer", "description": "Process ID to kill"},
      "signal": {"type": "integer", "description": "Signal number (default 15 = SIGTERM)", "default": 15}},
     ["pid"], category="system")

tool("health_diagnostic", "/health",
     "Run a FULL device health check: verifies core packages, Termux:API, storage access, network connectivity, permissions. Use this when something feels broken. For quick stats (CPU/RAM only), use get_system_info.",
     {}, category="system")


# ── Cron Scheduler ───────────────────────────────────────────────────────────

tool("cron_add", "/cron-add",
     "Add a cron job. Schedule format: '0 3 * * *' for daily at 3am.",
     {"schedule": {"type": "string", "description": "Cron schedule (5 fields)"},
      "command": {"type": "string", "description": "Command to run"},
      "label": {"type": "string", "description": "Label for the job", "default": "task"}},
     ["schedule", "command"], category="schedule")

tool("cron_list", "/cron-list",
     "List all cron jobs.",
     {}, category="schedule")

tool("cron_remove", "/cron-remove",
     "Remove cron jobs matching a label, or all if no label given.",
     {"label": {"type": "string", "description": "Label to match for removal"}},
     category="schedule")


# ── Backup, Restore & Cloud Sync ─────────────────────────────────────────────

tool("backup", "/backup",
     "Create a tar.gz backup of home directory, packages, or configs.",
     {"target": {"type": "string", "enum": ["home", "packages", "configs"], "description": "What to backup", "default": "home"},
      "output": {"type": "string", "description": "Output file path"}},
     category="backup")

tool("restore", "/restore",
     "Restore from a backup file.",
     {"file": {"type": "string", "description": "Backup file path"},
      "target": {"type": "string", "enum": ["home", "packages", "configs"], "description": "What to restore", "default": "home"}},
     ["file"], category="backup")

tool("cloud_sync", "/cloud-sync",
     "Create or restore cloud backups with rclone-style instructions.",
     {"action": {"type": "string", "enum": ["backup", "restore", "list"], "description": "Action to perform"},
      "target": {"type": "string", "enum": ["home", "packages", "configs"], "description": "What to target", "default": "home"},
      "output": {"type": "string", "description": "Output file path for backup"},
      "file": {"type": "string", "description": "File to restore from"}},
     ["action"], category="backup")


# ── Code & Files ─────────────────────────────────────────────────────────────

tool("diff", "/diff",
     "Show diff between two files, or file info/stats for a single file.",
     {"file": {"type": "string", "description": "Primary file path"},
      "file2": {"type": "string", "description": "Second file to diff against"}},
     ["file"], category="files")

tool("patch", "/patch",
     "Apply a diff patch to a file.",
     {"file": {"type": "string", "description": "File to patch"},
      "patch": {"type": "string", "description": "Patch content/diff to apply"}},
     ["file", "patch"], category="files")


# ── Device & Sensors ─────────────────────────────────────────────────────────

tool("get_battery", "/battery",
     "Get battery status: percentage, health, temperature, charging state via termux-battery-status.",
     {}, category="device")

tool("get_location", "/location",
     "Get GPS or network location: coordinates, altitude, accuracy, speed, bearing.",
     {"provider": {"type": "string", "enum": ["gps", "network"], "description": "Location provider", "default": "gps"}},
     category="device")

tool("get_wifi_info", "/wifi-info",
     "Get current WiFi connection details: SSID, BSSID, signal strength, IP, speed.",
     {}, category="network")

tool("scan_wifi", "/wifi-scan",
     "Scan nearby WiFi networks and list them.",
     {}, category="network")

tool("wifi_hotspot", "/wifi-hotspot",
     "Start or stop a WiFi hotspot on the device.",
     {"action": {"type": "string", "enum": ["start", "stop", "status"], "description": "Hotspot action"},
      "ssid": {"type": "string", "description": "SSID for the hotspot (for start)"},
      "password": {"type": "string", "description": "Password for the hotspot (for start, min 8 chars)"},
      "frequency": {"type": "integer", "enum": [0, 1, 2], "description": "0=auto, 1=2.4GHz, 2=5GHz", "default": 0}},
     ["action"], category="network")

tool("camera_photo", "/camera-photo",
     "Take a photo with the device camera.",
     {"camera_id": {"type": "integer", "description": "Camera ID (0=back, 1=front)", "default": 0},
      "output": {"type": "string", "description": "Output file path", "default": "photo.jpg"}},
     category="device")

tool("camera_info", "/camera-info",
     "List available camera information.",
     {}, category="device")

tool("take_screenshot", "/screenshot",
     "Take a screenshot of the device screen.",
     {"output": {"type": "string", "description": "Output file path", "default": "screenshot.png"}},
     category="device")

tool("read_sensor", "/sensor",
     "Read sensor data (accelerometer, gyroscope, magnetometer, etc.) or list available sensors.",
     {"sensor": {"type": "string", "description": "Sensor name (omit to list available sensors)"},
      "limit": {"type": "integer", "description": "Number of readings", "default": 1}},
     category="device")

tool("fingerprint_auth", "/fingerprint",
     "Authenticate using device fingerprint sensor.",
     {}, category="device")

tool("vibrate", "/vibrate",
     "Vibrate the device for a specified duration.",
     {"duration_ms": {"type": "integer", "description": "Vibration duration in milliseconds", "default": 500}},
     category="device")

tool("torch", "/torch",
     "Turn the camera flashlight/torch on or off.",
     {"state": {"type": "string", "enum": ["on", "off"], "description": "Torch state"}},
     ["state"], category="device")

tool("brightness", "/brightness",
     "Get or set screen brightness level (0-255).",
     {"level": {"type": "integer", "description": "Brightness level 0-255 (omit to query current)"}},
     category="device")

tool("volume", "/volume",
     "Get or set volume for a stream (music, notification, alarm, call, system).",
     {"stream": {"type": "string", "description": "Audio stream", "default": "music"},
      "level": {"type": "integer", "description": "Volume level (omit to query)"}},
     category="device")


# ── Communication ────────────────────────────────────────────────────────────

tool("send_notification", "/notify",
     "Send an Android system notification.",
     {"title": {"type": "string", "description": "Notification title", "default": "TermuxGPT"},
      "content": {"type": "string", "description": "Notification body text"},
      "priority": {"type": "string", "enum": ["default", "high", "low", "max", "min"], "description": "Notification priority", "default": "default"},
      "id": {"type": "string", "description": "Notification ID for updates/removal"},
      "alert_once": {"type": "boolean", "description": "Alert only once (no sound on update)", "default": False}},
     ["content"], category="communication")

tool("remove_notification", "/notify-remove",
     "Remove a notification by ID.",
     {"id": {"type": "string", "description": "Notification ID to remove"}},
     ["id"], category="communication")

tool("send_sms", "/sms-send",
     "Send an SMS message to a phone number.",
     {"number": {"type": "string", "description": "Phone number"},
      "text": {"type": "string", "description": "Message text"}},
     ["number", "text"], category="communication")

tool("read_sms_inbox", "/sms-inbox",
     "Read SMS inbox messages.",
     {"limit": {"type": "integer", "description": "Max messages to return", "default": 10},
      "unread_only": {"type": "boolean", "description": "Only show unread messages", "default": False}},
     category="communication")

tool("tts_speak", "/tts-speak",
     "Convert text to speech and play it through the device speaker.",
     {"text": {"type": "string", "description": "Text to speak aloud"},
      "rate": {"type": "number", "description": "Speech rate", "default": 1.0},
      "pitch": {"type": "number", "description": "Speech pitch", "default": 1.0}},
     ["text"], category="communication")

tool("speech_to_text", "/speech-to-text",
     "Listen for speech and convert to text (requires mic permission).",
     {"language": {"type": "string", "description": "Language code (e.g., 'en', 'es')", "default": "en"}},
     category="communication")

tool("show_toast", "/toast",
     "Show a temporary Android toast message.",
     {"text": {"type": "string", "description": "Toast message text"},
      "short_duration": {"type": "boolean", "description": "Short (true) or long (false) duration", "default": True}},
     ["text"], category="communication")

tool("show_dialog", "/dialog",
     "Show a confirmation dialog on the device screen.",
     {"title": {"type": "string", "description": "Dialog title", "default": "TermuxGPT"},
      "message": {"type": "string", "description": "Dialog message body"},
      "button1": {"type": "string", "description": "First button text", "default": "OK"},
      "button2": {"type": "string", "description": "Second button text", "default": "Cancel"}},
     ["message"], category="communication")

tool("share", "/share",
     "Share text or a file via Android share intent.",
     {"text": {"type": "string", "description": "Text content to share"},
      "file": {"type": "string", "description": "File path to share (alternative to text)"},
      "type": {"type": "string", "description": "MIME type hint (e.g., 'text/plain', 'image/png')"}},
     category="communication")

tool("clipboard_get", "/clipboard-get",
     "Read the current clipboard content.",
     {}, category="communication")

tool("clipboard_set", "/clipboard-set",
     "Set clipboard content.",
     {"text": {"type": "string", "description": "Text to set on clipboard"}},
     ["text"], category="communication")

tool("make_phone_call", "/call",
     "Initiate a phone call via telephony.",
     {"number": {"type": "string", "description": "Phone number to call"}},
     ["number"], category="communication")

tool("list_contacts", "/contacts",
     "List device contacts.",
     {"limit": {"type": "integer", "description": "Max contacts to return", "default": 50}},
     category="communication")

tool("list_installed_apps", "/list-apps",
     "List installed applications on the device.",
     {"filter": {"type": "string", "description": "Optional filter text to match package names"}},
     category="communication")


# ── App Management (new) ──────────────────────────────────────────────────────

tool("app_info", "/app-info",
     "Get detailed information about a specific installed app.",
     {"package": {"type": "string", "description": "Package name (e.g., 'com.termux')"}},
     ["package"], category="communication")

tool("launch_app", "/launch-app",
     "Launch an installed app by package name.",
     {"package": {"type": "string", "description": "Package name to launch"}},
     ["package"], category="communication")

tool("uninstall_app", "/uninstall-app",
     "Uninstall an app (requires Termux:API permissions).",
     {"package": {"type": "string", "description": "Package name to uninstall"},
      "confirmed": {"type": "boolean", "description": "Must be true to proceed", "default": False}},
     ["package", "confirmed"], category="communication")

tool("add_homescreen_shortcut", "/add-shortcut",
     "Add a shortcut to the Android homescreen.",
     {"name": {"type": "string", "description": "Shortcut name"},
      "command": {"type": "string", "description": "Termux command to run when tapped"}},
     ["name", "command"], category="communication")


# ── Network ──────────────────────────────────────────────────────────────────

tool("open_url", "/open-url",
     "Open a URL in the device browser.",
     {"url": {"type": "string", "description": "URL to open"}},
     ["url"], category="network")

tool("download_file", "/download",
     "Download a file from a URL via Android download manager.",
     {"url": {"type": "string", "description": "Download URL"},
      "description": {"type": "string", "description": "File description"},
      "title": {"type": "string", "description": "Notification title"}},
     ["url"], category="network")

tool("get_public_ip", "/public-ip",
     "Get the device's public IP address.",
     {}, category="network")

tool("get_weather", "/weather",
     "Get current weather for a city.",
     {"city": {"type": "string", "description": "City name (omit for current location)"}},
     category="network")

tool("speedtest", "/speedtest",
     "Run an internet speed test (download/upload/ping).",
     {}, category="network")

tool("web_server", "/web-server",
     "Manage Python's built-in http.server (static file server). ONLY for python3 -m http.server. For Flask/FastAPI/Node/custom servers, use run_command with 'nohup ... &' to background it.",
     {"action": {"type": "string", "enum": ["start", "stop", "status"], "description": "Server action"},
      "port": {"type": "integer", "description": "Port number", "default": 8080},
      "directory": {"type": "string", "description": "Directory to serve"}},
     ["action"], category="network")


# ── Media Processing ─────────────────────────────────────────────────────────

tool("image_process", "/image-process",
     "Process images via ImageMagick: get info, resize, crop, or rotate.",
     {"action": {"type": "string", "enum": ["info", "resize", "crop", "rotate"], "description": "Operation"},
      "input": {"type": "string", "description": "Input image path"},
      "output": {"type": "string", "description": "Output image path"},
      "width": {"type": "integer", "description": "Width for resize/crop"},
      "height": {"type": "integer", "description": "Height for resize/crop"},
      "x": {"type": "integer", "description": "X offset for crop", "default": 0},
      "y": {"type": "integer", "description": "Y offset for crop", "default": 0},
      "degrees": {"type": "integer", "description": "Rotation degrees", "default": 90}},
     ["action", "input"], category="media")

tool("video_process", "/video-process",
     "Process videos via FFmpeg: get info, compress, extract audio, or trim.",
     {"action": {"type": "string", "enum": ["info", "compress", "extract-audio", "trim", "convert"], "description": "Operation"},
      "input": {"type": "string", "description": "Input video path"},
      "output": {"type": "string", "description": "Output file path"},
      "crf": {"type": "integer", "description": "CRF value for compression (lower=better, 18-28)", "default": 28},
      "start": {"type": "string", "description": "Start time for trim (HH:MM:SS)", "default": "00:00:00"},
      "duration": {"type": "integer", "description": "Duration in seconds for trim", "default": 10},
      "format": {"type": "string", "description": "Target format for convert (e.g., 'mp4', 'avi', 'mkv')", "default": "mp4"}},
     ["action", "input"], category="media")

tool("extract_text", "/text-extract",
     "Extract text from an image via OCR (Tesseract).",
     {"input": {"type": "string", "description": "Input image path"},
      "lang": {"type": "string", "description": "Language code (e.g., eng, spa)", "default": "eng"}},
     ["input"], category="media")

tool("generate_qrcode", "/qrcode",
     "Generate a QR code image from text.",
     {"text": {"type": "string", "description": "Content to encode in QR code"},
      "output": {"type": "string", "description": "Output image path", "default": "qrcode.png"},
      "size": {"type": "integer", "description": "QR code size in pixels", "default": 256}},
     ["text"], category="media")

tool("scan_barcode", "/scan-barcode",
     "Take a photo and scan for barcodes/QR codes.",
     {"camera_id": {"type": "integer", "description": "Camera ID (0=back, 1=front)", "default": 0},
      "output": {"type": "string", "description": "Output image path", "default": "barcode.jpg"}},
     category="media")

tool("screen_record", "/screen-record",
     "Start or stop screen recording.",
     {"action": {"type": "string", "enum": ["start", "stop"], "description": "Record action"},
      "output": {"type": "string", "description": "Output file path", "default": "screen_record.mp4"},
      "limit_seconds": {"type": "integer", "description": "Max recording duration", "default": 30}},
     ["action"], category="media")

tool("microphone_record", "/microphone-record",
     "Start or stop microphone audio recording.",
     {"action": {"type": "string", "enum": ["start", "stop"], "description": "Record action"},
      "output": {"type": "string", "description": "Output file path", "default": "recording.mp3"},
      "limit_seconds": {"type": "integer", "description": "Max recording duration", "default": 10}},
     ["action"], category="media")

tool("set_wallpaper", "/wallpaper",
     "Set device wallpaper from an image file.",
     {"file": {"type": "string", "description": "Image file path"},
      "lockscreen": {"type": "boolean", "description": "Also set as lockscreen wallpaper", "default": False}},
     category="media")

tool("set_ringtone", "/set-ringtone",
     "Set a sound file as the device ringtone.",
     {"file": {"type": "string", "description": "Audio file path"},
      "type": {"type": "string", "enum": ["ringtone", "notification", "alarm"], "description": "Sound type", "default": "ringtone"}},
     ["file"], category="media")


# ── Smart Tools ──────────────────────────────────────────────────────────────

tool("smart_install", "/smart-install",
     "Install specific packages with dependency/conflict detection and pre-flight checks. Use this when you KNOW the package name. For discovering WHAT package to install for a task, use smart_pkg instead.",
     {"packages": {"type": "string", "description": "Package names (space-separated)"},
      "manager": {"type": "string", "enum": ["auto", "pkg", "pip", "npm"], "description": "Package manager", "default": "auto"},
      "dry_run": {"type": "boolean", "description": "Preview only without installing", "default": False}},
     ["packages"], category="dev")

tool("run_diagnostics", "/diagnose",
     "Run diagnostics for a specific tool or environment category.",
     {"intent": {"type": "string", "enum": ["python", "pip", "node", "git", "storage", "packages", "all"], "description": "What to diagnose", "default": "all"}},
     category="fix")

tool("smart_pkg", "/pkg-smart",
     "Intent-based package DISCOVERY: tells you what package(s) to install for a task. Use when you DON'T know the package name. Has 60+ mappings (e.g., 'edit video' → ffmpeg). For direct install of known packages, use smart_install instead.",
     {"intent": {"type": "string", "description": "What you want to do (e.g., 'edit video', 'scan wifi', 'compile c')"},
      "install": {"type": "boolean", "description": "Install the recommended packages", "default": False}},
     ["intent"], category="dev")

tool("setup_dev_env", "/dev-env",
     "One-click setup of a development environment (Python, Node.js, etc.).",
     {"intent": {"type": "string", "description": "Development environment type"},
      "name": {"type": "string", "description": "Name for the environment"}},
     ["intent"], category="dev")

tool("load_profile", "/profile",
     "Load a pre-configured Termux profile (dev, python, web, hacker, etc.).",
     {"profile": {"type": "string", "description": "Profile name"},
      "dry_run": {"type": "boolean", "description": "Preview without applying", "default": False}},
     ["profile"], category="dev")

tool("optimize_device", "/optimize",
     "Analyze device performance and provide optimization recommendations.",
     {}, category="fix")

tool("explain_error", "/error-explain",
     "Gather device context to help diagnose and explain an error.",
     {"error": {"type": "string", "description": "The error message to explain"},
      "command": {"type": "string", "description": "The command that caused the error"}},
     ["error"], category="fix")

tool("fix_permissions", "/permission-fix",
     "Diagnose and fix permission issues in Termux environment.",
     {"target": {"type": "string", "description": "Specific path or area to fix"}},
     category="fix")

tool("audit_storage", "/storage-audit",
     "Scan storage for large files and suggest cleanup actions.",
     {"path": {"type": "string", "description": "Directory to audit", "default": "~"},
      "min_size_mb": {"type": "integer", "description": "Minimum file size in MB to report", "default": 10}},
     category="fix")

tool("dependency_tree", "/deps-tree",
     "Show the dependency tree of a package.",
     {"package": {"type": "string", "description": "Package name"}},
     ["package"], category="fix")

tool("fix_config", "/config-fix",
     "Check and fix common Termux configuration issues.",
     {"config": {"type": "string", "description": "Config area to check"}},
     category="fix")

tool("review_code", "/review",
     "Perform static analysis on a file: syntax check, linting, basic code review.",
     {"file": {"type": "string", "description": "File path to review"}},
     ["file"], category="dev")

tool("analyze_log", "/log-analyze",
     "Extract errors, warnings, and patterns from a log file.",
     {"file": {"type": "string", "description": "Log file path to analyze"}},
     ["file"], category="fix")

tool("generate_script", "/script-gen",
     "Generate a shell or Python script from a natural language description.",
     {"description": {"type": "string", "description": "What the script should do"},
      "type": {"type": "string", "enum": ["shell", "python"], "description": "Script type", "default": "shell"},
      "output": {"type": "string", "description": "Output file path"}},
     ["description"], category="dev")

tool("test_regex", "/regex",
     "Test a regex pattern against sample text using grep.",
     {"pattern": {"type": "string", "description": "Regex pattern to test"},
      "test": {"type": "string", "description": "Sample text to test against"}},
     ["pattern", "test"], category="dev")

tool("create_database", "/db-design",
     "Create a SQLite database from a schema description.",
     {"schema": {"type": "string", "description": "Database schema description (e.g., 'users(id int, name text)')"},
      "output": {"type": "string", "description": "Output database file path"}},
     ["schema"], category="dev")

tool("query_database", "/db-query",
     "Execute a SQL query on a SQLite database.",
     {"database": {"type": "string", "description": "Database file path"},
      "query": {"type": "string", "description": "SQL query to execute"}},
     ["database", "query"], category="dev")

tool("translate_text", "/translate",
     "Translate text between languages using Google Translate.",
     {"text": {"type": "string", "description": "Text to translate"},
      "target_lang": {"type": "string", "description": "Target language code (e.g., 'es', 'fr')", "default": "en"},
      "source_lang": {"type": "string", "description": "Source language code (auto-detect if omitted)", "default": "auto"}},
     ["text"], category="dev")

tool("tutorial", "/tutorial",
     "Start an interactive Termux learning guide on a topic.",
     {"topic": {"type": "string", "description": "Topic to learn about"}},
     ["topic"], category="dev")


# ── Git Operations ───────────────────────────────────────────────────────────

tool("git_operation", "/git-op",
     "Perform git operations: clone, status, log, diff, pull, push, branch listing.",
     {"action": {"type": "string", "enum": ["clone", "status", "log", "diff", "pull", "push", "branch", "add", "commit", "checkout"], "description": "Git action to perform"},
      "url": {"type": "string", "description": "Repository URL (required for clone)"},
      "directory": {"type": "string", "description": "Target directory (for clone)"},
      "repo_dir": {"type": "string", "description": "Existing repo directory (for status/log/diff/pull/push/branch)"},
      "message": {"type": "string", "description": "Commit message (for commit action)"},
      "branch": {"type": "string", "description": "Branch name (for checkout)"},
      "limit": {"type": "integer", "description": "Number of log entries (for log action)", "default": 5}},
     ["action"], category="git")

tool("smart_git", "/git-smart",
     "AI-friendly smart git operations: diff summary, recent log, suggest commit message, fix conflicts.",
     {"action": {"type": "string", "enum": ["diff-summary", "log-recent", "suggest-commit", "fix-conflict"], "description": "Smart git action"},
      "repo_dir": {"type": "string", "description": "Repository directory"}},
     ["action"], category="git")

tool("git_pr", "/git-pr",
     "Manage GitHub Pull Requests: list, create, merge, or check status.",
     {"action": {"type": "string", "enum": ["list", "create", "merge", "status"], "description": "PR action"},
      "repo_dir": {"type": "string", "description": "Repository directory"},
      "title": {"type": "string", "description": "PR title (for create)"},
      "body": {"type": "string", "description": "PR body/description (for create)"},
      "head": {"type": "string", "description": "Source branch (for create)"},
      "base": {"type": "string", "description": "Target branch (for create)", "default": "main"}},
     ["action"], category="git")


# ── SSH, Services & Migration ────────────────────────────────────────────────

tool("ssh_wizard", "/ssh-wizard",
     "Full SSH server setup with key generation, start/stop/status.",
     {"action": {"type": "string", "enum": ["setup", "status", "stop", "restart"], "description": "SSH action"},
      "port": {"type": "integer", "description": "SSH port", "default": 8022}},
     ["action"], category="services")

tool("service_guard", "/service-guard",
     "Manage background services: start, stop, restart, or check status.",
     {"action": {"type": "string", "enum": ["start", "stop", "restart", "status"], "description": "Service action"},
      "name": {"type": "string", "description": "Service name"},
      "cmd": {"type": "string", "description": "Command to run (for start action)"}},
     ["action", "name"], category="services")

tool("history_insight", "/history-insight",
     "Analyze shell history to suggest aliases and productivity improvements.",
     {"file": {"type": "string", "description": "History file path"},
      "limit": {"type": "integer", "description": "Number of entries to analyze", "default": 100}},
     category="shell")

tool("quick_cmd", "/quick-cmd",
     "Manage aliases and shortcuts: list, add, remove.",
     {"action": {"type": "string", "enum": ["list", "add", "remove"], "description": "Action"},
      "name": {"type": "string", "description": "Alias/shortcut name"},
      "cmd": {"type": "string", "description": "Command for the alias"}},
     ["action"], category="shell")

tool("port_manage", "/port-manage",
     "Check or manage network port visibility and forwarding.",
     {"action": {"type": "string", "enum": ["check", "forward", "close"], "description": "Port action"},
      "port": {"type": "integer", "description": "Port number"},
      "protocol": {"type": "string", "enum": ["tcp", "udp"], "description": "Protocol", "default": "tcp"}},
     ["action", "port"], category="network")

tool("migrate_environment", "/migrate",
     "Full Termux environment migration: backup, restore, or preview.",
     {"action": {"type": "string", "enum": ["backup", "restore", "preview"], "description": "Migration action"},
      "output": {"type": "string", "description": "Output file path"},
      "file": {"type": "string", "description": "Backup file to restore from"}},
     ["action"], category="backup")


# ── Other / Utility ──────────────────────────────────────────────────────────

tool("ping_server", "/ping",
     "Check if the Termux-MCP server is alive. Returns status and current working directory.",
     {}, method="GET", category="other")

tool("get_environment", "/env",
     "Get environment info: current working directory, home, PID, active command PID.",
     {}, method="GET", category="system")

tool("explain_command", "/explain",
     "Get a detailed explanation of what a shell command does.",
     {"cmd": {"type": "string", "description": "Shell command to explain"}},
     ["cmd"], category="shell")

tool("get_telephony_info", "/telephony-deviceinfo",
     "Get device telephony information (network type, IMEI, etc.).",
     {}, category="device")

tool("get_cell_info", "/telephony-cellinfo",
     "Get cell tower information.",
     {}, category="device")

tool("infrared_transmit", "/infrared",
     "Transmit an infrared signal (requires IR blaster hardware).",
     {"frequency": {"type": "integer", "description": "IR carrier frequency in Hz"},
      "pattern": {"type": "string", "description": "IR pattern to transmit"}},
     ["frequency", "pattern"], category="device")

tool("media_player", "/media-player",
     "Control media playback: play, pause, stop, next, previous, or get info.",
     {"action": {"type": "string", "enum": ["play", "pause", "stop", "info", "next", "previous"], "description": "Media player action"}},
     ["action"], category="media")

tool("storage_get", "/storage-get",
     "Pick a file via Android Storage Access Framework and copy it to Termux.",
     {"output": {"type": "string", "description": "Output file path in Termux"}},
     ["output"], category="files")

tool("history_list", "/history",
     "List recent command history entries.",
     {}, method="GET", category="shell")

tool("history_save", "/history",
     "Save a command to history.",
     {"cmd": {"type": "string", "description": "Command to save"}},
     ["cmd"], category="shell")

tool("history_clear", "/history-clear",
     "Clear command history.",
     {}, category="shell")

tool("context_get", "/context",
     "Get a value from persistent context storage.",
     {"key": {"type": "string", "description": "Context key"}},
     ["key"], category="other")

tool("context_set", "/context-save",
     "Save a value to persistent context storage.",
     {"key": {"type": "string", "description": "Context key"},
      "value": {"type": "string", "description": "Value to store"}},
     ["key", "value"], category="other")

tool("recipe_list", "/recipe-list",
     "List saved automation recipes.",
     {}, category="schedule")

tool("recipe_run", "/recipe-run",
     "Run a saved automation recipe by name.",
     {"name": {"type": "string", "description": "Recipe name"}},
     ["name"], category="schedule")

tool("recipe_save", "/recipe-save",
     "Save a sequence of steps as a reusable recipe.",
     {"name": {"type": "string", "description": "Recipe name"},
      "steps": {"type": "string", "description": "Recipe steps/commands"}},
     ["name", "steps"], category="schedule")

# ── Notification Trigger (new) ───────────────────────────────────────────────

tool("notification_listen", "/notification-listen",
     "Listen for Android notifications matching a filter and trigger an action.",
     {"action": {"type": "string", "enum": ["start", "stop", "status"], "description": "Listener action"},
      "filter": {"type": "string", "description": "Notification text filter (e.g., 'whatsapp', 'message')"},
      "trigger_cmd": {"type": "string", "description": "Command to run when notification matches"}},
     ["action"], category="schedule")


# ── Generate OpenAI Tool Schemas ─────────────────────────────────────────────

TOOLS = []
TOOL_NAME_TO_ENDPOINT = {}
TOOL_NAME_TO_METHOD = {}
TOOL_NAMES = []
TOOL_CATEGORIES: dict[str, list[str]] = {}

for t in TOOL_DEFS:
    schema = {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": {
                "type": "object",
                "properties": t["properties"],
            },
            "strict": False,
        },
    }
    if t["required"]:
        schema["function"]["parameters"]["required"] = t["required"]
    if not t["properties"]:
        del schema["function"]["parameters"]["properties"]
        schema["function"]["parameters"] = {"type": "object", "properties": {}}
    TOOLS.append(schema)
    TOOL_NAME_TO_ENDPOINT[t["name"]] = t["endpoint"]
    TOOL_NAME_TO_METHOD[t["name"]] = t.get("method", "POST")
    TOOL_NAMES.append(t["name"])
    cat = t.get("category", "other")
    TOOL_CATEGORIES.setdefault(cat, []).append(t["name"])


def load_plugins(plugin_dir: str | Path) -> int:
    """Load tool plugins from the given directory.
    
    Each .py file can define a `tools` (list of dicts with name/endpoint/method/
    description/properties/required/category) and/or `system_prompt_extras` (str)
    and/or `tool_handlers` (dict mapping name -> async callable).
    
    Returns number of plugin files loaded.
    """
    plugin_path = Path(plugin_dir)
    if not plugin_path.exists():
        plugin_path.mkdir(parents=True, exist_ok=True)
        return 0

    global SYSTEM_PROMPT, TOOLS, TOOL_NAME_TO_ENDPOINT, TOOL_NAME_TO_METHOD, TOOL_NAMES, TOOL_CATEGORIES

    loaded = 0
    for f in sorted(plugin_path.glob("*.py")):
        if f.stem.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"plugin_{f.stem}", f)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            extras_changed = False
            if hasattr(mod, "system_prompt_extras") and mod.system_prompt_extras:
                SYSTEM_PROMPT += f"\n\n{mod.system_prompt_extras}"
                extras_changed = True

            handlers_changed = False
            if hasattr(mod, "tool_handlers") and mod.tool_handlers:
                for name, handler in mod.tool_handlers.items():
                    _PLUGIN_HANDLERS[name] = handler
                handlers_changed = True

            tools_changed = False
            if hasattr(mod, "tools"):
                for t in mod.tools:
                    TOOL_DEFS.append(t)
                    schema = {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": {
                                "type": "object",
                                "properties": t.get("properties", {}),
                            },
                            "strict": False,
                        },
                    }
                    if t.get("required"):
                        schema["function"]["parameters"]["required"] = t["required"]
                    if not t.get("properties"):
                        del schema["function"]["parameters"]["properties"]
                        schema["function"]["parameters"] = {"type": "object", "properties": {}}
                    TOOLS.append(schema)
                    TOOL_NAME_TO_ENDPOINT[t["name"]] = t.get("endpoint", "")
                    TOOL_NAME_TO_METHOD[t["name"]] = t.get("method", "POST")
                    TOOL_NAMES.append(t["name"])
                    cat = t.get("category", "other")
                    TOOL_CATEGORIES.setdefault(cat, []).append(t["name"])
                tools_changed = True

            if tools_changed or handlers_changed or extras_changed:
                loaded += 1
                logger.info("Loaded plugin: %s", f.name)
        except Exception as e:
            logger.error("Failed to load plugin %s: %s", f.name, e)

    return loaded


_PLUGIN_HANDLERS: dict[str, Any] = {}

def get_plugin_handler(name: str) -> Any | None:
    return _PLUGIN_HANDLERS.get(name)
