import sys
import math
import random
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QLinearGradient, QRadialGradient


class JarvisUI(QWidget):
    def __init__(self):
        super().__init__()

        self.showFullScreen()

        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # TEXT
        self.user_text = ""
        self.jarvis_text = ""
        self.display_text = ""

        # ANIMATION
        self.angle = 0
        self.glow = 80
        self.dir = 1
        self.wave_phase = 0
        self.speaking = False
        self.typing_index = 0

        # FX
        self.glitch_offset = 0
        self.scan_line = 0
        self.flicker = 1

        # PARTICLES
        self.particles = [
            [random.randint(0, 1920), random.randint(0, 1080), random.uniform(0.5, 2)]
            for _ in range(200)
        ]

        self.timer = QTimer()
        self.timer.timeout.connect(self.animate)
        self.timer.start(16)

        self.typing_timer = QTimer()
        self.typing_timer.timeout.connect(self.type_text)

    def update_user(self, text):
        self.user_text = text

    def update_jarvis(self, text):
        self.jarvis_text = text
        self.display_text = ""
        self.typing_index = 0
        self.typing_timer.start(10)

    def start_speaking(self):
        self.speaking = True
        QTimer.singleShot(2000, self.stop_speaking)

    def stop_speaking(self):
        self.speaking = False

    def type_text(self):
        if self.typing_index < len(self.jarvis_text):
            self.display_text += self.jarvis_text[self.typing_index]
            self.typing_index += 1
        else:
            self.typing_timer.stop()

    def animate(self):
        self.angle += 1.2
        self.wave_phase += 0.25

        self.glitch_offset = random.randint(-3, 3)
        self.scan_line += 5
        if self.scan_line > self.height():
            self.scan_line = 0

        self.flicker = random.uniform(0.85, 1.0)

        for p in self.particles:
            p[1] += p[2]
            if p[1] > self.height():
                p[0] = random.randint(0, self.width())
                p[1] = 0

        self.update()

    # ==========================
    # DRAWING
    # ==========================
    def draw_background(self, painter, w, h):
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0, QColor(5, 0, 20))
        gradient.setColorAt(1, QColor(0, 0, 0))
        painter.fillRect(0, 0, w, h, gradient)

    def draw_grid(self, painter, w, h):
        painter.setPen(QPen(QColor(0, 255, 255, 25), 1))
        for y in range(h // 2, h, 40):
            painter.drawLine(0, y, w, y)
        for x in range(0, w, 80):
            painter.drawLine(x, h // 2, w // 2, h)

    def draw_particles(self, painter):
        for x, y, speed in self.particles:
            painter.setPen(QPen(QColor(0, 255, 255, 80), 2))
            painter.drawPoint(int(x), int(y))

    def draw_core(self, painter, cx, cy):
        glow = QRadialGradient(cx, cy, 250)
        glow.setColorAt(0, QColor(0, 255, 255, 80))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(cx - 250, cy - 250, 500, 500)

        for i in range(6):
            r = 90 - i * 12
            painter.setPen(QPen(QColor(0, 255, 255, 180 - i * 30), 2))
            painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

    def draw_rotation(self, painter, cx, cy):
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self.angle)
        for i in range(24):
            painter.setPen(QPen(QColor(0, 255, 255, 120), 2))
            painter.drawLine(0, -120, 0, -160)
            painter.rotate(15)
        painter.restore()

    def draw_wave(self, painter, cx, cy):
        if not self.speaking:
            return
        for i in range(200):
            angle = (i / 200) * 2 * math.pi
            wave = math.sin(angle * 7 + self.wave_phase) * 30
            r = 150 + wave
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            painter.setPen(QPen(QColor(0, 255, 255, 120), 2))
            painter.drawPoint(int(x), int(y))

    def draw_scanline(self, painter, w):
        painter.setPen(QPen(QColor(0, 255, 255, 40), 2))
        painter.drawLine(0, self.scan_line, w, self.scan_line)

    def draw_title(self, painter, w):
        title = "JARVIS"

        # glitch layers
        painter.setFont(QFont("Orbitron", 80, QFont.Weight.Bold))

        painter.setPen(QColor(255, 0, 255, 100))
        painter.drawText(w // 2 - 200 + self.glitch_offset, 120, title)

        painter.setPen(QColor(0, 255, 255))
        painter.drawText(w // 2 - 200, 120, title)

        painter.setPen(QColor(0, 255, 255, 50))
        painter.drawText(w // 2 - 200, 120 + 2, title)

    def draw_ui(self, painter, w, h):
        painter.setOpacity(self.flicker)

        self.draw_title(painter, w)

        painter.setFont(QFont("Orbitron", 16))
        painter.setPen(QColor(180, 0, 255))
        painter.drawText(w // 2 - 160, 150, "NEURAL INTERFACE • ONLINE")

        painter.setFont(QFont("Orbitron", 36))
        painter.setPen(QColor(0, 255, 255))
        painter.drawText(w // 2 - 400, h // 2 - 140, self.display_text)

        painter.setFont(QFont("Consolas", 20))
        painter.setPen(QColor(0, 255, 120))
        painter.drawText(60, h - 60, f"> {self.user_text}")

    # ==========================
    # MAIN PIPELINE
    # ==========================
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2

        self.draw_background(painter, w, h)
        self.draw_grid(painter, w, h)
        self.draw_particles(painter)
        self.draw_core(painter, cx, cy)
        self.draw_rotation(painter, cx, cy)
        self.draw_wave(painter, cx, cy)
        self.draw_scanline(painter, w)
        self.draw_ui(painter, w, h)


def start_ui():
    app = QApplication(sys.argv)
    window = JarvisUI()
    window.show()
    return app, window