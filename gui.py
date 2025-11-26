import sys
import asyncio
import os
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QLabel,
    QProgressBar,
    QDialog,
    QTreeWidget,
    QTreeWidgetItem,
)
from PyQt6.QtCore import QTimer, Qt
from qasync import QEventLoop, asyncSlot
from client import TorrentClient
from torrent import Torrent


def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0: "", 1: "K", 2: "M", 3: "G", 4: "T"}
    while size >= power and n < len(power_labels) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels.get(n, '')}B"


class AddTorrentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Torrent")
        self.resize(500, 400)
        self.selected_files = []
        self.torrent_path = ""
        self.destination = ""
        self.torrent_info = None

        layout = QVBoxLayout(self)

        # File selection
        self.btn_browse = QPushButton("Select .torrent file")
        self.btn_browse.clicked.connect(self.browse_torrent)
        layout.addWidget(self.btn_browse)

        self.lbl_path = QLabel("No file selected")
        layout.addWidget(self.lbl_path)

        # Destination selection
        self.btn_dest = QPushButton("Select Destination Folder")
        self.btn_dest.clicked.connect(self.browse_dest)
        layout.addWidget(self.btn_dest)

        self.lbl_dest = QLabel(os.getcwd())
        self.destination = os.getcwd()
        layout.addWidget(self.lbl_dest)

        # Files tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Size"])
        layout.addWidget(self.tree)

        # Buttons
        buttons = QHBoxLayout()
        self.btn_ok = QPushButton("Download")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_ok.setEnabled(False)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_ok)
        buttons.addWidget(self.btn_cancel)
        layout.addLayout(buttons)

    def browse_torrent(self):
        fname, _ = QFileDialog.getOpenFileName(
            self, "Open Torrent", "", "Torrent Files (*.torrent)"
        )
        if fname:
            self.torrent_path = fname
            self.lbl_path.setText(fname)
            self.parse_torrent()

    def browse_dest(self):
        dname = QFileDialog.getExistingDirectory(self, "Select Directory")
        if dname:
            self.destination = dname
            self.lbl_dest.setText(dname)

    def parse_torrent(self):
        try:
            self.tree.clear()
            self.torrent_info = Torrent(self.torrent_path)

            if self.torrent_info.files:
                for idx, f in enumerate(self.torrent_info.files):
                    name = os.path.join(*f["path"])
                    size = format_bytes(f["length"])
                    item = QTreeWidgetItem([name, size])
                    item.setCheckState(0, Qt.CheckState.Checked)
                    item.setData(0, Qt.ItemDataRole.UserRole, idx)
                    self.tree.addTopLevelItem(item)
            else:
                # Single file
                item = QTreeWidgetItem(
                    [self.torrent_info.name, format_bytes(self.torrent_info.total_size)]
                )
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setData(0, Qt.ItemDataRole.UserRole, 0)
                self.tree.addTopLevelItem(item)

            self.btn_ok.setEnabled(True)
        except Exception as e:
            self.lbl_path.setText(f"Error: {e}")

    def get_data(self):
        # Get selected file indices
        file_mask = []
        root = self.tree.invisibleRootItem()
        count = root.childCount()

        if not self.torrent_info:
            return None, None, None

        if self.torrent_info.files:
            file_mask = [False] * count
            for i in range(count):
                item = root.child(i)
                if item.checkState(0) == Qt.CheckState.Checked:
                    idx = item.data(0, Qt.ItemDataRole.UserRole)
                    if idx < len(file_mask):
                        file_mask[idx] = True
        else:
            # Single file logic
            child = root.child(0)
            if child and child.checkState(0) == Qt.CheckState.Checked:
                file_mask = [True]
            else:
                file_mask = [False]

        return self.torrent_path, self.destination, file_mask


class MainWindow(QMainWindow):
    def __init__(self, client: TorrentClient):
        super().__init__()
        self.client = client
        self.setWindowTitle("Python BitTorrent Client")
        self.resize(800, 500)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Toolbar
        toolbar = QHBoxLayout()
        btn_add = QPushButton("Add Torrent")
        btn_add.clicked.connect(self.add_torrent_dialog)
        btn_pause = QPushButton("Pause")
        btn_pause.clicked.connect(self.pause_selected)
        btn_resume = QPushButton("Resume")
        btn_resume.clicked.connect(self.resume_selected)

        toolbar.addWidget(btn_add)
        toolbar.addWidget(btn_pause)
        toolbar.addWidget(btn_resume)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Progress", "Size", "Speed", "Status"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        # Update Timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(1000)

    def add_torrent_dialog(self):
        dialog = AddTorrentDialog(self)
        if dialog.exec():
            t_path, dest, mask = dialog.get_data()
            if t_path and mask and any(mask):
                asyncio.create_task(self.client.add_torrent(t_path, dest, mask))

    @asyncSlot()
    async def pause_selected(self):
        rows = self.table.selectionModel().selectedRows()
        for row in rows:
            idx = row.row()
            if idx < len(self.client.downloads):
                await self.client.pause_torrent(self.client.downloads[idx])

    @asyncSlot()
    async def resume_selected(self):
        rows = self.table.selectionModel().selectedRows()
        for row in rows:
            idx = row.row()
            if idx < len(self.client.downloads):
                await self.client.resume_torrent(self.client.downloads[idx])

    def update_ui(self):
        downloads = self.client.downloads
        self.table.setRowCount(len(downloads))

        for i, d in enumerate(downloads):
            pm = d.piece_manager

            # Name
            self._update_table_item(i, 0, d.torrent.name)

            displayed_downloaded = min(pm.total_downloaded, pm.total_selected_size)

            # Progress
            progress = 0
            if pm.total_selected_size > 0:
                progress = min(
                    100, (displayed_downloaded / pm.total_selected_size) * 100
                )
            elif pm.is_complete():
                progress = 100

            prog_widget = self.table.cellWidget(i, 1)
            if not prog_widget:
                prog_widget = QProgressBar()
                self.table.setCellWidget(i, 1, prog_widget)
            prog_widget.setValue(int(progress))

            # Size
            size_str = f"{format_bytes(displayed_downloaded)} / {format_bytes(pm.total_selected_size)}"
            self._update_table_item(i, 2, size_str)

            # Speed
            speed_str = f"{format_bytes(d.speed)}/s"
            self._update_table_item(i, 3, speed_str)

            # Status
            self._update_table_item(i, 4, d.status.name)

    def _update_table_item(self, row, col, text):
        """Вспомогательный метод для обновления ячейки без пересоздания."""
        item = self.table.item(row, col)
        if not item:
            item = QTableWidgetItem(text)
            self.table.setItem(row, col, item)
        else:
            if item.text() != text:
                item.setText(text)

    def closeEvent(self, event):
        """Обработка закрытия окна для корректной остановки."""
        self.client.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)

    # 1. Создаем цикл событий qasync правильно
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    client = TorrentClient()

    window = MainWindow(client)
    window.show()

    # 3. Запускаем вечный цикл
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
