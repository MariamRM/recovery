# WhatsApp Backup Reader & Recovery Assistant

Desktop MVP for viewing and exporting old WhatsApp chats from encrypted backups.

## MVP Scope

This build implements the first safe milestone:

1. Scan a folder for `msgstore.db.crypt12`, `msgstore.db.crypt14`, or `msgstore.db.crypt15`
2. Pick a discovered backup from a dropdown or select one manually
3. Keep imported backups in a local library for switching later
4. Select a manual WhatsApp `key` file
5. Decrypt the backup into a library-managed SQLite copy with `wa-crypt-tools`
6. Load chats from the decrypted SQLite database
7. Browse messages in a desktop viewer
8. Export the selected chat to `HTML`, `CSV`, `JSON`, or `PDF`

The original single-backup MVP is still supported:

1. Select `msgstore.db.crypt12`, `msgstore.db.crypt14`, or `msgstore.db.crypt15`
2. Select a manual WhatsApp `key` file
3. Decrypt the backup into `msgstore.db` with `wa-crypt-tools`
4. Load chats from the decrypted SQLite database
5. Browse messages in a desktop viewer
6. Export the selected chat to `HTML`, `CSV`, `JSON`, or `PDF`

USB key extraction and rooted-device workflows are intentionally left as future work. The app only checks ADB availability/device authorization status for now.

## Safety Boundaries

The tool is designed for local backup inspection and export only. It does not:

- push messages back into WhatsApp
- modify WhatsApp databases on the phone
- bypass Android security
- access devices without user action

## Prerequisites

- Python 3.11+
- `wa-crypt-tools` installed and available as `wadecrypt`
- Optional: Android Platform Tools (`adb`) on `PATH` for device status checks

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install wa-crypt-tools
```

## Run

```powershell
python app.py
```

## Notes About Decryption

The app shells out to `wadecrypt`. If the command is not available, install `wa-crypt-tools` in the same environment used to start the app.

The decrypted SQLite file is first written next to the encrypted backup as:

```text
msgstore.db
```

If that file already exists, the app creates:

```text
msgstore.decrypted.db
```

After that, the app copies the decrypted database into its local library under:

```text
app_data/decrypted/
```

Saved backup metadata is stored in:

```text
app_data/backup_library.json
```

## Project Layout

```text
app.py
requirements.txt
whatsapp_recovery/
  services.py
  ui.py
  theme.qss
```
