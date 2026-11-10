"""Native Qt client for NX300M MOD installation and LUT management.

The photo comparison interaction is adapted from the MIT licensed
``hestela/lut-previewer`` project. See LICENSES for attribution and terms.
"""
from __future__ import annotations

from datetime import datetime
from collections import OrderedDict
import json
import os
from pathlib import Path
import sys

import numpy as np
from PySide6.QtCore import (QItemSelection, QItemSelectionModel, QObject, QPoint,
                            QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal)
from PySide6.QtGui import (
    QColor, QFont, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QCompleter, QDialog,
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSlider, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from nx300m_lut_core import (
    Lut3D, LutError, apply_lut, blend_luts, cube_bytes, expand_lut_paths,
    identity_lut, load_card_lut, load_image_array, parse_lut_file,
)


APP_STYLESHEET = """
QWidget { color: #18313b; font-family: "Microsoft YaHei UI"; font-size: 10pt; }
QMainWindow, QWidget#appRoot { background: #eef3f4; }
QFrame#navRail { background: #12323a; border-radius: 0; }
QLabel#brandTitle { color: white; font-size: 20pt; font-weight: 700; }
QLabel#brandSub { color: #9fc0c7; font-size: 9pt; }
QPushButton#navButton { color: #d6e6e9; background: transparent; border: 0; text-align: left; padding: 13px 16px; border-radius: 9px; }
QPushButton#navButton:hover { background: #1b4650; color: white; }
QPushButton#navButton:checked { background: #1ba4a5; color: white; font-weight: 700; }
QFrame#panel, QFrame#hero, QFrame#toolbarCard, QFrame#detailPanel { background: white; border: 1px solid #dbe5e7; border-radius: 14px; }
QLabel#pageTitle { font-size: 22pt; font-weight: 700; color: #14333d; }
QLabel#sectionTitle { font-size: 13pt; font-weight: 700; color: #173b45; }
QLabel#muted { color: #667c84; }
QLabel#countChip { color: #0d7778; background: #dcf5f2; border-radius: 10px; padding: 5px 10px; font-weight: 700; }
QPushButton { background: #f6f9fa; border: 1px solid #cfdddf; border-radius: 9px; padding: 9px 14px; }
QPushButton:hover { background: #eaf2f4; border-color: #a9c3c8; }
QPushButton:pressed { background: #dbe8ea; }
QPushButton:checked { background: #d9f1ef; border-color: #79c5c3; color: #0e6668; font-weight: 700; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus { border: 1px solid #1ba4a5; }
QPushButton:disabled { color: #94a5aa; background: #eef3f4; border-color: #dfe7e8; }
QPushButton#primary { background: #158f93; border-color: #158f93; color: white; font-weight: 700; }
QPushButton#primary:hover { background: #10797d; }
QPushButton#danger { color: #a73c3c; border-color: #e3baba; background: #fff8f8; }
QPushButton#subtle { background: transparent; border: 0; color: #527078; }
QLineEdit, QComboBox { background: white; border: 1px solid #cfdddf; border-radius: 9px; padding: 8px 10px; min-height: 20px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #1ba4a5; }
QTableWidget { background: white; border: 0; gridline-color: #edf2f3; alternate-background-color: #f8fbfb; selection-background-color: #d9f1ef; selection-color: #12323a; }
QHeaderView::section { background: #f3f7f8; color: #526a72; border: 0; border-bottom: 1px solid #dbe5e7; padding: 8px; font-weight: 700; }
QSlider::groove:horizontal { height: 4px; background: #d8e4e6; border-radius: 2px; }
QSlider::handle:horizontal { width: 16px; margin: -6px 0; background: #159397; border-radius: 8px; }
QProgressBar { border: 0; background: #dce8ea; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #1ba4a5; border-radius: 3px; }
QTextEdit { background: #102a31; color: #cfe2e5; border: 0; border-radius: 10px; padding: 12px; font-family: "Cascadia Mono"; }
QToolTip { background: #16363f; color: white; border: 0; padding: 6px; }
"""


def array_to_qimage(array: np.ndarray) -> QImage:
    data = np.clip(array * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if data.ndim != 3 or data.shape[2] != 3:
        raise ValueError('preview array must be HxWx3')
    height, width, _ = data.shape
    return QImage(data.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.result.emit(self.function())
        except Exception as error:
            self.signals.error.emit(str(error))
        finally:
            self.signals.finished.emit()


class PreviewTask(QRunnable):
    class Signals(QObject):
        result = Signal(int, object, object)
        error = Signal(int, str)
        finished = Signal(int)

    def __init__(self, generation: int, original: np.ndarray | None, lut: Lut3D,
                 strength: float, image_path: Path | None):
        super().__init__()
        self.generation = generation
        self.original = original
        self.lut = lut
        self.strength = strength
        self.image_path = image_path
        self.cancelled = False
        self.signals = self.Signals()

    def run(self):
        try:
            original = self.original
            if original is None:
                original = load_image_array(self.image_path, max_edge=900)
            if self.cancelled:
                return
            processed = apply_lut(original, self.lut, strength=self.strength)
            if self.cancelled:
                return
            self.signals.result.emit(self.generation, original, processed)
        except Exception as error:
            self.signals.error.emit(self.generation, str(error))
        finally:
            self.signals.finished.emit(self.generation)


class LutLoadTask(QRunnable):
    class Signals(QObject):
        result = Signal(int, str, object)
        error = Signal(int, str, str)
        finished = Signal(int)

    def __init__(self, generation: int, key: str, look_dir: Path, metadata: dict):
        super().__init__()
        self.generation = generation
        self.key = key
        self.look_dir = look_dir
        self.metadata = metadata
        self.cancelled = False
        self.signals = self.Signals()

    def run(self):
        try:
            lut = load_card_lut(self.look_dir, self.metadata, prefer_native=True)
            if self.cancelled:
                return
            self.signals.result.emit(self.generation, self.key, lut)
        except Exception as error:
            self.signals.error.emit(self.generation, self.key, str(error))
        finally:
            self.signals.finished.emit(self.generation)


class BlendPairTask(QRunnable):
    class Signals(QObject):
        result = Signal(int, object, object)
        error = Signal(int, str)
        finished = Signal(int)

    def __init__(self, generation: int, a_path: Path | None, b_path: Path | None):
        super().__init__()
        self.generation = generation
        self.a_path = a_path
        self.b_path = b_path
        self.cancelled = False
        self.signals = self.Signals()

    def run(self):
        try:
            a = identity_lut(title='原图') if self.a_path is None else parse_lut_file(self.a_path).lut
            b = identity_lut(title='原图') if self.b_path is None else parse_lut_file(self.b_path).lut
            if not self.cancelled:
                self.signals.result.emit(self.generation, a, b)
        except Exception as error:
            if not self.cancelled:
                self.signals.error.emit(self.generation, str(error))
        finally:
            self.signals.finished.emit(self.generation)


class BlendPreviewTask(QRunnable):
    class Signals(QObject):
        result = Signal(int, object, object)
        error = Signal(int, str)
        finished = Signal(int)

    def __init__(self, generation: int, original: np.ndarray | None, a: Lut3D,
                 b: Lut3D, amount_b: float, image_path: Path | None):
        super().__init__()
        self.generation = generation
        self.original = original
        self.a = a
        self.b = b
        self.amount_b = amount_b
        self.image_path = image_path
        self.cancelled = False
        self.signals = self.Signals()

    def run(self):
        try:
            original = self.original
            if original is None:
                original = load_image_array(self.image_path, max_edge=900)
            blended = blend_luts(self.a, self.b, self.amount_b)
            if not self.cancelled:
                self.signals.result.emit(self.generation, original, apply_lut(original, blended))
        except Exception as error:
            if not self.cancelled:
                self.signals.error.emit(self.generation, str(error))
        finally:
            self.signals.finished.emit(self.generation)


class LutPreviewViewer(QWidget):
    """Before/after photo viewer with a draggable split and fit/zoom controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original: QPixmap | None = None
        self.processed: QPixmap | None = None
        self.mode = 'split'
        self.split = 0.52
        self.zoom = 1.0
        self.pan = QPoint(0, 0)
        self._dragging_split = False
        self._panning = False
        self._last_mouse = QPoint()
        self.busy = False
        self.busy_text = ''
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(40)
        self._spinner_timer.timeout.connect(self._advance_spinner)
        self.setMinimumSize(460, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_images(self, original: np.ndarray, processed: np.ndarray):
        self.original = QPixmap.fromImage(array_to_qimage(original))
        self.processed = QPixmap.fromImage(array_to_qimage(processed))
        self.reset_view()

    def set_busy(self, busy: bool, text: str = '正在加载 LUT…'):
        self.busy = busy
        self.busy_text = text
        if busy:
            self._spinner_timer.start()
        else:
            self._spinner_timer.stop()
        self.update()

    def _advance_spinner(self):
        self._spinner_angle = (self._spinner_angle + 12) % 360
        self.update()

    def set_mode(self, mode: str):
        self.mode = mode
        self.update()

    def reset_view(self):
        self.zoom = 1.0
        self.pan = QPoint(0, 0)
        self.update()

    def _image_rect(self) -> QRectF:
        if self.original is None:
            return QRectF()
        available = self.rect().adjusted(12, 12, -12, -12)
        image_size = self.original.size()
        scale = min(available.width() / max(image_size.width(), 1),
                    available.height() / max(image_size.height(), 1)) * self.zoom
        width = image_size.width() * scale
        height = image_size.height() * scale
        left = available.center().x() - width / 2 + self.pan.x()
        top = available.center().y() - height / 2 + self.pan.y()
        return QRectF(left, top, width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#10262c'))
        if self.original is None and not self.busy:
            painter.setPen(QColor('#9ab1b6'))
            painter.setFont(QFont('Microsoft YaHei UI', 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '选择 LUT 后在这里预览')
            return
        rect = self._image_rect()
        if rect.isEmpty() and not self.busy:
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.mode == 'before' and self.original is not None:
            painter.drawPixmap(rect, self.original, QRectF(self.original.rect()))
        elif self.mode == 'after' and self.processed is not None:
            painter.drawPixmap(rect, self.processed, QRectF(self.processed.rect()))
        elif self.original is not None:
            painter.drawPixmap(rect, self.original, QRectF(self.original.rect()))
            split_x = rect.left() + rect.width() * self.split
            if self.processed is not None:
                painter.save()
                painter.setClipRect(QRectF(split_x, rect.top(), rect.right() - split_x, rect.height()))
                painter.drawPixmap(rect, self.processed, QRectF(self.processed.rect()))
                painter.restore()
            pen = QPen(QColor(255, 255, 255, 230), 2)
            painter.setPen(pen)
            painter.drawLine(split_x, rect.top(), split_x, rect.bottom())
            painter.setBrush(QColor(21, 143, 147))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(split_x - 15, rect.center().y() - 22, 30, 44), 15, 15)
            painter.setPen(QPen(QColor('white'), 2))
            painter.drawLine(split_x - 7, rect.center().y(), split_x + 7, rect.center().y())
            self._draw_label(painter, '原图', QPoint(round(rect.left()) + 16, round(rect.top()) + 16))
            self._draw_label(painter, 'LUT', QPoint(round(rect.right()) - 55, round(rect.top()) + 16))
        if self.busy:
            painter.fillRect(self.rect(), QColor(10, 28, 33, 150))
            center = self.rect().center()
            circle = QRectF(center.x() - 20, center.y() - 28, 40, 40)
            pen = QPen(QColor('#62d4d0'), 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(circle, -self._spinner_angle * 16, 275 * 16)
            painter.setPen(QColor('#e8f7f7'))
            painter.setFont(QFont('Microsoft YaHei UI', 10, QFont.Weight.DemiBold))
            painter.drawText(QRectF(self.rect().left(), center.y() + 20, self.rect().width(), 28),
                             Qt.AlignmentFlag.AlignCenter, self.busy_text)

    @staticmethod
    def _draw_label(painter: QPainter, text: str, point: QPoint):
        painter.setFont(QFont('Microsoft YaHei UI', 9, QFont.Weight.DemiBold))
        metrics = painter.fontMetrics()
        box = QRectF(point.x() - 8, point.y() - 5, metrics.horizontalAdvance(text) + 16,
                     metrics.height() + 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(box, 7, 7)
        painter.setPen(QColor('white'))
        painter.drawText(box.adjusted(8, 5, -8, -5), Qt.AlignmentFlag.AlignCenter, text)

    def _split_x(self) -> float:
        rect = self._image_rect()
        return rect.left() + rect.width() * self.split

    def mousePressEvent(self, event):
        position = event.position()
        if (self.mode == 'split' and event.button() == Qt.MouseButton.LeftButton
                and abs(position.x() - self._split_x()) <= 28):
            self._dragging_split = True
            self.setCursor(Qt.CursorShape.SplitHCursor)
            return
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._panning = True
            self._last_mouse = position.toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        position = event.position()
        if self._dragging_split:
            rect = self._image_rect()
            if rect.width() > 0:
                self.split = max(0.02, min(0.98, (position.x() - rect.left()) / rect.width()))
                self.update()
            return
        if self._panning:
            current = position.toPoint()
            self.pan += current - self._last_mouse
            self._last_mouse = current
            self.update()
            return
        if self.mode == 'split' and abs(position.x() - self._split_x()) <= 28:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._dragging_split = False
        self._panning = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, event):
        self.reset_view()

    def wheelEvent(self, event):
        if self.original is None:
            return
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        self.zoom = max(0.25, min(12.0, self.zoom * factor))
        self.update()


class LutBlendDialog(QDialog):
    """Two-source sRGB blend editor; slider changes never compile native tables."""

    def __init__(self, workspace, edit_key: str | None = None):
        super().__init__(workspace)
        self.workspace = workspace
        self.backend = workspace.backend
        self.card = workspace.card
        self.identity = workspace.identity
        self.edit_key = edit_key
        self.original_array = workspace.original_array
        self.preview_image_path = workspace.preview_image_path
        self.a_lut: Lut3D | None = None
        self.b_lut: Lut3D | None = None
        self.pair_generation = 0
        self.preview_generation = 0
        self.pair_task: BlendPairTask | None = None
        self.preview_task: BlendPreviewTask | None = None
        self.pair_ok = False
        self.workers = set()
        self.dirty = False
        self.busy = False
        self.saved_key: str | None = None
        self.metadata = (self.backend.look_metadata(self.card, edit_key)
                         if edit_key else {})
        self.recipe = self.metadata.get('blend_recipe', {}) if edit_key else {}
        if edit_key and self.recipe.get('algorithm') != 'linear-srgb-output-v1':
            raise ValueError('该 LUT 的混合算法版本不可编辑。')
        self.candidates = [item for item in self.backend.blend_sources(self.card)
                           if item['valid']]
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.setWindowTitle('编辑混合风格' if edit_key else '创建混合风格')
        self.resize(1080, 780)
        self.setMinimumSize(900, 680)
        self._build_ui()
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.schedule_preview)
        self._load_initial_selection()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        title = QLabel('编辑混合配方' if self.edit_key else '创建混合风格')
        title.setObjectName('pageTitle')
        root.addWidget(title)
        note = QLabel('按 A/B 输出比例融合两款 sRGB LUT。滑块只更新 33³ 预览；保存时才生成相机用 9³ 表。')
        note.setObjectName('muted')
        note.setWordWrap(True)
        root.addWidget(note)

        selectors = QHBoxLayout()
        self.a_combo = QComboBox()
        self.b_combo = QComboBox()
        self.a_combo.setMinimumWidth(330)
        self.b_combo.setMinimumWidth(330)
        for combo in (self.a_combo, self.b_combo):
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.lineEdit().setPlaceholderText('搜索分类或 LUT 名称')
            combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        selectors.addWidget(QLabel('A'))
        selectors.addWidget(self.a_combo, 1)
        self.swap_button = QPushButton('交换 A/B')
        self.swap_button.clicked.connect(self.swap_sources)
        selectors.addWidget(self.swap_button)
        selectors.addWidget(QLabel('B'))
        selectors.addWidget(self.b_combo, 1)
        root.addLayout(selectors)

        self.viewer = LutPreviewViewer()
        root.addWidget(self.viewer, 1)
        modes = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for index, (label, mode) in enumerate((('对比', 'split'), ('原图', 'before'), ('混合结果', 'after'))):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, value=mode: self.viewer.set_mode(value))
            if index == 0:
                button.setChecked(True)
            self.mode_group.addButton(button)
            modes.addWidget(button)
        self.photo_button = QPushButton('更换参考照片')
        self.photo_button.clicked.connect(self.choose_photo)
        modes.addWidget(self.photo_button)
        modes.addStretch()
        reset = QPushButton('适配窗口')
        reset.clicked.connect(self.viewer.reset_view)
        modes.addWidget(reset)
        root.addLayout(modes)

        ratio_row = QHBoxLayout()
        ratio_row.addWidget(QLabel('混合比例'))
        self.ratio = QSlider(Qt.Orientation.Horizontal)
        self.ratio.setRange(0, 100)
        self.ratio.setValue(50)
        self.ratio.setTickInterval(10)
        self.ratio.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.ratio.valueChanged.connect(self._ratio_changed)
        ratio_row.addWidget(self.ratio, 1)
        self.ratio_label = QLabel('A 50% · B 50%')
        self.ratio_label.setMinimumWidth(150)
        ratio_row.addWidget(self.ratio_label)
        root.addLayout(ratio_row)

        form = QHBoxLayout()
        form.addWidget(QLabel('相机名称'))
        self.name = QLineEdit()
        self.name.setPlaceholderText('例如：青橙胶片 70:30')
        self.name.setMaxLength(60)
        if self.edit_key:
            self.name.setText(str(self.metadata.get('label', '')))
        else:
            current = self.workspace.current_key
            label = next((record['label'] for record in self.workspace.records
                          if record['key'] == current), '混合风格')
            self.name.setText(label + ' · 混合')
        self.name.textChanged.connect(self._mark_dirty)
        form.addWidget(self.name, 1)
        root.addLayout(form)

        self.status = QLabel('正在读取 A/B 源 LUT…')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        footer = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(180)
        self.progress.hide()
        footer.addWidget(self.progress)
        footer.addStretch()
        self.cancel_button = QPushButton('取消')
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.export_button = QPushButton('导出 .cube')
        self.export_button.clicked.connect(self.export_master)
        footer.addWidget(self.export_button)
        self.save_button = QPushButton('保存修改' if self.edit_key else '保存到卡')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self.save)
        footer.addWidget(self.save_button)
        root.addLayout(footer)

    def _load_initial_selection(self):
        keys = {item['id'] for item in self.candidates}
        for combo in (self.a_combo, self.b_combo):
            combo.blockSignals(True)
            combo.clear()
            for item in self.candidates:
                suffix = '' if item['original'] else f'  ·  {item["grid"] or "?"}³'
                combo.addItem(item['label'] + suffix, item['id'])
        if self.edit_key:
            a_id = self.recipe.get('a', {}).get('id', '__original__')
            b_id = self.recipe.get('b', {}).get('id', '__original__')
            for combo, source in ((self.a_combo, self.recipe.get('a', {})),
                                  (self.b_combo, self.recipe.get('b', {}))):
                if combo.findData(source.get('id')) < 0:
                    combo.addItem(source.get('label', source.get('id', '已删除源')) + '  ·  快照',
                                  source.get('id'))
            self.a_combo.setCurrentIndex(max(0, self.a_combo.findData(a_id)))
            self.b_combo.setCurrentIndex(max(0, self.b_combo.findData(b_id)))
            self.ratio.setValue(round(float(self.recipe.get('amount_b', 0.5)) * 100))
            self.a_combo.setEnabled(False)
            self.b_combo.setEnabled(False)
            self.swap_button.setEnabled(False)
            self.status.setText('正在读取保存的 A/B 源快照…')
        else:
            valid = [item['id'] for item in self.candidates]
            preferred = 'card:' + self.workspace.current_key if self.workspace.current_key else None
            non_original = [item for item in valid if item != '__original__']
            a_id = (preferred if preferred in keys else
                    (non_original[0] if non_original else ('__original__' if '__original__' in keys else valid[0])))
            remaining = [item for item in valid if item not in (a_id, '__original__')]
            remaining += [item for item in valid if item not in remaining and item != a_id]
            b_id = remaining[0] if remaining else a_id
            self.a_combo.setCurrentIndex(max(0, self.a_combo.findData(a_id)))
            self.b_combo.setCurrentIndex(max(0, self.b_combo.findData(b_id)))
        self.a_combo.blockSignals(False)
        self.b_combo.blockSignals(False)
        self.a_combo.currentIndexChanged.connect(self._source_changed)
        self.b_combo.currentIndexChanged.connect(self._source_changed)
        self._ratio_changed(self.ratio.value(), mark_dirty=False)
        self._source_changed(mark_dirty=False)
        self.dirty = False

    def _mark_dirty(self, *_):
        self.dirty = True

    def _ratio_changed(self, value, *, mark_dirty=True):
        self.ratio_label.setText(f'A {100 - value}% · B {value}%')
        if mark_dirty:
            self.dirty = True
        if self.a_lut is not None and self.b_lut is not None:
            self.preview_timer.start(140)

    def swap_sources(self):
        a_index, b_index = self.a_combo.currentIndex(), self.b_combo.currentIndex()
        self.a_combo.blockSignals(True)
        self.b_combo.blockSignals(True)
        self.a_combo.setCurrentIndex(b_index)
        self.b_combo.setCurrentIndex(a_index)
        self.a_combo.blockSignals(False)
        self.b_combo.blockSignals(False)
        self.ratio.setValue(100 - self.ratio.value())
        self._source_changed()

    def _source_path(self, key: str) -> Path | None:
        if key == '__original__':
            return None
        item = next((item for item in self.candidates if item['id'] == key), None)
        if item is None or not item.get('path'):
            return None
        return Path(item['path'])

    def _source_changed(self, *_args, mark_dirty=True):
        self.a_lut = None
        self.b_lut = None
        self.pair_ok = False
        self.save_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.pair_generation += 1
        generation = self.pair_generation
        if self.pair_task is not None:
            self.pair_task.cancelled = True
        if self.edit_key:
            directory = self.card / 'nx300m-mod/looks' / self.edit_key
            a_path = directory / self.recipe.get('a', {}).get('snapshot', 'sources/a.cube')
            b_path = directory / self.recipe.get('b', {}).get('snapshot', 'sources/b.cube')
        else:
            a_path = self._source_path(self.a_combo.currentData())
            b_path = self._source_path(self.b_combo.currentData())
        if mark_dirty:
            self.dirty = True
        self.viewer.set_busy(True, '正在读取 LUT 源…')
        self.status.setText('正在读取 A/B 源 LUT…')
        task = BlendPairTask(generation, a_path, b_path)
        task.setAutoDelete(False)
        self.pair_task = task
        task.signals.result.connect(self._pair_ready)
        task.signals.error.connect(self._pair_error)
        task.signals.finished.connect(self._pair_finished)
        self.pool.start(task)

    def _pair_ready(self, generation, a, b):
        if generation != self.pair_generation:
            return
        self.a_lut, self.b_lut = a, b
        self.pair_ok = True
        self.save_button.setEnabled(not self.busy)
        self.export_button.setEnabled(not self.busy)
        self.status.setText('混合源已读取，正在生成 33³ 预览…')
        self.preview_timer.start(0)

    def _pair_error(self, generation, message):
        if generation != self.pair_generation:
            return
        self.viewer.set_busy(False)
        self.status.setText('无法读取混合源：' + message)
        self.save_button.setEnabled(False)
        self.export_button.setEnabled(False)

    def _pair_finished(self, generation):
        if generation == self.pair_generation:
            self.pair_task = None

    def schedule_preview(self):
        if self.a_lut is None or self.b_lut is None or self.busy:
            return
        self.preview_generation += 1
        generation = self.preview_generation
        if self.preview_task is not None:
            self.preview_task.cancelled = True
        self.viewer.set_busy(True, '正在更新 33³ 预览…')
        self.status.setText('正在更新 33³ 预览…')
        task = BlendPreviewTask(generation, self.original_array, self.a_lut, self.b_lut,
                                self.ratio.value() / 100.0, self.preview_image_path)
        task.setAutoDelete(False)
        self.preview_task = task
        task.signals.result.connect(self._preview_ready)
        task.signals.error.connect(self._preview_error)
        task.signals.finished.connect(self._preview_finished)
        self.pool.start(task)

    def _preview_ready(self, generation, before, after):
        if generation != self.preview_generation:
            return
        self.original_array = before
        self.viewer.set_images(before, after)
        self.viewer.set_busy(False)
        self.status.setText('33³ master 预览。相机保存后会编译为 9³ 机内近似。')

    def _preview_error(self, generation, message):
        if generation == self.preview_generation:
            self.viewer.set_busy(False)
            self.status.setText('预览失败：' + message)

    def _preview_finished(self, generation):
        if generation == self.preview_generation:
            self.preview_task = None

    def choose_photo(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, '选择参考照片', '', '图片 (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)')
        if filename:
            self.preview_image_path = Path(filename)
            self.original_array = None
            self.dirty = True
            self.schedule_preview()

    def _set_busy(self, busy, message=''):
        self.busy = busy
        self.progress.setVisible(busy)
        for widget in (self.a_combo, self.b_combo, self.swap_button, self.ratio,
                       self.photo_button, self.name, self.export_button, self.save_button,
                       self.cancel_button):
            widget.setEnabled(not busy and not (
                self.edit_key and widget in (self.a_combo, self.b_combo, self.swap_button)))
        self.export_button.setEnabled(not busy and self.pair_ok)
        self.save_button.setEnabled(not busy and self.pair_ok)
        if message:
            self.status.setText(message)

    def save(self):
        if self.a_lut is None or self.b_lut is None or self.busy:
            return
        label = self.name.text().strip()
        if not label or any(char in '|\r\n\0' or ord(char) < 32 for char in label):
            QMessageBox.warning(self, '混合风格名称', '请输入可写入相机目录的名称。')
            return
        ratio = self.ratio.value() / 100.0
        a_key = self.a_combo.currentData()
        b_key = self.b_combo.currentData()
        self._set_busy(True, '正在编译两张 9³ 相机表并回读…')
        if self.edit_key:
            function = lambda: self.backend.update_blended_look(
                self.card, self.identity, self.edit_key, ratio, label, self.preview_image_path)
        else:
            function = lambda: self.backend.create_blended_look(
                self.card, self.identity, a_key, b_key, ratio, label, self.preview_image_path)
        worker = Worker(function)
        worker.setAutoDelete(False)
        self.workers.add(worker)
        worker.signals.result.connect(self._saved)
        worker.signals.error.connect(self._save_error)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        worker.signals.finished.connect(lambda worker=worker: self.workers.discard(worker))
        QThreadPool.globalInstance().start(worker)

    def _saved(self, result):
        self.saved_key = result['id']
        self.dirty = False
        self._stop_preview_tasks()
        self.workspace.refresh_library()
        self.workspace.select_key(self.saved_key)
        message = f'混合风格“{result["label"]}”已保存到卡，默认归入“自制”。'
        local = result.get('local_repository')
        if local and local.get('path'):
            message += f' 本地仓库：{local["path"]}'
        elif local and local.get('error'):
            message += f' 本地仓库写入失败：{local["error"]}'
        self.workspace.status.setText(message)
        self.workspace.log(message + f' 卡内备份：{result["backup"]}')
        self.accept()

    def _save_error(self, message):
        self._set_busy(False)
        self.status.setText('保存失败：' + message)
        QMessageBox.critical(self, '混合风格保存失败', message)

    def export_master(self):
        if self.a_lut is None or self.b_lut is None or self.busy:
            return
        suggested = (self.name.text().strip() or '混合 LUT') + '.cube'
        destination, _ = QFileDialog.getSaveFileName(
            self, '导出 33³ 混合 LUT', suggested, '3D LUT (*.cube)')
        if not destination:
            return
        target = Path(destination)
        ratio = self.ratio.value() / 100.0
        a_lut = self.a_lut
        b_lut = self.b_lut
        title = self.name.text().strip() or 'Blended LUT'
        self._set_busy(True, '正在导出 33³ master…')

        def export():
            data = cube_bytes(blend_luts(a_lut, b_lut, ratio), title=title)
            pending = target.with_name(target.name + '.tmp')
            pending.write_bytes(data)
            os.replace(pending, target)
            return target

        worker = Worker(export)
        worker.setAutoDelete(False)
        self.workers.add(worker)
        worker.signals.result.connect(lambda path: self.status.setText(f'已导出 {path}'))
        worker.signals.error.connect(self._save_error)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        worker.signals.finished.connect(lambda worker=worker: self.workers.discard(worker))
        QThreadPool.globalInstance().start(worker)

    def reject(self):
        if self.busy:
            return
        if self.dirty and QMessageBox.question(
                self, '放弃混合配方？', '当前 A/B 比例和预览尚未保存到相机卡。') != QMessageBox.StandardButton.Yes:
            return
        self._stop_preview_tasks()
        super().reject()

    def _stop_preview_tasks(self):
        for task in (self.pair_task, self.preview_task):
            if task is not None:
                task.cancelled = True
        self.preview_timer.stop()
        self.pool.clear()
        self.pool.waitForDone()


class LutWorkspace(QWidget):
    def __init__(self, backend, card_provider, log, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.card_provider = card_provider
        self.log = log
        self.card = None
        self.identity = None
        self.records: list[dict] = []
        self.group_state = {'groups': [], 'members': {}}
        self.current_key = None
        self.preview_image_path: Path | None = None
        self.original_array: np.ndarray | None = None
        self.current_lut: Lut3D | None = None
        self.preview_generation = 0
        self.lut_load_generation = 0
        self.lut_cache: dict[str, Lut3D] = {}
        self.metadata_cache: dict[str, dict] = {}
        self.lut_load_tasks: dict[int, LutLoadTask] = {}
        self.preview_tasks: dict[int, PreviewTask] = {}
        self.preview_pool = QThreadPool(self)
        self.preview_pool.setMaxThreadCount(2)
        self.preview_pixel_cache: OrderedDict[tuple[str, float, str], np.ndarray] = OrderedDict()
        self.preview_cache_keys: dict[int, tuple[str, float, str]] = {}
        self.workers = set()
        self._build_ui()
        self.strength_timer = QTimer(self)
        self.strength_timer.setSingleShot(True)
        self.strength_timer.timeout.connect(self.schedule_preview)
        self.setAcceptDrops(True)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel('LUT 工作台')
        title.setObjectName('pageTitle')
        subtitle = QLabel('在真实照片上比较效果，批量导入、整理和备份卡内风格。')
        subtitle.setObjectName('muted')
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.count_chip = QLabel('0 款')
        self.count_chip.setObjectName('countChip')
        header.addWidget(self.count_chip)
        self.import_button = QPushButton('导入 LUT')
        self.import_button.setObjectName('primary')
        self.import_button.clicked.connect(self.choose_lut_files)
        header.addWidget(self.import_button)
        self.folder_button = QPushButton('导入文件夹')
        self.folder_button.clicked.connect(self.choose_lut_folder)
        header.addWidget(self.folder_button)
        self.builtin_button = QPushButton('添加内置风格')
        self.builtin_button.clicked.connect(self.add_builtins)
        header.addWidget(self.builtin_button)
        self.blend_button = QPushButton('创建混合风格')
        self.blend_button.setObjectName('primary')
        self.blend_button.clicked.connect(self.open_blend_editor)
        header.addWidget(self.blend_button)
        root.addLayout(header)

        toolbar = QFrame()
        toolbar.setObjectName('toolbarCard')
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 11, 14, 11)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索名称或 ID')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        toolbar_layout.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(['全部', '收藏', 'Lumix Lab', '内置', '导入', '其他'])
        self.filter.currentTextChanged.connect(self.apply_filter)
        toolbar_layout.addWidget(self.filter)
        self.group_filter = QComboBox()
        self.group_filter.addItem('全部分组', None)
        self.group_filter.addItem('未分组', '__ungrouped__')
        self.group_filter.currentIndexChanged.connect(self.apply_filter)
        toolbar_layout.addWidget(self.group_filter)
        self.group_button = QPushButton('分类管理')
        self.group_button.clicked.connect(self.manage_groups)
        toolbar_layout.addWidget(self.group_button)
        self.batch_group_button = QPushButton('调整分类')
        self.batch_group_button.setObjectName('primary')
        self.batch_group_button.clicked.connect(self.assign_selected_groups)
        toolbar_layout.addWidget(self.batch_group_button)
        self.batch_favorite_button = QPushButton('收藏选中')
        self.batch_favorite_button.clicked.connect(self.toggle_selected_favorites)
        toolbar_layout.addWidget(self.batch_favorite_button)
        self.select_visible_button = QPushButton('全选结果')
        self.select_visible_button.clicked.connect(self.select_visible)
        toolbar_layout.addWidget(self.select_visible_button)
        refresh = QPushButton('刷新')
        refresh.clicked.connect(self.refresh_library)
        toolbar_layout.addWidget(refresh)
        root.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['', '名称', '分类', '来源', '网格', '状态'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.cellClicked.connect(self.cell_clicked)
        splitter.addWidget(self.table)

        detail = QFrame()
        detail.setObjectName('detailPanel')
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        preview_header = QHBoxLayout()
        self.preview_name = QLabel('选择一个 LUT')
        self.preview_name.setObjectName('sectionTitle')
        preview_header.addWidget(self.preview_name)
        preview_header.addStretch()
        self.photo_button = QPushButton('更换照片')
        self.photo_button.clicked.connect(self.choose_preview_image)
        preview_header.addWidget(self.photo_button)
        detail_layout.addLayout(preview_header)
        self.viewer = LutPreviewViewer()
        detail_layout.addWidget(self.viewer, 1)

        modes = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for index, (label, mode) in enumerate((('对比', 'split'), ('原图', 'before'), ('效果', 'after'))):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty('mode', mode)
            button.clicked.connect(lambda checked=False, value=mode: self.viewer.set_mode(value))
            if index == 0:
                button.setChecked(True)
            self.mode_group.addButton(button)
            modes.addWidget(button)
        reset = QPushButton('适配窗口')
        reset.clicked.connect(self.viewer.reset_view)
        modes.addWidget(reset)
        detail_layout.addLayout(modes)

        strength_row = QHBoxLayout()
        strength_row.addWidget(QLabel('预览强度'))
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0, 100)
        self.strength.setValue(100)
        self.strength.valueChanged.connect(lambda _: self.strength_timer.start(100))
        strength_row.addWidget(self.strength, 1)
        self.strength_value = QLabel('100%')
        self.strength.valueChanged.connect(lambda value: self.strength_value.setText(f'{value}%'))
        strength_row.addWidget(self.strength_value)
        detail_layout.addLayout(strength_row)

        self.metadata_label = QLabel('选择风格后显示来源、输入域和原生表信息。')
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setObjectName('muted')
        detail_layout.addWidget(self.metadata_label)
        actions = QHBoxLayout()
        self.favorite_button = QPushButton('收藏')
        self.favorite_button.clicked.connect(self.toggle_favorite)
        actions.addWidget(self.favorite_button)
        self.rename_button = QPushButton('重命名')
        self.rename_button.clicked.connect(self.rename_current)
        actions.addWidget(self.rename_button)
        self.export_button = QPushButton('导出 .cube')
        self.export_button.clicked.connect(self.export_current)
        actions.addWidget(self.export_button)
        self.edit_blend_button = QPushButton('编辑混合配方')
        self.edit_blend_button.clicked.connect(self.edit_blend)
        self.edit_blend_button.hide()
        actions.addWidget(self.edit_blend_button)
        self.assign_group_button = QPushButton('调整分类')
        self.assign_group_button.clicked.connect(self.assign_selected_groups)
        actions.addWidget(self.assign_group_button)
        self.remove_button = QPushButton('删除并备份')
        self.remove_button.setObjectName('danger')
        self.remove_button.clicked.connect(self.delete_current)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        detail_layout.addLayout(actions)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([570, 650])
        root.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        self.status = QLabel('请选择相机存储卡。')
        self.status.setObjectName('muted')
        bottom.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(180)
        self.progress.hide()
        bottom.addWidget(self.progress)
        root.addLayout(bottom)

    def set_card(self, card, identity):
        self.card = card
        self.identity = identity
        self.current_key = None
        self.current_lut = None
        self.edit_blend_button.hide()
        self.lut_cache.clear()
        self.metadata_cache.clear()
        self.preview_pixel_cache.clear()
        self.preview_cache_keys.clear()
        for task in self.lut_load_tasks.values():
            task.cancelled = True
        for task in self.preview_tasks.values():
            task.cancelled = True
        for pending in self.lut_load_tasks.values():
            pending.cancelled = True
        self.lut_load_generation += 1
        self.preview_generation += 1
        self.refresh_library()

    def _set_busy(self, busy: bool, message: str | None = None):
        self.progress.setVisible(busy)
        for button in (self.import_button, self.folder_button, self.builtin_button,
                       self.blend_button, self.edit_blend_button,
                       self.favorite_button, self.rename_button, self.export_button,
                       self.assign_group_button, self.group_button, self.batch_group_button,
                       self.batch_favorite_button, self.select_visible_button, self.remove_button):
            button.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.search.setEnabled(not busy)
        self.filter.setEnabled(not busy)
        self.group_filter.setEnabled(not busy)
        if message is not None:
            self.status.setText(message)

    def _run(self, function, success, *, busy_message='处理中…'):
        self._set_busy(True, busy_message)
        worker = Worker(function)
        worker.setAutoDelete(False)
        self.workers.add(worker)
        worker.signals.result.connect(success)
        worker.signals.error.connect(self._operation_error)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        worker.signals.finished.connect(lambda worker=worker: self.workers.discard(worker))
        QThreadPool.globalInstance().start(worker)

    def _operation_error(self, message):
        self.refresh_library()
        self.status.setText('操作失败：' + message)
        self.log('LUT 操作失败：' + message)

    def _refresh_group_choices(self):
        previous = self.group_filter.currentData()
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem('全部分组', None)
        self.group_filter.addItem('未分组', '__ungrouped__')
        for group in self.group_state['groups']:
            self.group_filter.addItem(group['label'], group['id'])
        index = self.group_filter.findData(previous)
        self.group_filter.setCurrentIndex(index if index >= 0 else 0)
        self.group_filter.blockSignals(False)

    def refresh_library(self):
        if self.card is None:
            self.table.setRowCount(0)
            self.records = []
            self.group_state = {'groups': [], 'members': {}}
            self._refresh_group_choices()
            self.count_chip.setText('0 款')
            self.status.setText('请选择相机存储卡。')
            return
        try:
            _, rows = self.backend.catalog(self.card)
            favorites = self.backend.favorite_keys(self.card)
            self.group_state = self.backend.lut_groups(self.card)
        except Exception as error:
            self.status.setText('无法读取 LUT：' + str(error))
            return
        self._refresh_group_choices()
        previous = self.current_key
        records = []
        metadata_errors = 0
        for row in rows:
            try:
                metadata = self.metadata_cache.get(row[0])
                if metadata is None:
                    metadata = self.backend.look_metadata(self.card, row[0])
                    self.metadata_cache[row[0]] = metadata
            except Exception as error:
                metadata = {'read_error': str(error)}
                metadata_errors += 1
            kind = metadata.get('source_kind', '')
            if row[0].startswith('panasonic-lumix') or 'lumix' in kind:
                filter_kind, source = 'lumix', 'Lumix Lab'
            elif kind == 'original-procedural' or row[0].startswith(('nx300-', 'metol-', 'ricoh-')):
                filter_kind, source = 'builtin', '内置'
            elif row[0].startswith('import-') or kind in ('user-cube', 'user-lumix'):
                filter_kind, source = 'import', '导入'
            else:
                filter_kind, source = 'other', '其他'
            records.append({
                'key': row[0], 'label': row[1], 'favorite': row[0] in favorites,
                'source': source, 'filter': filter_kind,
                'grid': metadata.get('source_grid', metadata.get('native_format', {}).get('grid', '—')),
                'groups': tuple(self.group_state['members'].get(row[0], [])),
                'metadata': metadata,
            })
        self.records = records
        self.table.blockSignals(True)
        self.table.setRowCount(len(records))
        for index, record in enumerate(records):
            star = QTableWidgetItem('★' if record['favorite'] else '☆')
            star.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name = QTableWidgetItem(record['label'])
            name.setToolTip(record['key'])
            group_names = [
                group['label'] for group in self.group_state['groups']
                if group['id'] in record['groups']
            ]
            group = QTableWidgetItem('、'.join(group_names) or '未分类')
            source = QTableWidgetItem(record['source'])
            grid = QTableWidgetItem(str(record['grid']))
            status_bits = []
            if record['favorite']:
                status_bits.append('收藏')
            if record['groups']:
                status_bits.append(f'{len(record["groups"])} 组')
            status = QTableWidgetItem(' · '.join(status_bits) or '未分组')
            for column, item in enumerate((star, name, group, source, grid, status)):
                item.setData(Qt.ItemDataRole.UserRole, record['key'])
                self.table.setItem(index, column, item)
        self.table.blockSignals(False)
        self.count_chip.setText(f'{len(records)} 款')
        self.apply_filter()
        restored = self.select_key(previous) if previous else False
        visible = [row for row in range(len(records)) if not self.table.isRowHidden(row)]
        if not restored and visible:
            self.table.selectRow(visible[0])
        elif not visible:
            self.viewer.original = None
            self.viewer.processed = None
            self.viewer.update()
        suffix = f' 有 {metadata_errors} 款元数据损坏。' if metadata_errors else ''
        self.status.setText(
            f'已读取 {len(records)} 款 LUT。Ctrl/Shift 可多选，再批量分类或收藏。{suffix}')

    def apply_filter(self):
        query = self.search.text().strip().casefold()
        category = self.filter.currentText()
        group_filter = self.group_filter.currentData()
        mapping = {'收藏': 'favorite', 'Lumix Lab': 'lumix', '内置': 'builtin',
                   '导入': 'import', '其他': 'other'}
        visible_rows = []
        for row, record in enumerate(self.records):
            visible = (not query or query in record['label'].casefold()
                       or query in record['key'].casefold())
            if category == '收藏':
                visible = visible and record['favorite']
            elif category in mapping:
                visible = visible and record['filter'] == mapping[category]
            if group_filter == '__ungrouped__':
                visible = visible and not record['groups']
            elif group_filter:
                visible = visible and group_filter in record['groups']
            self.table.setRowHidden(row, not visible)
            if visible:
                visible_rows.append(row)
        selected = self.table.selectionModel().selectedRows()
        if selected and selected[0].row() not in visible_rows and visible_rows:
            self.table.selectRow(visible_rows[0])
        visible_count = len(visible_rows)
        total = len(self.records)
        self.count_chip.setText(
            f'{visible_count} / {total} 款' if visible_count != total else f'{total} 款')

    def select_key(self, key: str) -> bool:
        for row, record in enumerate(self.records):
            if record['key'] == key:
                if self.table.isRowHidden(row):
                    return False
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 1))
                return True
        return False

    def selected_keys(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        keys = []
        for row in rows:
            item = self.table.item(row, 1)
            if item is not None:
                key = item.data(Qt.ItemDataRole.UserRole)
                if key and key not in keys:
                    keys.append(key)
        return keys

    def select_visible(self):
        self.table.clearSelection()
        rows = [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]
        model = self.table.model()
        selection = QItemSelection()
        for row in rows:
            selection.select(model.index(row, 0), model.index(row, self.table.columnCount() - 1))
        self.table.selectionModel().select(
            selection, QItemSelectionModel.SelectionFlag.ClearAndSelect |
            QItemSelectionModel.SelectionFlag.Rows)
        self.status.setText(f'已选中当前筛选结果 {len(rows)} 款。')

    def selection_changed(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        key_item = self.table.item(rows[0].row(), 1)
        if key_item is None:
            return
        key = key_item.data(Qt.ItemDataRole.UserRole)
        if key == self.current_key:
            return
        self.current_key = key
        record = next(item for item in self.records if item['key'] == key)
        metadata = record['metadata']
        self.preview_name.setText(record['label'])
        self.favorite_button.setText('取消收藏' if record['favorite'] else '收藏')
        parts = [f'ID  {key}', f'来源  {record["source"]}']
        if metadata.get('source_style'):
            parts.append('基底  ' + str(metadata['source_style']))
        if metadata.get('source_grid'):
            parts.append(f'源网格  {metadata["source_grid"]}³')
        if metadata.get('source_name'):
            parts.append('文件  ' + str(metadata['source_name']))
        group_names = [
            group['label'] for group in self.group_state['groups']
            if group['id'] in record['groups']
        ]
        parts.append('分组  ' + ('、'.join(group_names) if group_names else '未分组'))
        self.metadata_label.setText('   ·   '.join(parts))
        self.edit_blend_button.setVisible(metadata.get('source_kind') == 'blend-linear-srgb')
        self.edit_blend_button.setEnabled(metadata.get('source_kind') == 'blend-linear-srgb')
        self.lut_load_generation += 1
        self.preview_generation += 1
        generation = self.lut_load_generation
        cached = self.lut_cache.get(key)
        if cached is not None:
            self.current_lut = cached
            self.schedule_preview()
            return
        self.current_lut = None
        self.viewer.set_busy(True, "正在加载 LUT…")
        self.status.setText("正在加载 LUT…")
        task = LutLoadTask(generation, key, self.card / "nx300m-mod/looks" / key, metadata)
        task.setAutoDelete(False)
        self.lut_load_tasks[generation] = task
        task.signals.result.connect(self._lut_ready)
        task.signals.error.connect(self._lut_error)
        task.signals.finished.connect(self._lut_load_finished)
        self.preview_pool.start(task)

    def _lut_ready(self, generation, key, lut):
        if generation != self.lut_load_generation or key != self.current_key:
            return
        self.lut_cache[key] = lut
        self.current_lut = lut
        self.schedule_preview()

    def _lut_error(self, generation, key, message):
        if generation != self.lut_load_generation or key != self.current_key:
            return
        self.current_lut = None
        self.viewer.original = None
        self.viewer.processed = None
        self.viewer.set_busy(False)
        self.status.setText("无法预览：" + message)

    def _lut_load_finished(self, generation):
        self.lut_load_tasks.pop(generation, None)

    def schedule_preview(self):
        if self.current_lut is None or self.card is None:
            return
        for pending in self.preview_tasks.values():
            pending.cancelled = True
        self.preview_generation += 1
        generation = self.preview_generation
        strength = self.strength.value() / 100.0
        cache_key = (self.current_key or '', strength,
                     str(self.preview_image_path) if self.preview_image_path else '')
        cached = self.preview_pixel_cache.get(cache_key)
        if cached is not None and self.original_array is not None:
            self.preview_pixel_cache.move_to_end(cache_key)
            self.viewer.set_images(self.original_array, cached)
            self.viewer.set_busy(False)
            self.status.setText('预览已命中缓存。')
            return
        self.viewer.set_busy(True, "正在生成预览…")
        task = PreviewTask(generation, self.original_array, self.current_lut, strength,
                           self.preview_image_path)
        task.setAutoDelete(False)
        self.preview_tasks[generation] = task
        self.preview_cache_keys[generation] = cache_key
        task.signals.result.connect(lambda number, before, after: self._preview_ready(number, before, after))
        task.signals.error.connect(lambda number, message: self._preview_error(number, message))
        task.signals.finished.connect(self._preview_finished)
        self.status.setText("正在生成预览…")
        self.preview_pool.start(task)

    def _preview_finished(self, generation):
        self.preview_tasks.pop(generation, None)
        if generation != self.preview_generation:
            self.preview_cache_keys.pop(generation, None)

    def _preview_ready(self, generation, before, after):
        if generation != self.preview_generation:
            return
        self.original_array = before
        cache_key = self.preview_cache_keys.pop(generation, None)
        if cache_key is not None:
            self.preview_pixel_cache[cache_key] = after
            self.preview_pixel_cache.move_to_end(cache_key)
            while len(self.preview_pixel_cache) > 4:
                self.preview_pixel_cache.popitem(last=False)
        self.viewer.set_images(before, after)
        self.viewer.set_busy(False)
        self.status.setText("预览已更新。拖动分割线比较原图与 LUT。")

    def _preview_error(self, generation, message):
        self.preview_cache_keys.pop(generation, None)
        if generation == self.preview_generation:
            self.viewer.set_busy(False)
            self.status.setText("预览失败：" + message)

    def choose_preview_image(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, '选择预览照片', '', '图片 (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)')
        if filename:
            self.preview_image_path = Path(filename)
            self.original_array = None
            self.schedule_preview()

    def open_blend_editor(self):
        if self.card is None:
            QMessageBox.warning(self, 'LUT 混合', '请先选择相机存储卡。')
            return
        try:
            dialog = LutBlendDialog(self)
        except Exception as error:
            QMessageBox.critical(self, '无法创建混合风格', str(error))
            return
        dialog.exec()
        self.refresh_library()

    def edit_blend(self):
        if self.card is None or not self.current_key:
            return
        record = next((item for item in self.records if item['key'] == self.current_key), None)
        if record is None or record['metadata'].get('source_kind') != 'blend-linear-srgb':
            return
        try:
            dialog = LutBlendDialog(self, self.current_key)
        except Exception as error:
            QMessageBox.critical(self, '无法编辑混合配方', str(error))
            return
        dialog.exec()
        self.refresh_library()

    def choose_lut_files(self):
        filenames, _ = QFileDialog.getOpenFileNames(
            self, '导入 LUT', '', 'LUT (*.cube *.vlt *.zip)')
        if filenames:
            self.import_paths([Path(name) for name in filenames])

    def choose_lut_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择包含 LUT 的文件夹')
        if not folder:
            return
        paths = [path for path in Path(folder).rglob('*')
                 if path.is_file() and path.suffix.casefold() in ('.cube', '.vlt', '.zip')]
        self.import_paths(paths)

    def import_paths(self, paths):
        if self.card is None:
            QMessageBox.warning(self, 'LUT 导入', '请先选择相机存储卡。')
            return
        try:
            expanded = expand_lut_paths(list(paths))
        except Exception as error:
            QMessageBox.critical(self, 'LUT 校验失败', str(error))
            return
        if not expanded:
            QMessageBox.information(self, 'LUT 导入', '所选文件里没有可用 LUT。')
            return
        if QMessageBox.question(
                self, '导入 LUT',
                f'将检查并导入 {len(expanded)} 个 LUT。重复项和非 Standard 输入域会自动跳过。继续吗？'
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run(
            lambda: self.backend.import_lut_sources(self.card, self.identity, list(paths)),
            self._import_done, busy_message='正在编译 NX300M 原生表…')

    def _import_done(self, result):
        self.refresh_library()
        imported = len(result['imported'])
        skipped = result['skipped']
        message = f'已导入 {imported} 款。'
        if skipped:
            message += f' 跳过 {len(skipped)} 款。'
        if result.get('backup'):
            message += f' 备份：{result["backup"]}'
        self.status.setText(message)
        self.log(message)
        for item in skipped:
            self.log(f'跳过 {item["name"]}：{item["reason"]}')

    def add_builtins(self):
        if self.card is None:
            return
        self._run(lambda: self.backend.add_builtin_looks(self.card, self.identity),
                  lambda result: (self.refresh_library(),
                                  self.status.setText(f'已添加 {result[0]} 款内置风格。')),
                  busy_message='正在添加内置风格…')

    def cell_clicked(self, row, column):
        if column != 0:
            return
        key_item = self.table.item(row, 1)
        if key_item is None:
            return
        key = key_item.data(Qt.ItemDataRole.UserRole)
        self.select_key(key)
        self.toggle_favorite()

    def toggle_favorite(self):
        if not self.current_key:
            return
        key = self.current_key
        record = next(item for item in self.records if item['key'] == key)
        desired = not record['favorite']
        self._run(
            lambda: self.backend.set_favorites(self.card, self.identity, [key], desired),
            lambda _result: self._favorite_batch_done([key], desired),
            busy_message="正在更新收藏状态…")

    def toggle_selected_favorites(self):
        keys = self.selected_keys()
        if not keys:
            QMessageBox.information(self, '收藏 LUT', '请先选择至少一款 LUT。')
            return
        records = {record['key']: record for record in self.records}
        desired = not all(records[key]['favorite'] for key in keys)
        self._run(
            lambda: self.backend.set_favorites(self.card, self.identity, keys, desired),
            lambda _result: self._favorite_batch_done(keys, desired),
            busy_message="正在更新收藏状态…")

    def _favorite_batch_done(self, keys, desired):
        wanted = set(keys)
        for row, record in enumerate(self.records):
            if record['key'] not in wanted:
                continue
            record['favorite'] = desired
            star = self.table.item(row, 0)
            if star is not None:
                star.setText('★' if desired else '☆')
            status = self.table.item(row, 5)
            if status is not None:
                bits = []
                if desired:
                    bits.append('收藏')
                if record['groups']:
                    bits.append(f'{len(record["groups"])} 组')
                status.setText(' · '.join(bits) or '未分组')
        if self.current_key in wanted:
            self.favorite_button.setText('取消收藏' if desired else '收藏')
        self.apply_filter()
        self.select_key(keys[0])
        action = '收藏' if desired else '取消收藏'
        self.status.setText(
            f'已将 {len(keys)} 款 LUT {action}，相机面板读取同一收藏文件。')

    def _apply_group_membership(self, assignments):
        for row, record in enumerate(self.records):
            key = record['key']
            if key not in assignments:
                continue
            selected = tuple(assignments[key])
            if selected:
                self.group_state['members'][key] = list(selected)
            else:
                self.group_state['members'].pop(key, None)
            record['groups'] = selected
            names = [group['label'] for group in self.group_state['groups']
                     if group['id'] in selected]
            group_cell = self.table.item(row, 2)
            if group_cell is not None:
                group_cell.setText('、'.join(names) or '未分类')
            status = self.table.item(row, 5)
            if status is not None:
                bits = []
                if record['favorite']:
                    bits.append('收藏')
                if selected:
                    bits.append(f'{len(selected)} 组')
                status.setText(' · '.join(bits) or '未分组')
        self.apply_filter()

    def rename_current(self):
        if not self.current_key:
            return
        record = next(item for item in self.records if item['key'] == self.current_key)
        name, accepted = QInputDialog.getText(
            self, '重命名 LUT', '相机中显示的名称：', text=record['label'])
        if not accepted or not name.strip() or name.strip() == record['label']:
            return
        try:
            backup = self.backend.rename_look(
                self.card, self.identity, self.current_key, name.strip())
            key = self.current_key
            self.refresh_library()
            self.select_key(key)
            self.status.setText(f'名称已更新。备份：{backup}')
        except Exception as error:
            QMessageBox.critical(self, '重命名失败', str(error))

    def assign_selected_groups(self):
        keys = self.selected_keys()
        if not keys:
            QMessageBox.information(self, 'LUT 分类', '请先选择至少一款 LUT。')
            return
        state = self.backend.lut_groups(self.card)
        if not state['groups']:
            if QMessageBox.question(
                    self, 'LUT 分类',
                    '还没有自定义分类。现在新建一个吗？'
            ) == QMessageBox.StandardButton.Yes:
                self.manage_groups()
                state = self.backend.lut_groups(self.card)
            if not state['groups']:
                return
        dialog = QDialog(self)
        dialog.setWindowTitle('调整 LUT 分类')
        dialog.resize(500, 520)
        layout = QVBoxLayout(dialog)
        title = QLabel(f'为 {len(keys)} 款 LUT 调整分类')
        title.setObjectName('sectionTitle')
        layout.addWidget(title)
        hint = QLabel('可同时勾选多个分类。勾选结果就是最终归属；'
                      '全部不勾则从自定义分类移除。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        group_list = QListWidget()
        group_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        layout.addWidget(group_list, 1)

        def reload():
            fresh = self.backend.lut_groups(self.card)
            self.group_state = fresh
            group_list.clear()
            counts = {group['id']: 0 for group in fresh['groups']}
            for ids in fresh['members'].values():
                for group_id in ids:
                    if group_id in counts:
                        counts[group_id] += 1
            common = set(fresh['members'].get(keys[0], []))
            for key in keys[1:]:
                common.intersection_update(fresh['members'].get(key, []))
            for group in fresh['groups']:
                item = QListWidgetItem(f'{group["label"]}    {counts[group["id"]]} 款')
                item.setData(Qt.ItemDataRole.UserRole, group['id'])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if group['id'] in common
                                   else Qt.CheckState.Unchecked)
                group_list.addItem(item)

        buttons = QHBoxLayout()
        manage_button = QPushButton('新建 / 改名 / 删除分类')
        cancel_button = QPushButton('取消')
        apply_button = QPushButton('应用分类')
        apply_button.setObjectName('primary')
        buttons.addWidget(manage_button)
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(apply_button)
        layout.addLayout(buttons)

        def manage():
            self.manage_groups()
            reload()

        def apply():
            group_ids = [
                group_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(group_list.count())
                if group_list.item(row).checkState() == Qt.CheckState.Checked
            ]
            assignments = {key: list(group_ids) for key in keys}
            self._run(
                lambda: self.backend.set_looks_groups(self.card, self.identity, assignments),
                lambda backup: self._group_assignment_done(keys, assignments, backup, dialog),
                busy_message='正在写入 LUT 分类…')

        manage_button.clicked.connect(manage)
        cancel_button.clicked.connect(dialog.reject)
        apply_button.clicked.connect(apply)
        reload()
        dialog.exec()

    def _group_assignment_done(self, keys, assignments, backup, dialog):
        self._apply_group_membership(assignments)
        if self.current_key in assignments:
            self.current_key = None
        self.select_key(keys[0])
        message = f'已更新 {len(keys)} 款 LUT 的分类。'
        if backup:
            message += f'备份：{backup}'
        self.status.setText(message)
        dialog.accept()

    def manage_groups(self):
        if self.card is None or self.identity is None:
            QMessageBox.warning(self, 'LUT 分组', '请先选择相机存储卡。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('管理 LUT 分组')
        dialog.resize(520, 430)
        layout = QVBoxLayout(dialog)
        explanation = QLabel('这里管理分类名称和排序所需的分组；LUT 本身不会被复制。建议保留少量大类，再在 LUT 工作台批量分配。')
        explanation.setWordWrap(True)
        explanation.setObjectName('muted')
        layout.addWidget(explanation)
        group_list = QListWidget()
        layout.addWidget(group_list, 1)
        buttons = QHBoxLayout()
        create_button = QPushButton('新建分组')
        create_button.setObjectName('primary')
        rename_button = QPushButton('改名')
        delete_button = QPushButton('删除分组')
        delete_button.setObjectName('danger')
        close_button = QPushButton('关闭')
        buttons.addWidget(create_button)
        buttons.addWidget(rename_button)
        buttons.addWidget(delete_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def selected_group():
            item = group_list.currentItem()
            return ((item.data(Qt.ItemDataRole.UserRole), item.data(Qt.ItemDataRole.UserRole + 1))
                    if item else (None, ''))

        def reload():
            state = self.backend.lut_groups(self.card)
            group_list.clear()
            counts = {group['id']: 0 for group in state['groups']}
            for ids in state['members'].values():
                for group_id in ids:
                    if group_id in counts:
                        counts[group_id] += 1
            for group in state['groups']:
                item = QListWidgetItem(f'{group["label"]}    {counts[group["id"]]} 款')
                item.setData(Qt.ItemDataRole.UserRole, group['id'])
                item.setData(Qt.ItemDataRole.UserRole + 1, group['label'])
                group_list.addItem(item)
            rename_button.setEnabled(group_list.count() > 0)
            delete_button.setEnabled(group_list.count() > 0)

        def create():
            label, accepted = QInputDialog.getText(dialog, '新建 LUT 分组', '分组名称：')
            if not accepted:
                return
            try:
                group, backup = self.backend.create_lut_group(self.card, self.identity, label)
                reload()
                for row in range(group_list.count()):
                    if group_list.item(row).data(Qt.ItemDataRole.UserRole) == group['id']:
                        group_list.setCurrentRow(row)
                        break
                self.log(f'已新建 LUT 分组“{group["label"]}”。备份：{backup}')
                self.refresh_library()
            except Exception as error:
                QMessageBox.critical(dialog, '新建分组失败', str(error))

        def rename():
            group_id, current = selected_group()
            if group_id is None:
                return
            label, accepted = QInputDialog.getText(
                dialog, '重命名 LUT 分组', '分组名称：', text=current)
            if not accepted or label.strip() == current:
                return
            try:
                backup = self.backend.rename_lut_group(
                    self.card, self.identity, group_id, label)
                reload()
                self.log(f'LUT 分组已改名为“{label.strip()}”。备份：{backup}')
                self.refresh_library()
            except Exception as error:
                QMessageBox.critical(dialog, '分组改名失败', str(error))

        def remove():
            group_id, current = selected_group()
            if group_id is None:
                return
            if QMessageBox.question(
                    dialog, '删除 LUT 分组',
                    f'删除分组“{current}”？LUT 文件本身不会被删除。'
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                backup = self.backend.delete_lut_group(self.card, self.identity, group_id)
                reload()
                self.log(f'已删除 LUT 分组“{current}”。备份：{backup}')
                self.refresh_library()
            except Exception as error:
                QMessageBox.critical(dialog, '删除分组失败', str(error))

        create_button.clicked.connect(create)
        rename_button.clicked.connect(rename)
        delete_button.clicked.connect(remove)
        close_button.clicked.connect(dialog.accept)
        reload()
        dialog.exec()
        self.refresh_library()

    def assign_current_group(self):
        if self.card is None or self.identity is None or self.current_key is None:
            QMessageBox.warning(self, 'LUT 分组', '请先选择一个 LUT。')
            return
        state = self.backend.lut_groups(self.card)
        if not state['groups']:
            QMessageBox.information(self, 'LUT 分组', '还没有自定义分组，请先新建一个分组。')
            self.manage_groups()
            return
        current = set(state['members'].get(self.current_key, []))
        dialog = QDialog(self)
        dialog.setWindowTitle('设置 LUT 分组')
        dialog.resize(430, 390)
        layout = QVBoxLayout(dialog)
        title = QLabel(next(record['label'] for record in self.records
                            if record['key'] == self.current_key))
        title.setObjectName('sectionTitle')
        layout.addWidget(title)
        hint = QLabel('一款 LUT 可以同时属于多个分组。取消全部勾选即移入“未分组”。')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        layout.addWidget(hint)
        group_list = QListWidget()
        layout.addWidget(group_list, 1)

        def reload(checked=None):
            nonlocal current
            fresh = self.backend.lut_groups(self.card)
            if checked is not None:
                current = set(checked)
            group_list.clear()
            for group in fresh['groups']:
                item = QListWidgetItem(group['label'])
                item.setData(Qt.ItemDataRole.UserRole, group['id'])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if group['id'] in current else Qt.CheckState.Unchecked)
                group_list.addItem(item)

        buttons = QHBoxLayout()
        manage_button = QPushButton('新建/管理分组')
        cancel_button = QPushButton('取消')
        save_button = QPushButton('保存分组')
        save_button.setObjectName('primary')
        buttons.addWidget(manage_button)
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)

        def manage():
            checked = {
                group_list.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(group_list.count())
                if group_list.item(index).checkState() == Qt.CheckState.Checked
            }
            self.manage_groups()
            reload(checked)

        def save():
            selected = {
                group_list.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(group_list.count())
                if group_list.item(index).checkState() == Qt.CheckState.Checked
            }
            try:
                backup = self.backend.set_look_groups(
                    self.card, self.identity, self.current_key, selected)
                key = self.current_key
                self.refresh_library()
                self.select_key(key)
                if backup is None:
                    self.status.setText('LUT 分组未变化。')
                else:
                    message = 'LUT 分组已更新。备份：' + str(backup)
                    self.status.setText(message)
                    self.log(message)
                dialog.accept()
            except Exception as error:
                QMessageBox.critical(dialog, '更新分组失败', str(error))

        manage_button.clicked.connect(manage)
        cancel_button.clicked.connect(dialog.reject)
        save_button.clicked.connect(save)
        reload()
        dialog.exec()

    def export_current(self):
        if self.current_lut is None or not self.current_key:
            QMessageBox.warning(self, '导出 LUT', '请先选择一个可读取的 LUT。')
            return
        record = next(item for item in self.records if item['key'] == self.current_key)
        suggested = ''.join('_' if char in '<>:"/\\|?*' else char
                            for char in record['label']).strip() or self.current_key
        destination, _ = QFileDialog.getSaveFileName(
            self, '导出 LUT', suggested + '.cube', '3D LUT (*.cube)')
        if not destination:
            return
        try:
            Path(destination).write_bytes(cube_bytes(self.current_lut, title=record['label']))
            self.status.setText(f'已导出 {destination}')
            self.log(self.status.text())
        except OSError as error:
            QMessageBox.critical(self, 'LUT 导出失败', str(error))

    def delete_current(self):
        if not self.current_key:
            return
        record = next(item for item in self.records if item['key'] == self.current_key)
        if QMessageBox.question(
                self, '删除 LUT',
                f'从相机目录移除“{record["label"]}”？\n\n风格目录和 looks.txt 会先备份到电脑。'
        ) != QMessageBox.StandardButton.Yes:
            return
        key = self.current_key
        self._run(lambda: self.backend.delete_look(self.card, self.identity, key),
                  lambda backup: self._delete_done(key, backup),
                  busy_message='正在备份并移除 LUT…')

    def _delete_done(self, key, backup):
        self.current_key = None
        self.refresh_library()
        self.status.setText(f'已移除 {key}。备份：{backup}')
        self.log(self.status.text())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        dropped = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        paths = []
        for path in dropped:
            if path.is_dir():
                paths.extend(item for item in path.rglob('*')
                             if item.is_file()
                             and item.suffix.casefold() in ('.cube', '.vlt', '.zip'))
            else:
                paths.append(path)
        images = [path for path in paths if path.suffix.casefold() in {
            '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}]
        luts = [path for path in paths if path.suffix.casefold() in ('.cube', '.vlt', '.zip')]
        if images:
            self.preview_image_path = images[0]
            self.original_array = None
            self.schedule_preview()
        if luts:
            self.import_paths(luts)
        event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.checked = None
        self.workers = set()
        self.setWindowTitle('NX300M MOD')
        self.setMinimumSize(1100, 820)
        self.resize(1320, 840)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_ui()
        self.refresh_cards()

    def _build_ui(self):
        root = QWidget()
        root.setObjectName('appRoot')
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        nav = QFrame()
        nav.setObjectName('navRail')
        nav.setFixedWidth(218)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(22, 28, 22, 24)
        title = QLabel('NX300M')
        title.setObjectName('brandTitle')
        sub = QLabel('MOD CONTROL CENTER')
        sub.setObjectName('brandSub')
        nav_layout.addWidget(title)
        nav_layout.addWidget(sub)
        nav_layout.addSpacing(34)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = []
        labels = [('概览', 0), ('LUT 工作台', 1), ('诊断与日志', 2)]
        for label, index in labels:
            button = QPushButton(label)
            button.setObjectName('navButton')
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, page=index: self.show_page(page))
            self.nav_group.addButton(button)
            self.nav_buttons.append(button)
            nav_layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        nav_layout.addStretch()
        version = QLabel('本地工具 · 不自动上传')
        version.setObjectName('brandSub')
        nav_layout.addWidget(version)
        outer.addWidget(nav)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(26, 20, 26, 24)
        body_layout.setSpacing(16)
        header = QFrame()
        header.setObjectName('panel')
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 11, 16, 11)
        header_layout.addWidget(QLabel('相机存储卡'))
        self.card_combo = QComboBox()
        self.card_combo.setMinimumWidth(360)
        self.card_combo.currentIndexChanged.connect(self.card_changed)
        header_layout.addWidget(self.card_combo, 1)
        refresh = QPushButton('刷新设备')
        refresh.clicked.connect(self.refresh_cards)
        header_layout.addWidget(refresh)
        self.card_status = QLabel('未选择')
        self.card_status.setObjectName('muted')
        header_layout.addWidget(self.card_status)
        body_layout.addWidget(header)

        self.stack = QStackedWidget()
        body_layout.addWidget(self.stack, 1)
        self.overview = self._overview_page()
        self.lut_workspace = LutWorkspace(self.backend, self.selected_card, self.append_log)
        self.diagnostics = self._diagnostics_page()
        self.stack.addWidget(self.overview)
        self.stack.addWidget(self.lut_workspace)
        self.stack.addWidget(self.diagnostics)
        outer.addWidget(body, 1)

    def _overview_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        hero = QFrame()
        hero.setObjectName('hero')
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(26, 22, 26, 22)
        text = QVBoxLayout()
        title = QLabel('安装、升级和 LUT 管理')
        title.setObjectName('pageTitle')
        description = QLabel('面向已刷 MFW 的 NX300M 1.15。所有卡写入都先校验、备份并回读。')
        description.setObjectName('muted')
        description.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(description)
        hero_layout.addLayout(text, 1)
        self.overview_badge = QLabel('等待选择存储卡')
        self.overview_badge.setObjectName('countChip')
        hero_layout.addWidget(self.overview_badge)
        layout.addWidget(hero)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        card1 = self._action_card('首次安装', '写入启动入口、运行文件与首装清单。',
                                  '安装 MOD 到存储卡', self.first_install, primary=True)
        card2 = self._action_card('升级维护', '识别已知版本，备份后更新运行文件。',
                                  '检查升级', self.check_upgrade)
        card3 = self._action_card('LUT 工作台', '批量导入、预览、收藏、重命名和删除备份。',
                                  '打开 LUT 工作台', lambda: self.show_page(1))
        cards.addWidget(card1, 1)
        cards.addWidget(card2, 1)
        cards.addWidget(card3, 1)
        layout.addLayout(cards)

        panel = QFrame()
        panel.setObjectName('panel')
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        heading = QLabel('当前设备')
        heading.setObjectName('sectionTitle')
        panel_layout.addWidget(heading)
        self.device_detail = QLabel('连接读卡器并选择相机存储卡。')
        self.device_detail.setObjectName('muted')
        self.device_detail.setWordWrap(True)
        panel_layout.addWidget(self.device_detail)
        actions = QHBoxLayout()
        self.upgrade_button = QPushButton('升级 MOD')
        self.upgrade_button.setObjectName('primary')
        self.upgrade_button.setEnabled(False)
        self.upgrade_button.clicked.connect(self.upgrade)
        actions.addWidget(self.upgrade_button)
        uninstall = QPushButton('卸载 MOD')
        uninstall.setObjectName('danger')
        uninstall.clicked.connect(self.uninstall)
        actions.addWidget(uninstall)
        logs = QPushButton('导出排错日志')
        logs.clicked.connect(self.export_logs)
        actions.addWidget(logs)
        actions.addStretch()
        panel_layout.addLayout(actions)
        layout.addWidget(panel)

        self.overview_log = QTextEdit()
        self.overview_log.setReadOnly(True)
        self.overview_log.setMinimumHeight(130)
        self.overview_log.setPlaceholderText('操作记录会显示在这里。')
        layout.addWidget(self.overview_log)
        layout.addStretch()
        return page

    @staticmethod
    def _action_card(title_text, description, button_text, callback, *, primary=False):
        frame = QFrame()
        frame.setObjectName('panel')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        title = QLabel(title_text)
        title.setObjectName('sectionTitle')
        text = QLabel(description)
        text.setObjectName('muted')
        text.setWordWrap(True)
        button = QPushButton(button_text)
        if primary:
            button.setObjectName('primary')
        button.clicked.connect(callback)
        layout.addWidget(title)
        layout.addWidget(text)
        layout.addStretch()
        layout.addWidget(button)
        return frame

    def _diagnostics_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel('诊断与日志')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        description = QLabel('导出本机操作记录和卡上 MOD 日志；不会包含照片或完整配置。')
        description.setObjectName('muted')
        layout.addWidget(description)
        export = QPushButton('导出排错日志')
        export.setObjectName('primary')
        export.clicked.connect(self.export_logs)
        layout.addWidget(export, 0, Qt.AlignmentFlag.AlignLeft)
        self.diagnostic_log = QTextEdit()
        self.diagnostic_log.setReadOnly(True)
        layout.addWidget(self.diagnostic_log, 1)
        return page

    def show_page(self, index: int):
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        if index == 1:
            card, identity = self.selected_card(required=False)
            if card is not None and (self.lut_workspace.card != card or self.lut_workspace.identity != identity):
                self.lut_workspace.set_card(card, identity)

    def refresh_cards(self):
        previous = self.card_combo.currentData()
        self.card_combo.blockSignals(True)
        self.card_combo.clear()
        found = []
        for index in range(26):
            root = f'{chr(65 + index)}:\\'
            try:
                identity = self.backend.volume(root)
            except (OSError, ValueError):
                continue
            if identity['filesystem'].upper() not in ('FAT32', 'EXFAT'):
                continue
            label = f"{root}  {identity['label'] or '未命名'}  {identity['filesystem']}  {identity['serial']}"
            self.card_combo.addItem(label, (root, identity))
            found.append(root)
        if previous:
            for index in range(self.card_combo.count()):
                if self.card_combo.itemData(index)[0] == previous[0]:
                    self.card_combo.setCurrentIndex(index)
                    break
        elif len(found) == 1:
            self.card_combo.setCurrentIndex(0)
        self.card_combo.blockSignals(False)
        if not found:
            self.card_status.setText('未找到 FAT32/exFAT 可移动卡')
        self.card_changed()

    def selected_card(self, required=True):
        data = self.card_combo.currentData()
        if not data:
            if required:
                raise ValueError('请先选择相机存储卡。')
            return None, None
        return Path(data[0]), data[1]

    def card_changed(self):
        data = self.card_combo.currentData()
        self.checked = None
        self.upgrade_button.setEnabled(False)
        if not data:
            if self.card_combo.count() == 0:
                self.card_status.setText('未找到 FAT32/exFAT 可移动卡')
                self.overview_badge.setText('未找到存储卡')
                self.device_detail.setText('连接读卡器，或确认存储卡使用 FAT32/exFAT 文件系统。')
            else:
                self.card_status.setText('未选择')
                self.overview_badge.setText('等待选择存储卡')
                self.device_detail.setText('选择一个相机存储卡。')
            self.lut_workspace.set_card(None, None)
            return
        root, identity = data
        self.card_status.setText(f"{root} · {identity['filesystem']} · {identity['serial']}")
        self.overview_badge.setText('已连接')
        self.device_detail.setText(
            f"盘符 {root}    文件系统 {identity['filesystem']}    卷号 {identity['serial']}\n"
            '相机关机后再取出存储卡。LUT 管理只修改 nx300m-mod 数据目录。')
        self.lut_workspace.set_card(Path(root), identity)

    def _run(self, function, success, busy_text):
        self.append_log(busy_text)
        worker = Worker(function)
        worker.setAutoDelete(False)
        self.workers.add(worker)
        worker.signals.result.connect(success)
        worker.signals.error.connect(lambda message: self._task_error(message))
        worker.signals.finished.connect(lambda worker=worker: self.workers.discard(worker))
        QThreadPool.globalInstance().start(worker)

    def _task_error(self, message):
        self.append_log('操作失败：' + message)
        QMessageBox.critical(self, '操作失败', message)

    def first_install(self):
        try:
            card, identity = self.selected_card()
            self.backend.prepare_first(card, identity)
        except Exception as error:
            QMessageBox.critical(self, '首次安装检查未通过', str(error))
            return
        if QMessageBox.question(
                self, '首次安装',
                f'确认相机已刷入 MFW、已关机，并选对存储卡 {card}（{identity["serial"]}）？'
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run(lambda: self.backend.install_first(card, identity),
                  lambda _: self._installed(), '正在写入并回读首次安装文件…')

    def _installed(self):
        self.append_log('首次安装完成。请安全弹出存储卡，放回相机后开机检查。')
        self.checked = None
        self.upgrade_button.setEnabled(False)

    def check_upgrade(self):
        try:
            card, identity = self.selected_card()
            self.backend.prepare(card)
            self.checked = (str(card), identity)
            self.upgrade_button.setEnabled(True)
            self.append_log(f'检查通过：{card}  卷号 {identity["serial"]}。')
        except Exception as error:
            self.checked = None
            self.upgrade_button.setEnabled(False)
            QMessageBox.critical(self, '检查未通过', str(error))

    def upgrade(self):
        try:
            card, identity = self.selected_card()
            if not self.checked or (str(card), identity) != self.checked:
                raise ValueError('存储卡信息已变化，请重新检查。')
        except Exception as error:
            QMessageBox.critical(self, '无法升级', str(error))
            return
        if QMessageBox.question(
                self, '确认升级',
                f'将备份并更新 {card}（{identity["serial"]}）。继续吗？'
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run(lambda: self.backend.apply(card, identity),
                  lambda backup: self._upgraded(backup), '正在备份并升级 MOD…')

    def _upgraded(self, backup):
        self.checked = None
        self.upgrade_button.setEnabled(False)
        self.append_log(f'升级完成，写入已回读。备份：{backup}')

    def uninstall(self):
        try:
            card, identity = self.selected_card()
        except Exception as error:
            QMessageBox.critical(self, '无法卸载', str(error))
            return
        if QMessageBox.question(
                self, '卸载 MOD',
                f'把 {card}（{identity["serial"]}）上的启动入口和 MOD 移入卡内备份目录？'
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run(lambda: self.backend.uninstall_mod(card, identity),
                  lambda backup: self.append_log(f'MOD 已从启动入口移除；文件保留在 {backup}。'),
                  '正在卸载 MOD…')

    def export_logs(self):
        try:
            card, identity = self.selected_card()
        except Exception as error:
            QMessageBox.critical(self, '日志导出失败', str(error))
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, '导出排错日志', 'NX300M-logs.zip', 'ZIP (*.zip)')
        if not destination:
            return
        if Path(destination).resolve().is_relative_to(card.resolve()):
            QMessageBox.warning(self, '日志导出', '请把日志保存到电脑。')
            return
        try:
            count = self.backend.export_logs(card, destination, identity)
            self.append_log(f'已导出 {count} 个日志文件：{destination}')
        except Exception as error:
            QMessageBox.critical(self, '日志导出失败', str(error))

    def append_log(self, message: str):
        line = f'{datetime.now().strftime("%H:%M:%S")}  {message}'
        for view in (self.overview_log, self.diagnostic_log):
            view.append(line)
        try:
            with (self.backend.BASE / 'installer.log').open('a', encoding='utf-8') as handle:
                handle.write(datetime.now().isoformat(timespec='seconds') + ' ' + message + '\n')
        except OSError:
            pass


def create_app(backend):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('NX300M MOD')
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = MainWindow(backend)
    return app, window
