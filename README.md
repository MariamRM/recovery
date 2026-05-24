# WhatsApp Backup Reader & Recovery Assistant

Desktop app for viewing and exporting old WhatsApp chats from encrypted backups, with optional local media linking for voice notes and PDF attachments.

## MVP Scope

This build implements the first safe milestone:

1. Scan a folder for `msgstore.db.crypt12`, `msgstore.db.crypt14`, or `msgstore.db.crypt15`
2. Pick a discovered backup from a dropdown or select one manually
3. Keep imported backups in a local library for switching later
4. Select a manual WhatsApp `key` file
5. Select the WhatsApp Media folder if you want voice notes, PDFs, and other linked files preserved
6. Decrypt the backup into a library-managed SQLite copy with `wa-crypt-tools`
7. Load chats from the decrypted SQLite database
8. Browse messages in a desktop viewer
9. Open linked local media files directly from the app
10. Export the selected chat to `HTML`, `CSV`, `JSON`, or `PDF`

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
- Optional: Android Platform Tools (`adb`) on `PATH` for device status checks

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python app.py
```

## Media Support

If you choose the WhatsApp Media folder, the app will:

- resolve local voice notes and audio files
- resolve local PDFs and document attachments
- let you open a selected file from the message table
- copy linked files into the export package for `HTML`, `CSV`, `JSON`, and alongside `PDF`

Without the media folder, the app still exports message text and raw media references.

## Notes About Decryption

The app uses the `wa-crypt-tools` Python library directly. End users do not need a separate `wadecrypt.exe` install if you build the packaged desktop executable.

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

In the packaged Windows executable, this library is stored under:

```text
%LOCALAPPDATA%/WhatsAppBackupReader/
```

## Build A Downloadable Windows App

This project can be packaged as a self-contained Windows desktop app:

```powershell
.\build_windows.ps1
```

The packaged executable will be created at:

```text
dist/WhatsAppBackupReader.exe
```

You can share that `.exe` directly with other Windows users. They do not need Python installed.

## Project Layout

```text
app.py
requirements.txt
whatsapp_recovery/
  services.py
  ui.py
  theme.qss
```
