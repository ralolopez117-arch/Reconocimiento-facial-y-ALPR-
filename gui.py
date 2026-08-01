import sys
import uuid
import cv2
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QGridLayout, QLabel, QVBoxLayout,
    QMenuBar, QMenu, QDialog, QListWidget, QPushButton, QHBoxLayout,
    QInputDialog, QMessageBox, QDockWidget, QListWidgetItem, QComboBox,
    QLineEdit, QFormLayout
)
from PyQt6.QtGui import QImage, QPixmap, QDrag
from PyQt6.QtCore import Qt, pyqtSlot, pyqtSignal, QMimeData
import numpy as np
import json

from config_manager import load_config, save_config
from video_worker import VideoWorker

class CameraFormDialog(QDialog):
    def __init__(self, parent=None, camera_data=None):
        super().__init__(parent)
        self.setWindowTitle("Configurar Cámara")
        self.camera_data = camera_data or {}

        layout = QFormLayout(self)

        self.name_input = QLineEdit()
        self.name_input.setText(self.camera_data.get("name", ""))
        layout.addRow("Nombre:", self.name_input)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["USB", "IP"])
        if self.camera_data.get("type") == "IP":
            self.type_combo.setCurrentText("IP")
        layout.addRow("Tipo:", self.type_combo)

        self.source_input = QLineEdit()
        self.source_input.setText(str(self.camera_data.get("source", "")))
        self.source_input.setPlaceholderText("Ej. 0 (USB) o http://... (IP)")
        layout.addRow("Fuente (Índice o URL):", self.source_input)

        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Guardar")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addRow(btn_layout)

    def get_data(self):
        return {
            "id": self.camera_data.get("id", str(uuid.uuid4())),
            "name": self.name_input.text(),
            "type": self.type_combo.currentText(),
            "source": self.source_input.text()
        }

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Administrador de Cámaras")
        self.setMinimumSize(400, 300)
        self.config = load_config()

        layout = QVBoxLayout()
        self.list_widget = QListWidget()
        self.update_list()
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton("Agregar")
        add_btn.clicked.connect(self.add_stream)
        edit_btn = QPushButton("Editar")
        edit_btn.clicked.connect(self.edit_stream)
        remove_btn = QPushButton("Eliminar")
        remove_btn.clicked.connect(self.remove_stream)
        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.accept)

        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def update_list(self):
        self.list_widget.clear()
        for cam in self.config.get("cameras", []):
            item = QListWidgetItem(f"[{cam['type']}] {cam['name']}")
            item.setData(Qt.ItemDataRole.UserRole, cam)
            self.list_widget.addItem(item)

    def add_stream(self):
        dialog = CameraFormDialog(self)
        if dialog.exec():
            data = dialog.get_data()
            self.config.setdefault("cameras", []).append(data)
            save_config(self.config)
            self.update_list()
            
    def edit_stream(self):
        item = self.list_widget.currentItem()
        if not item: return
        cam_data = item.data(Qt.ItemDataRole.UserRole)
        
        dialog = CameraFormDialog(self, cam_data)
        if dialog.exec():
            new_data = dialog.get_data()
            for i, c in enumerate(self.config["cameras"]):
                if c["id"] == new_data["id"]:
                    self.config["cameras"][i] = new_data
                    break
            save_config(self.config)
            self.update_list()

    def remove_stream(self):
        item = self.list_widget.currentItem()
        if not item: return
        cam_data = item.data(Qt.ItemDataRole.UserRole)
        self.config["cameras"] = [c for c in self.config["cameras"] if c["id"] != cam_data["id"]]
        save_config(self.config)
        self.update_list()

class CameraWidget(QLabel):
    double_clicked = pyqtSignal(QWidget)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: black; color: white;")
        self.setText("Arrastra una cámara aquí")
        self.setAcceptDrops(True)
        self.worker = None

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.stop()
        else:
            super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        data = event.mimeData().text()
        try:
            cam_info = json.loads(data)
            self.set_stream(cam_info["source"])
        except json.JSONDecodeError:
            pass

    def set_stream(self, stream_source):
        self.stop()
        self.setText("Cargando...")
        self.worker = VideoWorker(stream_source)
        self.worker.change_pixmap_signal.connect(self.update_image)
        self.worker.error_signal.connect(self.handle_error)
        self.worker.start()

    @pyqtSlot(np.ndarray)
    def update_image(self, cv_img):
        rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        q_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        
        pixmap = QPixmap.fromImage(q_img)
        scaled_pixmap = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        self.setPixmap(scaled_pixmap)

    @pyqtSlot(str)
    def handle_error(self, error_msg):
        self.setText(f"Error: {error_msg}")

    def stop(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self.clear()
        self.setText("Arrastra una cámara aquí")


class CameraListWidget(QListWidget):
    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item: return
        cam_data = item.data(Qt.ItemDataRole.UserRole)
        
        drag = QDrag(self)
        mimeData = QMimeData()
        mimeData.setText(json.dumps(cam_data))
        drag.setMimeData(mimeData)
        drag.exec(Qt.DropAction.CopyAction)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YOLO + ByteTrack Multi-Camera")
        self.resize(1200, 800)
        self.is_fullscreen_mode = False

        # Setup menu
        menu_bar = self.menuBar()
        settings_menu = menu_bar.addMenu("Opciones")
        
        config_action = settings_menu.addAction("Administrador de Cámaras")
        config_action.triggered.connect(self.open_settings)
        
        view_menu = menu_bar.addMenu("Vista")
        self.toggle_panel_action = view_menu.addAction("Mostrar/Ocultar Panel de Cámaras")
        self.toggle_panel_action.triggered.connect(self.toggle_side_panel)

        # Dock Widget
        self.dock = QDockWidget("Mis Cámaras", self)
        self.dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        
        self.camera_list = CameraListWidget()
        self.dock.setWidget(self.camera_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock)

        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.grid = QGridLayout(central_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)

        self.cameras = []
        for i in range(2):
            for j in range(2):
                cam = CameraWidget()
                cam.double_clicked.connect(self.toggle_fullscreen)
                self.grid.addWidget(cam, i, j)
                self.cameras.append(cam)

        self.refresh_side_panel()

    def refresh_side_panel(self):
        self.camera_list.clear()
        config = load_config()
        for cam in config.get("cameras", []):
            item = QListWidgetItem(f"[{cam['type']}] {cam['name']}")
            item.setData(Qt.ItemDataRole.UserRole, cam)
            self.camera_list.addItem(item)

    def toggle_side_panel(self):
        self.dock.setVisible(not self.dock.isVisible())

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec():
            self.refresh_side_panel()

    def toggle_fullscreen(self, clicked_cam):
        if not self.is_fullscreen_mode:
            for cam in self.cameras:
                if cam != clicked_cam:
                    cam.hide()
            self.is_fullscreen_mode = True
        else:
            for cam in self.cameras:
                cam.show()
            self.is_fullscreen_mode = False

    def closeEvent(self, event):
        for cam in self.cameras:
            cam.stop()
        event.accept()
