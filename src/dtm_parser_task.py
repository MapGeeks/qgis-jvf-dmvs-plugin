from qgis.core import QgsTask
from PyQt5.QtCore import pyqtSignal

import logging
logger = logging.getLogger(__name__)

from lxml import etree as ET



class DTMParserTask(QgsTask):
    
    taskCompleted = pyqtSignal(bool)

    def __init__(self, description, parser, filename, namespaces):
        super().__init__(description, QgsTask.CanCancel)
        self.parser = parser
        self.filename = filename
        self.exception = None
        self.success = False
        self.message = ""
        self.namespaces = namespaces
        
        # Uložíme přímé reference na XML prvky, které budeme zpracovávat
        self.data_elements = []
        
    def run(self):
        """Metoda spuštěná v odděleném vláknu"""
        try:
            # Import ET přímo v metodě
            from lxml import etree as ET
            
            # Načtení stylů pokud ještě nejsou
            if not self.parser.style_manager.initialized:
                self.parser.style_manager.load_styles()

            # Čistě parsování XML a příprava dat
            context = ET.iterparse(
                self.filename, events=("end",), tag=f'{{{self.namespaces["objtyp"]}}}DataJVFDTM'
            )
            
            # Zde jen shromáždíme data, ale neprovádíme jejich finální zpracování
            for event, elem in context:
                if self.isCanceled():
                    self.message = "Zpracování bylo zrušeno uživatelem"
                    return False
                    
                try:
                    # Ve vlákně jen zpracujeme XML a připravíme základní struktury
                    self.parser._process_data_element(elem)
                    
                    # Čištění paměti
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
                    
                except Exception as e:
                    logger.error(f"Chyba při zpracování elementu: {e}")
                    continue
            
            self.success = True
            self.message = "Data byla úspěšně načtena"

            return True
            
        except Exception as e:
            self.exception = e
            self.message = f"Chyba při parsování: {str(e)}"
            logger.error(f"Chyba při parsování souboru: {e}", exc_info=True)
            return False
    
    def finished(self, result):
        """Metoda volaná v hlavním vlákně po dokončení úlohy"""
        self.success = result
        if not result:
            self.message = f"Chyba při parsování dat"
        else:
            self.message = "Data byla úspěšně načtena"
        
        # Emitujeme signál
        self.taskCompleted.emit(result)
        
        
           
    def _finalizeInMainThread(self):
        """Metoda spuštěná v hlavním vlákně přes QTimer"""
        try:
            # Přidáme vrstvy do projektu
            self.parser._finalize_project_tree()
            
            # Zoom provedeme také s krátkým zpožděním
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(500, self.parser._zoom_to_data)
            
            self.parser.iface.messageBar().pushSuccess("DTM Parser", self.message)
        except Exception as e:
            logger.error(f"Chyba při finalizaci dat: {e}", exc_info=True)
            self.parser.iface.messageBar().pushWarning("DTM Parser", f"Chyba při finalizaci: {str(e)}")