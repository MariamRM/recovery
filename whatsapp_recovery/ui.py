from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QDate, Qt, QUrl
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QTextDocument

from whatsapp_recovery.services import (
    BackupLibrary,
    BackupLibraryEntry,
    ChatSummary,
    MessageRecord,
    RecoveryError,
    WhatsAppDatabase,
    adb_root_status,
    adb_status,
    attach_media_paths,
    build_media_index,
    decrypt_backup_to_library,
    extract_whatsapp_key_via_adb_root,
    export_chat_csv,
    export_chat_html,
    export_chat_json,
    filter_messages,
    format_timestamp,
    safe_filename,
    scan_backup_folder,
    validate_backup_file,
    validate_key_file,
)


@dataclass(slots=True)
class LoadedState:
    database_path: Path | None = None
    chats: list[ChatSummary] | None = None
    current_chat: ChatSummary | None = None
    current_messages: list[MessageRecord] | None = None
    library_entry: BackupLibraryEntry | None = None


class RecoveryMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WhatsApp Backup Reader & Recovery Assistant")
        self.resize(1400, 860)

        self.state = LoadedState(chats=[], current_messages=[])
        self.library = BackupLibrary()
        self.scanned_backups: list[Path] = []
        self.library_entries: list[BackupLibraryEntry] = []

        self.backup_path_input = QLineEdit()
        self.backup_folder_input = QLineEdit()
        self.key_path_input = QLineEdit()
        self.media_folder_input = QLineEdit()
        self.folder_backup_combo = QComboBox()
        self.library_backup_combo = QComboBox()
        self.crypt_version_label = QLabel("No backup selected")
        self.crypt_version_label.setObjectName("MutedLabel")
        self.device_status_label = QLabel("ADB status not checked yet")
        self.device_status_label.setObjectName("MutedLabel")
        self.media_status_label = QLabel("No media folder selected. Voice notes and PDF attachments will stay as references only.")
        self.media_status_label.setObjectName("MutedLabel")

        self.chat_filter_input = QLineEdit()
        self.chat_list = QListWidget()
        self.search_input = QLineEdit()
        self.start_date_input = QDateEdit()
        self.end_date_input = QDateEdit()
        self.export_type_combo = QComboBox()
        self.message_table = QTableWidget()
        self.chat_header_label = QLabel("No chat loaded")
        self.chat_header_label.setObjectName("TitleLabel")
        self.chat_meta_label = QLabel("Decrypt a backup to start browsing messages.")
        self.chat_meta_label.setObjectName("MutedLabel")
        self.media_index_root: Path | None = None
        self.media_index: dict[str, list[Path]] | None = None

        self._build_ui()
        self._bind_events()
        self.refresh_library_dropdown()

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        decrypt_action = QAction("Decrypt && Load", self)
        decrypt_action.triggered.connect(self.decrypt_and_load)
        toolbar.addAction(decrypt_action)

        load_saved_action = QAction("Load Saved Backup", self)
        load_saved_action.triggered.connect(self.load_selected_library_backup)
        toolbar.addAction(load_saved_action)

        auto_key_action = QAction("Auto Detect Key", self)
        auto_key_action.triggered.connect(self.auto_detect_key)
        toolbar.addAction(auto_key_action)

        open_media_action = QAction("Open Selected Media", self)
        open_media_action.triggered.connect(self.open_selected_media)
        toolbar.addAction(open_media_action)

        export_action = QAction("Export Current Chat", self)
        export_action.triggered.connect(self.export_current_chat)
        toolbar.addAction(export_action)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(16)

        root_layout.addWidget(self._build_header_card())
        root_layout.addWidget(self._build_workspace())

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Select a backup and key file to begin.")

    def _build_header_card(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("HeaderCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("WhatsApp Backup Reader & Recovery Assistant")
        title.setObjectName("TitleLabel")
        subtitle = QLabel(
            "Manual key-based recovery MVP. The app decrypts a local backup, reads the SQLite database, and exports chats without modifying WhatsApp."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("MutedLabel")

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)

        folder_row = self._folder_row()
        backup_row = self._path_row(self.backup_path_input, self.select_backup_file)
        key_row = self._path_row(self.key_path_input, self.select_key_file)

        form.addRow("Backup Folder", folder_row)
        form.addRow("Folder Backups", self.folder_backup_combo)
        form.addRow("Saved Library", self._library_row())
        form.addRow("Backup File", backup_row)
        form.addRow("Key File", key_row)
        form.addRow("Media Folder", self._media_row())
        form.addRow("Media Status", self.media_status_label)
        form.addRow("Detected Version", self.crypt_version_label)
        form.addRow("ADB / USB Status", self._device_row())

        actions = QHBoxLayout()
        decrypt_button = QPushButton("Decrypt && Load Chats")
        decrypt_button.clicked.connect(self.decrypt_and_load)
        load_saved_button = QPushButton("Load Saved Backup")
        load_saved_button.clicked.connect(self.load_selected_library_backup)
        auto_key_button = QPushButton("Auto Detect Key")
        auto_key_button.clicked.connect(self.auto_detect_key)
        open_media_button = QPushButton("Open Selected Media")
        open_media_button.clicked.connect(self.open_selected_media)
        check_adb_button = QPushButton("Check Device Status")
        check_adb_button.clicked.connect(self.refresh_adb_status)
        actions.addWidget(decrypt_button)
        actions.addWidget(load_saved_button)
        actions.addWidget(auto_key_button)
        actions.addWidget(open_media_button)
        actions.addWidget(check_adb_button)
        actions.addStretch(1)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(form)
        layout.addLayout(actions)
        return frame

    def _build_workspace(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_messages_card())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        return splitter

    def _build_sidebar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("SidebarCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel("Chats")
        header.setObjectName("TitleLabel")
        header.setStyleSheet("font-size: 14pt;")

        self.chat_filter_input.setPlaceholderText("Filter chats...")
        self.chat_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        layout.addWidget(header)
        layout.addWidget(self.chat_filter_input)
        layout.addWidget(self.chat_list, 1)
        return frame

    def _build_messages_card(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("MessagesCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.search_input.setPlaceholderText("Search messages...")
        self.start_date_input.setCalendarPopup(True)
        self.end_date_input.setCalendarPopup(True)
        today = QDate.currentDate()
        self.start_date_input.setDate(today.addYears(-20))
        self.end_date_input.setDate(today.addYears(1))
        self.export_type_combo.addItems(["HTML", "CSV", "JSON", "PDF"])

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Search"))
        filters.addWidget(self.search_input, 2)
        filters.addWidget(QLabel("From"))
        filters.addWidget(self.start_date_input)
        filters.addWidget(QLabel("To"))
        filters.addWidget(self.end_date_input)
        filters.addWidget(QLabel("Export"))
        filters.addWidget(self.export_type_combo)

        export_button = QPushButton("Export Current Chat")
        export_button.clicked.connect(self.export_current_chat)
        filters.addWidget(export_button)

        self.message_table.setColumnCount(6)
        self.message_table.setHorizontalHeaderLabels(
            ["Timestamp", "Sender", "Direction", "Text", "Media", "Reference"]
        )
        self.message_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.message_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.message_table.verticalHeader().setVisible(False)
        self.message_table.horizontalHeader().setStretchLastSection(True)

        layout.addWidget(self.chat_header_label)
        layout.addWidget(self.chat_meta_label)
        layout.addLayout(filters)
        layout.addWidget(self.message_table, 1)
        return frame

    def _path_row(self, line_edit: QLineEdit, callback) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        line_edit.setPlaceholderText("Select a file...")
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(callback)
        layout.addWidget(line_edit, 1)
        layout.addWidget(browse_button)
        return container

    def _folder_row(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.backup_folder_input.setPlaceholderText("Select a folder with WhatsApp backups...")
        browse_button = QPushButton("Browse Folder")
        browse_button.clicked.connect(self.select_backup_folder)
        scan_button = QPushButton("Scan")
        scan_button.clicked.connect(self.scan_selected_folder)
        layout.addWidget(self.backup_folder_input, 1)
        layout.addWidget(browse_button)
        layout.addWidget(scan_button)
        return container

    def _library_row(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_library_dropdown)
        layout.addWidget(self.library_backup_combo, 1)
        layout.addWidget(refresh_button)
        return container

    def _media_row(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.media_folder_input.setPlaceholderText("Select the WhatsApp Media folder to keep voices and PDFs...")
        browse_button = QPushButton("Browse Media")
        browse_button.clicked.connect(self.select_media_folder)
        layout.addWidget(self.media_folder_input, 1)
        layout.addWidget(browse_button)
        return container

    def _device_row(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.device_status_label, 1)
        return container

    def _bind_events(self) -> None:
        self.chat_filter_input.textChanged.connect(self.refresh_chat_list)
        self.chat_list.currentItemChanged.connect(self.on_chat_selected)
        self.folder_backup_combo.currentIndexChanged.connect(self.on_scanned_backup_selected)
        self.library_backup_combo.currentIndexChanged.connect(self.on_library_backup_selected)
        self.message_table.itemDoubleClicked.connect(lambda _: self.open_selected_media())
        self.search_input.textChanged.connect(self.refresh_message_table)
        self.start_date_input.dateChanged.connect(self.refresh_message_table)
        self.end_date_input.dateChanged.connect(self.refresh_message_table)

    def select_backup_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Backup Folder", "")
        if not path:
            return
        self.backup_folder_input.setText(path)
        self.scan_selected_folder()

    def select_backup_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select WhatsApp Backup",
            "",
            "WhatsApp Backups (*.crypt12 *.crypt14 *.crypt15);;All Files (*)",
        )
        if not path:
            return

        self.backup_path_input.setText(path)
        try:
            backup_path = Path(path)
            crypt_version = validate_backup_file(backup_path)
            self.crypt_version_label.setText(crypt_version)
            self.library.upsert_entry(backup_path, crypt_version)
            self.refresh_library_dropdown()
            self.statusBar().showMessage(f"Backup selected: {path}")
        except RecoveryError as exc:
            self.crypt_version_label.setText(str(exc))
            self.show_error(str(exc))

    def select_key_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select WhatsApp Key File", "", "All Files (*)")
        if not path:
            return
        self.key_path_input.setText(path)
        try:
            validate_key_file(Path(path))
            self.statusBar().showMessage(f"Key selected: {path}")
        except RecoveryError as exc:
            self.show_error(str(exc))

    def select_media_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select WhatsApp Media Folder", "")
        if not path:
            return
        media_root = Path(path)
        try:
            self._set_media_folder(media_root)
            self._persist_media_folder_for_current_backup(media_root)
            self.refresh_message_table()
        except RecoveryError as exc:
            self.show_error(str(exc))

    def scan_selected_folder(self) -> None:
        folder_text = self.backup_folder_input.text().strip()
        if not folder_text:
            self.show_error("Select a backup folder first.")
            return

        try:
            backups = scan_backup_folder(Path(folder_text))
        except RecoveryError as exc:
            self.show_error(str(exc))
            return

        self.scanned_backups = backups
        self.folder_backup_combo.blockSignals(True)
        self.folder_backup_combo.clear()
        if not backups:
            self.folder_backup_combo.addItem("No backup files found")
            self.folder_backup_combo.setCurrentIndex(0)
            self.folder_backup_combo.blockSignals(False)
            self.statusBar().showMessage("No .crypt12/.crypt14/.crypt15 files found in the selected folder.")
            return

        for backup in backups:
            self.folder_backup_combo.addItem(backup.name, str(backup))
        self.folder_backup_combo.blockSignals(False)
        self.folder_backup_combo.setCurrentIndex(0)
        self.statusBar().showMessage(f"Found {len(backups)} backup file(s) in {folder_text}")

    def refresh_library_dropdown(self) -> None:
        current_backup_path = self.library_backup_combo.currentData()
        try:
            self.library_entries = self.library.list_entries()
        except RecoveryError as exc:
            self.show_error(str(exc))
            self.library_entries = []

        self.library_backup_combo.blockSignals(True)
        self.library_backup_combo.clear()
        if not self.library_entries:
            self.library_backup_combo.addItem("No saved backups yet")
            self.library_backup_combo.setCurrentIndex(0)
            self.library_backup_combo.blockSignals(False)
            return

        for entry in self.library_entries:
            status = "ready" if entry.decrypted_db_path else "not decrypted"
            label = f"{entry.display_name} [{entry.crypt_version}] - {status}"
            self.library_backup_combo.addItem(label, entry.backup_path)
        self.library_backup_combo.blockSignals(False)
        restored_index = 0
        if current_backup_path:
            for index, entry in enumerate(self.library_entries):
                if entry.backup_path == current_backup_path:
                    restored_index = index
                    break
        self.library_backup_combo.setCurrentIndex(restored_index)

    def on_scanned_backup_selected(self, index: int) -> None:
        if index < 0 or index >= len(self.scanned_backups):
            return
        backup_path = self.scanned_backups[index]
        try:
            crypt_version = validate_backup_file(backup_path)
            self._set_selected_backup(backup_path)
            self.library.upsert_entry(backup_path, crypt_version)
            self.refresh_library_dropdown()
        except RecoveryError as exc:
            self.show_error(str(exc))

    def on_library_backup_selected(self, index: int) -> None:
        if index < 0 or index >= len(self.library_entries):
            return
        entry = self.library_entries[index]
        backup_path = Path(entry.backup_path)
        if backup_path.exists():
            try:
                self._set_selected_backup(backup_path)
            except RecoveryError as exc:
                self.show_error(str(exc))
        else:
            self.backup_path_input.setText(entry.backup_path)
            self.crypt_version_label.setText(entry.crypt_version)
        if entry.media_root_path:
            media_root = Path(entry.media_root_path)
            if media_root.exists():
                try:
                    self._set_media_folder(media_root)
                except RecoveryError as exc:
                    self.show_error(str(exc))
            else:
                self._clear_media_folder()
                self.media_folder_input.setText(entry.media_root_path)
                self.media_status_label.setText("Saved media folder is missing. Re-select it to keep voices and PDFs.")
        else:
            self._clear_media_folder()

    def _set_selected_backup(self, backup_path: Path) -> None:
        crypt_version = validate_backup_file(backup_path)
        self.backup_path_input.setText(str(backup_path))
        self.crypt_version_label.setText(crypt_version)
        matching_entry = next(
            (entry for entry in self.library_entries if entry.backup_path == str(backup_path.resolve())),
            None,
        )
        if matching_entry and matching_entry.media_root_path:
            media_root = Path(matching_entry.media_root_path)
            if media_root.exists():
                self._set_media_folder(media_root)
            else:
                self._clear_media_folder()
                self.media_folder_input.setText(matching_entry.media_root_path)
                self.media_status_label.setText("Saved media folder is missing. Re-select it to keep voices and PDFs.")
        else:
            self._clear_media_folder()
        self.statusBar().showMessage(f"Backup selected: {backup_path}")

    def _set_media_folder(self, media_root: Path) -> None:
        self.media_folder_input.setText(str(media_root))
        self.media_index = build_media_index(media_root)
        self.media_index_root = media_root
        self.media_status_label.setText(
            f"Media folder indexed. Linked files like voice notes and PDFs can now be opened and exported."
        )
        self.statusBar().showMessage(f"Media folder selected: {media_root}")

    def _clear_media_folder(self) -> None:
        self.media_folder_input.clear()
        self.media_index = None
        self.media_index_root = None
        self.media_status_label.setText(
            "No media folder selected. Voice notes and PDF attachments will stay as references only."
        )

    def _persist_media_folder_for_current_backup(self, media_root: Path) -> None:
        backup_text = self.backup_path_input.text().strip()
        if not backup_text:
            return
        backup_path = Path(backup_text)
        try:
            crypt_version = validate_backup_file(backup_path)
        except RecoveryError:
            return
        self.library.upsert_entry(backup_path, crypt_version, media_root_path=media_root)
        self.refresh_library_dropdown()

    def _current_media_root(self) -> Path | None:
        media_text = self.media_folder_input.text().strip()
        if not media_text:
            return None
        media_root = Path(media_text)
        if not media_root.exists() or not media_root.is_dir():
            return None
        return media_root

    def _current_media_index(self) -> dict[str, list[Path]] | None:
        media_root = self._current_media_root()
        if media_root is None:
            return None
        if self.media_index_root != media_root or self.media_index is None:
            self._set_media_folder(media_root)
        return self.media_index

    def _messages_with_media(self, messages: list[MessageRecord]) -> list[MessageRecord]:
        media_root = self._current_media_root()
        if media_root is None:
            return messages
        return attach_media_paths(messages, media_root, self._current_media_index())

    def refresh_adb_status(self) -> None:
        adb_message = adb_status()
        root_message = adb_root_status()
        status = f"{adb_message} {root_message}"
        self.device_status_label.setText(status)
        self.statusBar().showMessage(status)

    def auto_detect_key(self) -> None:
        try:
            key_path = extract_whatsapp_key_via_adb_root()
            self.key_path_input.setText(str(key_path))
            validate_key_file(key_path)
        except RecoveryError as exc:
            self.show_error(str(exc))
            self.device_status_label.setText(str(exc))
            return

        success_message = f"WhatsApp key extracted automatically to {key_path}"
        self.device_status_label.setText(
            "ADB authorized. Root access is available and the WhatsApp key was extracted successfully."
        )
        self.statusBar().showMessage(success_message)

    def decrypt_and_load(self) -> None:
        backup_path = Path(self.backup_path_input.text().strip())
        key_path = Path(self.key_path_input.text().strip())

        try:
            db_path, library_entry = decrypt_backup_to_library(
                backup_path,
                key_path,
                self.library,
                self._current_media_root(),
            )
            self._load_database(db_path, library_entry)
        except RecoveryError as exc:
            self.show_error(str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive UI guard
            self.show_error(f"Unexpected load failure: {exc}")
            return

        self.refresh_library_dropdown()
        self.statusBar().showMessage(f"Backup decrypted and saved to library: {db_path.name}")

    def load_selected_library_backup(self) -> None:
        index = self.library_backup_combo.currentIndex()
        if index < 0 or index >= len(self.library_entries):
            self.show_error("No saved backup is selected.")
            return

        entry = self.library_entries[index]
        db_path = entry.decrypted_db_file
        if db_path is None:
            self.show_error("This backup is in the library, but it has not been decrypted yet.")
            return
        if not db_path.exists():
            self.show_error("The saved decrypted database is missing from the local library.")
            return

        try:
            self._load_database(db_path, entry)
        except RecoveryError as exc:
            self.show_error(str(exc))
            return

        self.statusBar().showMessage(f"Loaded saved backup: {entry.display_name}")

    def _load_database(self, db_path: Path, library_entry: BackupLibraryEntry | None = None) -> None:
        database = WhatsAppDatabase(db_path)
        chats = database.load_chats()
        self.state.database_path = db_path
        self.state.chats = chats
        self.state.current_chat = None
        self.state.current_messages = []
        self.state.library_entry = library_entry
        self.refresh_chat_list()
        self.message_table.setRowCount(0)
        self.chat_header_label.setText("Chats Loaded")
        source_label = library_entry.display_name if library_entry else db_path.name
        self.chat_meta_label.setText(f"Loaded from library backup: {source_label}")
        self.statusBar().showMessage(f"Loaded {len(chats)} chats from {db_path.name}")

    def refresh_chat_list(self) -> None:
        chats = self.state.chats or []
        query = self.chat_filter_input.text().strip().lower()

        self.chat_list.clear()
        for chat in chats:
            haystack = f"{chat.title} {chat.chat_id}".lower()
            if query and query not in haystack:
                continue

            label = f"{chat.title} ({chat.message_count})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, chat.chat_id)
            item.setToolTip(chat.chat_id)
            self.chat_list.addItem(item)

        if self.chat_list.count() and not self.chat_list.currentItem():
            self.chat_list.setCurrentRow(0)

    def on_chat_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        del previous
        if current is None or not self.state.database_path:
            return

        chat_id = current.data(Qt.ItemDataRole.UserRole)
        chat = next((item for item in self.state.chats or [] if item.chat_id == chat_id), None)
        if chat is None:
            return

        try:
            database = WhatsAppDatabase(self.state.database_path)
            messages = database.load_messages(chat.chat_id)
        except RecoveryError as exc:
            self.show_error(str(exc))
            return

        self.state.current_chat = chat
        self.state.current_messages = messages
        self.chat_header_label.setText(chat.title)
        last_seen = format_timestamp(chat.last_message_at) or "No timestamps found"
        self.chat_meta_label.setText(
            f"{chat.message_count} messages | Last message: {last_seen} | Chat ID: {chat.chat_id}"
        )
        self.refresh_message_table()

    def refresh_message_table(self) -> None:
        messages = self._messages_with_media(self.state.current_messages or [])
        query = self.search_input.text()
        start_date = self._date_edit_to_datetime(self.start_date_input)
        end_date = self._date_edit_to_datetime(self.end_date_input)
        filtered = filter_messages(messages, query, start_date, end_date)

        self.message_table.setRowCount(len(filtered))
        for row_index, message in enumerate(filtered):
            values = [
                format_timestamp(message.timestamp_ms),
                message.sender,
                message.direction,
                message.text,
                message.media_name or message.media_type,
                message.resolved_media_path or message.media_reference,
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, message.resolved_media_path)
                if column_index == 2:
                    alignment = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                    item.setTextAlignment(int(alignment))
                self.message_table.setItem(row_index, column_index, item)

        self.message_table.resizeColumnsToContents()
        if self.state.current_chat:
            source_text = ""
            if self.state.library_entry:
                source_text = f" | Backup: {self.state.library_entry.display_name}"
            self.chat_meta_label.setText(
                f"{len(filtered)} visible messages | Chat ID: {self.state.current_chat.chat_id}{source_text}"
            )

    def export_current_chat(self) -> None:
        if not self.state.current_chat or not self.state.current_messages:
            self.show_error("Load a chat before exporting.")
            return

        export_type = self.export_type_combo.currentText().lower()
        extension = export_type
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Export {export_type.upper()}",
            f"{safe_filename(self.state.current_chat.title)}.{extension}",
            f"{export_type.upper()} Files (*.{extension});;All Files (*)",
        )
        if not path:
            return

        output_path = Path(path)
        filtered = filter_messages(
            self._messages_with_media(self.state.current_messages),
            self.search_input.text(),
            self._date_edit_to_datetime(self.start_date_input),
            self._date_edit_to_datetime(self.end_date_input),
        )
        include_media_files = self._current_media_root() is not None

        try:
            if export_type == "html":
                artifacts = export_chat_html(
                    self.state.current_chat.title,
                    filtered,
                    output_path,
                    include_media_files=include_media_files,
                )
            elif export_type == "csv":
                artifacts = export_chat_csv(filtered, output_path, include_media_files=include_media_files)
            elif export_type == "json":
                artifacts = export_chat_json(filtered, output_path, include_media_files=include_media_files)
            elif export_type == "pdf":
                artifacts = self._export_pdf(output_path, filtered, include_media_files)
            else:
                raise RecoveryError(f"Unsupported export type: {export_type}")
        except RecoveryError as exc:
            self.show_error(str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive UI guard
            self.show_error(f"Export failed: {exc}")
            return

        message = f"Exported {len(filtered)} messages to {output_path}"
        if artifacts.media_count:
            message += f" with {artifacts.media_count} media file(s) in {artifacts.media_folder}"
        self.statusBar().showMessage(message)

    def _export_pdf(
        self,
        output_path: Path,
        messages: list[MessageRecord],
        include_media_files: bool,
    ):
        temp_html = output_path.with_suffix(".html")
        artifacts = export_chat_html(
            self.state.current_chat.title,
            messages,
            temp_html,
            include_media_files=include_media_files,
        )
        html = temp_html.read_text(encoding="utf-8")

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(output_path))

        document = QTextDocument()
        document.setHtml(html)
        document.print(printer)

        temp_html.unlink(missing_ok=True)
        return artifacts

    def open_selected_media(self) -> None:
        current_row = self.message_table.currentRow()
        if current_row < 0:
            self.show_error("Select a message row first.")
            return
        item = self.message_table.item(current_row, 0)
        media_path = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if not media_path:
            self.show_error("The selected message does not have a resolved local media file.")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(media_path)):
            self.show_error("Failed to open the selected media file.")

    def _date_edit_to_datetime(self, widget: QDateEdit) -> datetime:
        date = widget.date()
        return datetime(date.year(), date.month(), date.day())

    def show_error(self, message: str) -> None:
        self.statusBar().showMessage(message)
        QMessageBox.critical(self, "Recovery Error", message)
