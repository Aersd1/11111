import json
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QTableWidget, QAbstractItemView

PAPER_MIME = 'application/x-literature-paper-ids'


def start_paper_drag(widget, ids):
    if not ids:
        return
    mime = QMimeData()
    mime.setData(PAPER_MIME, json.dumps(ids).encode('utf-8'))
    drag = QDrag(widget)
    drag.setMimeData(mime)
    # Move means moving the index's category only. No file URLs are exposed,
    # and source rows are refreshed explicitly after the drop is completed.
    drag.exec(Qt.DropAction.MoveAction)


class PaperTable(QTableWidget):
    def __init__(self, *args):
        super().__init__(*args)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def startDrag(self, supported):
        ids = [self.item(i.row(), 0).data(Qt.ItemDataRole.UserRole + 1) for i in self.selectionModel().selectedRows()]
        start_paper_drag(self, ids)
