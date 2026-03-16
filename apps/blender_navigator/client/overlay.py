import sys
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtCore import Qt, QRect

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.target_rect = None # (x, y, w, h)

    def initUI(self):
        # Make the window transparent and borderless
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Allows clicking through the overlay
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        # Determine screen size (or Blender window size ideally)
        # For simplicity, let's cover the entire primary screen
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        
    def set_target(self, x, y, w, h):
        self.target_rect = (x, y, w, h)
        self.update() # Trigger a repaint

    def paintEvent(self, event):
        if not self.target_rect:
            return
            
        x, y, w, h = self.target_rect
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw a bright red rectangle
        pen = QPen(QColor(255, 0, 0, 200), 5, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.drawRect(x, y, w, h)
        
        # Can add text, arrows, etc.

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = Overlay()
    
    # Test rectangle:
    ex.set_target(100, 100, 200, 50)
    
    ex.show()
    sys.exit(app.exec())
