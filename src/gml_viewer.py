"""
@brief Plugin Implementation of Czech DTM QGIS Viewer

Classes:
 - GMLViewer

(C) 2024-2025 by MapGeeks
@author Petr Barandovski petr.barandovski@gmail.com
@author Linda Karlovska linda.karlovska@seznam.cz

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.    
"""

from pathlib import Path
from typing import Optional

from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox, QWidget
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication

from .czech_dtm_parser import CzechDTMParser
from .dxf import export_layers_to_dxf

class GMLViewer:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor."""
        # Save reference to the QGIS interface
        self.iface = iface

        # Initialize plugin directory and locale
        self.plugin_dir = Path(__file__).parent
        self.locale = self._get_locale()

        # Load translations if available
        self._load_translations()

        # Declare instance attributes
        self.actions = []
        self.menu = "JVF DTM Viewer"

        # Check if plugin was started the first time in current QGIS session
        self.first_start = None

        # Initialize parser
        self.parser = CzechDTMParser(self.iface)

    def _get_locale(self) -> str:
        """Retrieve the user's locale setting or default to 'en'."""
        locale = QSettings().value("locale/userLocale", "")
        return locale[:2] if locale else "en"

    def _load_translations(self) -> None:
        """Load and install translations based on locale."""
        locale_path = self.plugin_dir / "i18n" / f"GMLViewer_{self.locale}.qm"
        if locale_path.exists():
            translator = QTranslator()
            translator.load(str(locale_path))
            QCoreApplication.installTranslator(translator)

    @staticmethod
    def _show_message_box(message: str, success: bool) -> None:
        """Display a message box with the given message and icon."""
        msg_box = QMessageBox()
        msg_box.setText(message)
        msg_box.setIcon(QMessageBox.Information if success else QMessageBox.Critical)
        msg_box.exec()

    def add_action(
        self,
        icon_path: Path,
        text: str,
        callback: callable,
        enabled_flag: bool = True,
        add_to_menu: bool = True,
        add_to_toolbar: bool = True,
        status_tip: Optional[str] = None,
        whats_this: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> QAction:
        """Add a toolbar icon to the toolbar."""
        action = QAction(QIcon(str(icon_path)), text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip:
            action.setStatusTip(status_tip)

        if whats_this:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.iface.addToolBarIcon(action)

        if add_to_menu:
            self.iface.addPluginToVectorMenu(self.menu, action)

        self.actions.append(action)
        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside QGIS."""
        icon_path = self.plugin_dir / "../icons/iconIn.png"
        self.add_action(
            icon_path,
            text="Open JVF DTM Viewer",
            callback=self.run,
            parent=self.iface.mainWindow(),
        )
        
        icon_path_dxf = self.plugin_dir / "../icons/iconOut.png"
        self.add_action(
            icon_path_dxf,
            text="JVF DTM Viewer - export to DXF",
            callback=self.run_dxf,
            parent=self.iface.mainWindow(),
        )
            
        self.first_start = True

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginVectorMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        self.actions.clear()

    def run(self) -> None:
        """
        Run method that performs all the real work.
        Zpracování probíhá asynchronně v samostatném vlákně.
        """
        filename, _ = QFileDialog.getOpenFileName(
            None, "Select JVF File", "", "JVF files XML files (*.xml);;All files (*.*)"
        )
        if not filename:
            return

        # Spustíme parsování ve vlákně a pokračujeme dál
        # Zpětná vazba uživateli bude poskytnuta přes QgsMessageBar
        self.parser.parse_file(filename)
        
        # Zde už se nevypisuje žádná zpráva - zpětná vazba je zajištěna v DTMParserTask.finished

    def run_dxf(self) -> None:
        """Run method that performs export to DXF"""
        filename, _ = QFileDialog.getSaveFileName(
            None, "Select DXF File", "", "DXF (*.dxf);;All files (*.*)"
        )
        if not filename:
            return
        if filename.lower().endswith('.dxf'):
            filename = filename[:-4]
        export_layers_to_dxf(filename, None, 500)

