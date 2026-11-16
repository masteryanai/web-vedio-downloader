"""Shared Steam-style dialog navigation; pages retain their real configuration widgets."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QFrame,QHBoxLayout,QVBoxLayout,QLabel,QListWidget,QStackedWidget,QScrollArea

class DialogPages(QWidget):
    def __init__(self,title,parent=None):
        super().__init__(parent)
        outer=QHBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        side=QFrame();side.setObjectName('settingsSidebar');side.setFixedWidth(190)
        box=QVBoxLayout(side);box.setContentsMargins(12,22,12,16);box.setSpacing(22)
        brand=QLabel(title);brand.setObjectName('settingsBrand');box.addWidget(brand)
        self.navigation=QListWidget();self.navigation.setObjectName('settingsNavigation');box.addWidget(self.navigation)
        outer.addWidget(side);self.pages=QStackedWidget();outer.addWidget(self.pages,1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
    def page(self,title):
        self.navigation.addItem(title)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setObjectName('settingsScroll')
        content=QWidget();content.setObjectName('settingsContent');layout=QVBoxLayout(content);layout.setContentsMargins(28,26,28,24);layout.setSpacing(16)
        heading=QLabel(title);heading.setObjectName('dialogTitle');layout.addWidget(heading)
        scroll.setWidget(content);self.pages.addWidget(scroll)
        if self.navigation.currentRow()<0:self.navigation.setCurrentRow(0)
        return layout
