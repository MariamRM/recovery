from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from typing import Iterable
import zlib

from wa_crypt_tools.lib.db.dbfactory import DatabaseFactory
from wa_crypt_tools.lib.key.keyfactory import KeyFactory
from wa_crypt_tools.lib.utils import test_decompression

try:
    from Cryptodome.Cipher import AES
except ModuleNotFoundError:
    from Crypto.Cipher import AES


SUPPORTED_EXTENSIONS = {".crypt12", ".crypt14", ".crypt15"}


def _app_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "WhatsAppBackupReader"
        return Path.home() / "AppData" / "Local" / "WhatsAppBackupReader"
    return Path(__file__).resolve().parent.parent / "app_data"


APP_DATA_DIR = _app_data_dir()
DECRYPTED_LIBRARY_DIR = APP_DATA_DIR / "decrypted"
KEY_LIBRARY_DIR = APP_DATA_DIR / "keys"
LIBRARY_INDEX_PATH = APP_DATA_DIR / "backup_library.json"


class RecoveryError(Exception):
    """Raised for recoverable app errors."""


@dataclass(slots=True)
class ChatSummary:
    chat_id: str
    title: str
    last_message_at: int | None
    message_count: int


@dataclass(slots=True)
class MessageRecord:
    message_id: str
    chat_id: str
    timestamp_ms: int | None
    sender: str
    direction: str
    text: str
    media_type: str
    media_name: str
    media_reference: str
    resolved_media_path: str = ""

    @property
    def datetime_value(self) -> datetime | None:
        return timestamp_ms_to_datetime(self.timestamp_ms)

    @property
    def resolved_media_file(self) -> Path | None:
        return Path(self.resolved_media_path) if self.resolved_media_path else None


@dataclass(slots=True)
class BackupLibraryEntry:
    backup_path: str
    crypt_version: str
    display_name: str
    added_at: str
    decrypted_db_path: str = ""
    media_root_path: str = ""

    @property
    def backup_file(self) -> Path:
        return Path(self.backup_path)

    @property
    def decrypted_db_file(self) -> Path | None:
        return Path(self.decrypted_db_path) if self.decrypted_db_path else None

    @property
    def media_root_dir(self) -> Path | None:
        return Path(self.media_root_path) if self.media_root_path else None


@dataclass(slots=True)
class ExportArtifacts:
    media_count: int = 0
    media_folder: Path | None = None


def validate_backup_file(path: Path) -> str:
    if not path.exists():
        raise RecoveryError("Backup file does not exist.")
    if not path.is_file():
        raise RecoveryError("Backup path must point to a file.")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise RecoveryError("Backup must be a .crypt12, .crypt14, or .crypt15 file.")
    return path.suffix.lower().lstrip(".")


def validate_key_file(path: Path) -> None:
    if not path.exists():
        raise RecoveryError("Key file does not exist.")
    if not path.is_file():
        raise RecoveryError("Key path must point to a file.")
    if path.stat().st_size == 0:
        raise RecoveryError("Key file is empty.")


def safe_filename(value: str, fallback: str = "chat_export") -> str:
    cleaned = "".join(char if char not in '<>:"/\\|?*' else "_" for char in value).strip()
    return cleaned or fallback


def ensure_app_data_dirs() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    DECRYPTED_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    KEY_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)


def scan_backup_folder(folder_path: Path) -> list[Path]:
    if not folder_path.exists():
        raise RecoveryError("Backup folder does not exist.")
    if not folder_path.is_dir():
        raise RecoveryError("Backup folder path must point to a directory.")

    return sorted(
        [
            path
            for path in folder_path.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ],
        key=lambda item: item.name.lower(),
    )


def _library_db_name(backup_path: Path) -> str:
    digest = hashlib.sha1(str(backup_path.resolve()).encode("utf-8")).hexdigest()[:10]
    base_name = safe_filename(backup_path.stem or "msgstore")
    return f"{base_name}_{digest}.db"


def store_decrypted_copy(source_db_path: Path, backup_path: Path) -> Path:
    ensure_app_data_dirs()
    target_path = DECRYPTED_LIBRARY_DIR / _library_db_name(backup_path)
    shutil.copy2(source_db_path, target_path)
    return target_path


class BackupLibrary:
    def __init__(self, index_path: Path = LIBRARY_INDEX_PATH) -> None:
        self.index_path = index_path
        ensure_app_data_dirs()

    def list_entries(self) -> list[BackupLibraryEntry]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RecoveryError("Backup library index is corrupted.") from exc
        return [BackupLibraryEntry(**item) for item in data]

    def save_entries(self, entries: list[BackupLibraryEntry]) -> None:
        ensure_app_data_dirs()
        serialized = [asdict(entry) for entry in entries]
        self.index_path.write_text(json.dumps(serialized, indent=2), encoding="utf-8")

    def upsert_entry(
        self,
        backup_path: Path,
        crypt_version: str,
        decrypted_db_path: Path | None = None,
        media_root_path: Path | None = None,
    ) -> BackupLibraryEntry:
        entries = self.list_entries()
        backup_str = str(backup_path.resolve())
        existing = next((entry for entry in entries if entry.backup_path == backup_str), None)
        display_name = backup_path.name
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        decrypted_str = str(decrypted_db_path.resolve()) if decrypted_db_path else ""
        media_root_str = str(media_root_path.resolve()) if media_root_path else ""

        if existing:
            existing.crypt_version = crypt_version
            existing.display_name = display_name
            if decrypted_str:
                existing.decrypted_db_path = decrypted_str
            if media_root_str:
                existing.media_root_path = media_root_str
            self.save_entries(entries)
            return existing

        entry = BackupLibraryEntry(
            backup_path=backup_str,
            crypt_version=crypt_version,
            display_name=display_name,
            added_at=now,
            decrypted_db_path=decrypted_str,
            media_root_path=media_root_str,
        )
        entries.append(entry)
        self.save_entries(entries)
        return entry


def adb_status() -> str:
    adb_path = shutil.which("adb")
    if not adb_path:
        return "ADB not found on PATH."

    try:
        result = subprocess.run(
            [adb_path, "devices"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return f"ADB check failed: {exc}"

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        return "ADB available, but no device detected."

    device_lines = lines[1:]
    authorized = [line for line in device_lines if "\tdevice" in line]
    unauthorized = [line for line in device_lines if "\tunauthorized" in line]

    if authorized:
        return f"ADB available. Authorized device(s): {len(authorized)}."
    if unauthorized:
        return "Device detected, but USB debugging authorization is still pending."
    return "ADB available. Device state is not ready."


def _require_adb() -> str:
    adb_path = shutil.which("adb")
    if not adb_path:
        raise RecoveryError("ADB not found on PATH.")
    return adb_path


def _run_adb_command(
    args: list[str],
    *,
    timeout: int = 20,
    text: bool = True,
    check: bool = True,
):
    adb_path = _require_adb()
    result = subprocess.run(
        [adb_path, *args],
        capture_output=True,
        text=text,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if text and result.stderr else ""
        stdout = result.stdout.strip() if text and result.stdout else ""
        details = stderr or stdout or "Unknown ADB error."
        raise RecoveryError(f"ADB command failed: {details}")
    return result


def ensure_authorized_device() -> None:
    result = _run_adb_command(["devices"], timeout=10)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        raise RecoveryError("No Android device detected over ADB.")

    device_lines = lines[1:]
    authorized = [line for line in device_lines if "\tdevice" in line]
    unauthorized = [line for line in device_lines if "\tunauthorized" in line]

    if authorized:
        return
    if unauthorized:
        raise RecoveryError("Device is connected but USB debugging authorization is still pending.")
    raise RecoveryError("ADB found a device, but it is not in a usable state.")


def adb_root_status() -> str:
    try:
        ensure_authorized_device()
        result = _run_adb_command(["shell", "su", "-c", "id"], timeout=10, check=False)
    except RecoveryError as exc:
        return str(exc)

    combined = ""
    if result.stdout:
        combined += result.stdout
    if result.stderr:
        combined += f"\n{result.stderr}"

    if result.returncode == 0 and "uid=0" in combined:
        return "ADB authorized. Root access is available for automatic key extraction."
    return "ADB authorized, but root access is not available. Manual key file selection is required."


def _detect_remote_whatsapp_key_path() -> tuple[str, str]:
    candidates = [
        ("com.whatsapp", "/data/data/com.whatsapp/files/key"),
        ("com.whatsapp.w4b", "/data/data/com.whatsapp.w4b/files/key"),
    ]
    for package_name, remote_path in candidates:
        result = _run_adb_command(
            ["shell", "su", "-c", f"test -f {remote_path} && echo FOUND"],
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and "FOUND" in (result.stdout or ""):
            return package_name, remote_path
    raise RecoveryError(
        "No WhatsApp key file was found with root access. If this device is not rooted, select the key file manually."
    )


def extract_whatsapp_key_via_adb_root() -> Path:
    ensure_app_data_dirs()
    ensure_authorized_device()

    root_check = _run_adb_command(["shell", "su", "-c", "id"], timeout=10, check=False)
    combined = ""
    if root_check.stdout:
        combined += root_check.stdout
    if root_check.stderr:
        combined += f"\n{root_check.stderr}"
    if root_check.returncode != 0 or "uid=0" not in combined:
        raise RecoveryError(
            "Automatic key extraction is not available on non-rooted devices. Please provide the key file manually."
        )

    package_name, remote_key_path = _detect_remote_whatsapp_key_path()
    result = _run_adb_command(
        ["exec-out", "su", "-c", f"cat {remote_key_path}"],
        timeout=20,
        text=False,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise RecoveryError("Failed to read the WhatsApp key from the rooted device.")

    key_path = KEY_LIBRARY_DIR / f"{package_name.replace('.', '_')}_key"
    key_path.write_bytes(result.stdout)
    validate_key_file(key_path)
    return key_path


def decrypt_backup(backup_path: Path, key_path: Path) -> Path:
    validate_backup_file(backup_path)
    validate_key_file(key_path)

    output_path = backup_path.with_name("msgstore.db")
    if output_path.exists():
        output_path = backup_path.with_name("msgstore.decrypted.db")

    try:
        key = KeyFactory.new(str(key_path))
        with backup_path.open("rb") as encrypted_handle, output_path.open("wb") as decrypted_handle:
            database = DatabaseFactory.from_file(encrypted_handle)
            cipher = AES.new(key.get(), AES.MODE_GCM, database.get_iv())
            output_decrypted: bytearray = database.decrypt(key, encrypted_handle.read())
            try:
                z_obj = zlib.decompressobj()
                output_file = z_obj.decompress(output_decrypted)
                if not z_obj.eof:
                    raise RecoveryError("The encrypted database file is truncated or damaged.")
            except zlib.error:
                output_file = output_decrypted
                if not test_decompression(output_file[: io.DEFAULT_BUFFER_SIZE]):
                    raise RecoveryError(
                        "Decryption failed. The key may not match the backup, or the backup is empty."
                    )
            decrypted_handle.write(output_file)
            del cipher
    except RecoveryError:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise RecoveryError(f"Decryption failed: {exc}") from exc

    if not output_path.exists():
        raise RecoveryError("Decryption reported success, but no SQLite file was produced.")
    return output_path


def decrypt_backup_to_library(
    backup_path: Path,
    key_path: Path,
    library: BackupLibrary | None = None,
    media_root_path: Path | None = None,
) -> tuple[Path, BackupLibraryEntry]:
    crypt_version = validate_backup_file(backup_path)
    output_path = decrypt_backup(backup_path, key_path)
    library_copy = store_decrypted_copy(output_path, backup_path)
    index = library or BackupLibrary()
    entry = index.upsert_entry(backup_path, crypt_version, library_copy, media_root_path)
    return library_copy, entry


def timestamp_ms_to_datetime(timestamp_ms: int | None) -> datetime | None:
    if timestamp_ms in (None, 0):
        return None
    try:
        seconds = timestamp_ms / 1000 if timestamp_ms > 10_000_000_000 else timestamp_ms
        return datetime.fromtimestamp(seconds)
    except (OSError, OverflowError, ValueError):
        return None


def format_timestamp(timestamp_ms: int | None) -> str:
    value = timestamp_ms_to_datetime(timestamp_ms)
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _column_choice(columns: set[str], *names: str, default: str = "NULL") -> str:
    for name in names:
        if name in columns:
            return name
    return default


class WhatsAppDatabase:
    def __init__(self, db_path: Path) -> None:
        if not db_path.exists():
            raise RecoveryError("Decrypted SQLite database was not found.")
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _tables(self, connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {row["name"] for row in rows}

    def _columns(self, connection: sqlite3.Connection, table: str) -> set[str]:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    def load_chats(self) -> list[ChatSummary]:
        with self._connect() as connection:
            tables = self._tables(connection)
            if {"chat", "jid", "message"}.issubset(tables):
                return self._load_chats_modern(connection)
            if {"messages", "chat_list"}.issubset(tables) or "messages" in tables:
                return self._load_chats_legacy(connection)
            raise RecoveryError("Unsupported WhatsApp schema. Expected message/chat tables were not found.")

    def load_messages(self, chat_id: str) -> list[MessageRecord]:
        with self._connect() as connection:
            tables = self._tables(connection)
            if {"chat", "jid", "message"}.issubset(tables):
                return self._load_messages_modern(connection, chat_id)
            if "messages" in tables:
                return self._load_messages_legacy(connection, chat_id)
            raise RecoveryError("Unsupported WhatsApp schema for message loading.")

    def _load_chats_modern(self, connection: sqlite3.Connection) -> list[ChatSummary]:
        chat_cols = self._columns(connection, "chat")
        jid_cols = self._columns(connection, "jid")
        message_cols = self._columns(connection, "message")

        title_expr = "COALESCE(chat.subject, jid.user, jid.raw_string, CAST(chat._id AS TEXT))"
        if "subject" not in chat_cols:
            title_expr = "COALESCE(jid.user, jid.raw_string, CAST(chat._id AS TEXT))"
        if "user" not in jid_cols:
            title_expr = "COALESCE(jid.raw_string, CAST(chat._id AS TEXT))"

        timestamp_col = _column_choice(message_cols, "timestamp", "received_timestamp", default="NULL")

        query = f"""
            SELECT
                CAST(chat._id AS TEXT) AS chat_id,
                {title_expr} AS title,
                MAX({timestamp_col}) AS last_message_at,
                COUNT(message._id) AS message_count
            FROM chat
            LEFT JOIN jid ON jid._id = chat.jid_row_id
            LEFT JOIN message ON message.chat_row_id = chat._id
            GROUP BY chat._id, {title_expr}
            ORDER BY last_message_at DESC, title ASC
        """

        rows = connection.execute(query).fetchall()
        return [
            ChatSummary(
                chat_id=row["chat_id"],
                title=row["title"] or row["chat_id"],
                last_message_at=row["last_message_at"],
                message_count=row["message_count"] or 0,
            )
            for row in rows
        ]

    def _load_chats_legacy(self, connection: sqlite3.Connection) -> list[ChatSummary]:
        messages_cols = self._columns(connection, "messages")
        tables = self._tables(connection)
        chat_list_cols = self._columns(connection, "chat_list") if "chat_list" in tables else set()

        jid_col = _column_choice(messages_cols, "key_remote_jid", "remote_jid")
        timestamp_col = _column_choice(messages_cols, "timestamp", "received_timestamp", default="NULL")
        title_expr = jid_col
        join_clause = ""
        if "subject" in chat_list_cols:
            title_expr = f"COALESCE(chat_list.subject, {jid_col})"
            join_clause = f"LEFT JOIN chat_list ON chat_list.key_remote_jid = {jid_col}"

        query = f"""
            SELECT
                CAST({jid_col} AS TEXT) AS chat_id,
                {title_expr} AS title,
                MAX({timestamp_col}) AS last_message_at,
                COUNT(messages._id) AS message_count
            FROM messages
            {join_clause}
            GROUP BY chat_id, title
            ORDER BY last_message_at DESC, title ASC
        """

        rows = connection.execute(query).fetchall()
        return [
            ChatSummary(
                chat_id=row["chat_id"],
                title=row["title"] or row["chat_id"],
                last_message_at=row["last_message_at"],
                message_count=row["message_count"] or 0,
            )
            for row in rows
        ]

    def _load_messages_modern(self, connection: sqlite3.Connection, chat_id: str) -> list[MessageRecord]:
        message_cols = self._columns(connection, "message")
        jid_cols = self._columns(connection, "jid")

        timestamp_col = _column_choice(message_cols, "timestamp", "received_timestamp", default="NULL")
        from_me_col = _column_choice(message_cols, "from_me", default="0")
        text_col = _column_choice(message_cols, "text_data", "text", default="''")
        media_type_col = _column_choice(message_cols, "message_type", "media_wa_type", default="''")
        media_name_col = _column_choice(message_cols, "media_name", default="''")
        media_url_col = _column_choice(message_cols, "media_url", "media_caption", default="''")
        sender_join = ""
        sender_expr = "'Unknown'"
        if "sender_jid_row_id" in message_cols:
            sender_join = "LEFT JOIN jid AS sender_jid ON sender_jid._id = message.sender_jid_row_id"
            sender_expr = "COALESCE(sender_jid.user, sender_jid.raw_string, 'Unknown')"
            if "user" not in jid_cols:
                sender_expr = "COALESCE(sender_jid.raw_string, 'Unknown')"

        query = f"""
            SELECT
                CAST(message._id AS TEXT) AS message_id,
                CAST(message.chat_row_id AS TEXT) AS chat_id,
                {timestamp_col} AS timestamp_ms,
                {from_me_col} AS from_me,
                COALESCE({text_col}, '') AS text,
                CAST(COALESCE({media_type_col}, '') AS TEXT) AS media_type,
                CAST(COALESCE({media_name_col}, '') AS TEXT) AS media_name,
                CAST(COALESCE({media_url_col}, '') AS TEXT) AS media_reference,
                {sender_expr} AS sender
            FROM message
            {sender_join}
            WHERE CAST(message.chat_row_id AS TEXT) = ?
            ORDER BY timestamp_ms ASC, message._id ASC
        """

        rows = connection.execute(query, (chat_id,)).fetchall()
        return [
            MessageRecord(
                message_id=row["message_id"],
                chat_id=row["chat_id"],
                timestamp_ms=row["timestamp_ms"],
                sender="Me" if int(row["from_me"] or 0) else row["sender"],
                direction="Outgoing" if int(row["from_me"] or 0) else "Incoming",
                text=row["text"] or "",
                media_type=row["media_type"] or "",
                media_name=row["media_name"] or "",
                media_reference=row["media_reference"] or "",
            )
            for row in rows
        ]

    def _load_messages_legacy(self, connection: sqlite3.Connection, chat_id: str) -> list[MessageRecord]:
        message_cols = self._columns(connection, "messages")

        jid_col = _column_choice(message_cols, "key_remote_jid", "remote_jid")
        timestamp_col = _column_choice(message_cols, "timestamp", "received_timestamp", default="NULL")
        from_me_col = _column_choice(message_cols, "key_from_me", "from_me", default="0")
        text_col = _column_choice(message_cols, "data", "text_data", default="''")
        media_type_col = _column_choice(message_cols, "media_wa_type", "message_type", default="''")
        media_name_col = _column_choice(message_cols, "media_name", default="''")
        media_ref_col = _column_choice(message_cols, "media_url", "remote_resource", default="''")
        sender_col = _column_choice(message_cols, "remote_resource", default="''")

        query = f"""
            SELECT
                CAST(_id AS TEXT) AS message_id,
                CAST({jid_col} AS TEXT) AS chat_id,
                {timestamp_col} AS timestamp_ms,
                {from_me_col} AS from_me,
                COALESCE({text_col}, '') AS text,
                CAST(COALESCE({media_type_col}, '') AS TEXT) AS media_type,
                CAST(COALESCE({media_name_col}, '') AS TEXT) AS media_name,
                CAST(COALESCE({media_ref_col}, '') AS TEXT) AS media_reference,
                CAST(COALESCE({sender_col}, '') AS TEXT) AS sender
            FROM messages
            WHERE CAST({jid_col} AS TEXT) = ?
            ORDER BY timestamp_ms ASC, _id ASC
        """

        rows = connection.execute(query, (chat_id,)).fetchall()
        return [
            MessageRecord(
                message_id=row["message_id"],
                chat_id=row["chat_id"],
                timestamp_ms=row["timestamp_ms"],
                sender="Me" if int(row["from_me"] or 0) else (row["sender"] or chat_id),
                direction="Outgoing" if int(row["from_me"] or 0) else "Incoming",
                text=row["text"] or "",
                media_type=row["media_type"] or "",
                media_name=row["media_name"] or "",
                media_reference=row["media_reference"] or "",
            )
            for row in rows
        ]


def filter_messages(
    messages: Iterable[MessageRecord],
    text_query: str = "",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[MessageRecord]:
    query = text_query.strip().lower()
    filtered: list[MessageRecord] = []

    for message in messages:
        timestamp = message.datetime_value
        if query:
            haystack = " ".join(
                [message.sender, message.text, message.media_name, message.media_reference]
            ).lower()
            if query not in haystack:
                continue

        if start_date and timestamp and timestamp.date() < start_date.date():
            continue
        if end_date and timestamp and timestamp.date() > end_date.date():
            continue

        filtered.append(message)

    return filtered


def build_media_index(media_root: Path) -> dict[str, list[Path]]:
    if not media_root.exists():
        raise RecoveryError("Media folder does not exist.")
    if not media_root.is_dir():
        raise RecoveryError("Media folder path must point to a directory.")

    index: dict[str, list[Path]] = {}
    for path in media_root.rglob("*"):
        if not path.is_file():
            continue
        index.setdefault(path.name.lower(), []).append(path)
    return index


def resolve_media_path(
    message: MessageRecord,
    media_root: Path | None,
    media_index: dict[str, list[Path]] | None = None,
) -> Path | None:
    if not media_root:
        return None

    if message.resolved_media_path:
        resolved = Path(message.resolved_media_path)
        if resolved.exists():
            return resolved

    candidates: list[str] = []
    for raw_value in (message.media_reference, message.media_name):
        value = (raw_value or "").strip()
        if not value:
            continue
        candidate_path = Path(value)
        if candidate_path.is_absolute() and candidate_path.exists():
            return candidate_path
        candidates.append(value)
        if candidate_path.name:
            candidates.append(candidate_path.name)

    for candidate in candidates:
        direct = media_root / candidate
        if direct.exists() and direct.is_file():
            return direct

    lowered_names = {Path(candidate).name.lower() for candidate in candidates if Path(candidate).name}
    if media_index:
        for lowered_name in lowered_names:
            matches = media_index.get(lowered_name, [])
            if matches:
                return matches[0]
    return None


def attach_media_paths(
    messages: Iterable[MessageRecord],
    media_root: Path | None,
    media_index: dict[str, list[Path]] | None = None,
) -> list[MessageRecord]:
    attached: list[MessageRecord] = []
    for message in messages:
        resolved = resolve_media_path(message, media_root, media_index)
        attached.append(
            MessageRecord(
                message_id=message.message_id,
                chat_id=message.chat_id,
                timestamp_ms=message.timestamp_ms,
                sender=message.sender,
                direction=message.direction,
                text=message.text,
                media_type=message.media_type,
                media_name=message.media_name,
                media_reference=message.media_reference,
                resolved_media_path=str(resolved) if resolved else "",
            )
        )
    return attached


def _message_dict(message: MessageRecord) -> dict[str, str | int | None]:
    return {
        "message_id": message.message_id,
        "chat_id": message.chat_id,
        "timestamp": format_timestamp(message.timestamp_ms),
        "sender": message.sender,
        "direction": message.direction,
        "text": message.text,
        "media_type": message.media_type,
        "media_name": message.media_name,
        "media_reference": message.media_reference,
        "resolved_media_path": message.resolved_media_path,
    }


def _copy_media_files(
    messages: list[MessageRecord],
    output_path: Path,
) -> tuple[list[str], ExportArtifacts]:
    media_folder = output_path.parent / f"{output_path.stem}_media"
    copied_map: dict[str, str] = {}
    copied_refs: list[str] = []
    media_count = 0

    for message in messages:
        resolved = message.resolved_media_file
        if not resolved or not resolved.exists():
            copied_refs.append("")
            continue

        source_key = str(resolved.resolve())
        if source_key in copied_map:
            copied_refs.append(copied_map[source_key])
            continue

        media_folder.mkdir(parents=True, exist_ok=True)
        target_name = safe_filename(resolved.name, "media_file")
        target_path = media_folder / target_name
        suffix_counter = 1
        while target_path.exists() and target_path.resolve() != resolved.resolve():
            target_path = media_folder / f"{target_path.stem}_{suffix_counter}{target_path.suffix}"
            suffix_counter += 1

        shutil.copy2(resolved, target_path)
        relative_ref = target_path.relative_to(output_path.parent).as_posix()
        copied_map[source_key] = relative_ref
        copied_refs.append(relative_ref)
        media_count += 1

    artifacts = ExportArtifacts(media_count=media_count, media_folder=media_folder if media_count else None)
    return copied_refs, artifacts


def export_chat_html(
    chat_title: str,
    messages: list[MessageRecord],
    output_path: Path,
    include_media_files: bool = False,
) -> ExportArtifacts:
    copied_refs = [""] * len(messages)
    artifacts = ExportArtifacts()
    if include_media_files:
        copied_refs, artifacts = _copy_media_files(messages, output_path)

    rows = []
    for index, message in enumerate(messages):
        row = _message_dict(message)
        media_value = _html_escape(row["media_name"] or "")
        reference_value = _html_escape(row["media_reference"] or "")
        if include_media_files and copied_refs[index]:
            media_value = f'<a href="{_html_escape(copied_refs[index])}">{media_value or "Open file"}</a>'
            reference_value = f'<a href="{_html_escape(copied_refs[index])}">{_html_escape(copied_refs[index])}</a>'
        elif row["resolved_media_path"]:
            resolved_name = Path(str(row["resolved_media_path"])).name
            media_value = _html_escape(row["media_name"] or resolved_name)
            reference_value = _html_escape(str(row["resolved_media_path"]))
        rows.append(
            "<tr>"
            f"<td>{_html_escape(row['timestamp'] or '')}</td>"
            f"<td>{_html_escape(row['sender'] or '')}</td>"
            f"<td>{_html_escape(row['direction'] or '')}</td>"
            f"<td>{_html_escape(row['text'] or '')}</td>"
            f"<td>{media_value}</td>"
            f"<td>{reference_value}</td>"
            "</tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{_html_escape(chat_title)}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #1f2933; }}
    h1 {{ color: #0f5132; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid #d7dde3; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #e8eef2; }}
    tr:nth-child(even) {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <h1>{_html_escape(chat_title)}</h1>
  <p>Exported messages: {len(messages)}</p>
  <table>
    <thead>
      <tr>
        <th>Timestamp</th>
        <th>Sender</th>
        <th>Direction</th>
        <th>Text</th>
        <th>Media Name</th>
        <th>Media Reference</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    return artifacts


def export_chat_csv(
    messages: list[MessageRecord],
    output_path: Path,
    include_media_files: bool = False,
) -> ExportArtifacts:
    copied_refs = [""] * len(messages)
    artifacts = ExportArtifacts()
    if include_media_files:
        copied_refs, artifacts = _copy_media_files(messages, output_path)

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "message_id",
                "chat_id",
                "timestamp",
                "sender",
                "direction",
                "text",
                "media_type",
                "media_name",
                "media_reference",
                "resolved_media_path",
                "exported_media_path",
            ],
        )
        writer.writeheader()
        for index, message in enumerate(messages):
            row = _message_dict(message)
            row["exported_media_path"] = copied_refs[index]
            writer.writerow(row)
    return artifacts


def export_chat_json(
    messages: list[MessageRecord],
    output_path: Path,
    include_media_files: bool = False,
) -> ExportArtifacts:
    copied_refs = [""] * len(messages)
    artifacts = ExportArtifacts()
    if include_media_files:
        copied_refs, artifacts = _copy_media_files(messages, output_path)

    data = [_message_dict(message) for message in messages]
    for index, row in enumerate(data):
        row["exported_media_path"] = copied_refs[index]
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifacts


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
