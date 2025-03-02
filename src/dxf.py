# -*- coding: utf-8 -*-
"""
Skript pro export vybraných vrstev v QGIS do DXF v UTF-8 v měřítku 1:500
"""

import os
import sys
from qgis.core import (QgsProject, QgsLayoutExporter, QgsLayout, 
                      QgsLayoutItemMap, QgsLayoutSize, QgsUnitTypes,
                      QgsCoordinateReferenceSystem, QgsVectorFileWriter,
                      QgsDxfExport, QgsRectangle, QgsMapLayer)
from qgis.utils import iface
from PyQt5.QtCore import QSize, QRectF
from PyQt5.QtWidgets import QFileDialog

def export_layers_to_dxf(output_filename, selected_layers=None, scale=500):
    """
    Exportuje vybrané vrstvy do DXF v měřítku 1:500 s kódováním UTF-8
    
    Args:
        output_filename (str): Cesta a název výstupního souboru (bez přípony)
        selected_layers (list): Seznam názvů vrstev k exportu (None = všechny viditelné)
        scale (int): Měřítko exportu (výchozí 1:500)
    """
    # Odvodit výstupní adresář z output_filename
    output_dir = os.path.dirname(output_filename)
    if not output_dir:  # Pokud nebyla specifikována cesta, použij aktuální adresář
        output_dir = os.getcwd()
        output_filename = os.path.join(output_dir, output_filename)
    
    # Vytvoření output adresáře, pokud neexistuje
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # Získání projektu
    project = QgsProject.instance()
    
    # Určení, které vrstvy budou exportovány
    layers_to_export = []
    if selected_layers:
        # Export jen specifikovaných vrstev
        for layer_name in selected_layers:
            layers = project.mapLayersByName(layer_name)
            if layers:
                layers_to_export.append(layers[0])
    else:
        # Export všech viditelných vrstev
        root = project.layerTreeRoot()
        for layer in root.findLayers():
            if layer.isVisible():
                layers_to_export.append(layer.layer())
    
    if not layers_to_export:
        print("Žádné vrstvy ke exportu!")
        return
    
    print(f"Nalezeno {len(layers_to_export)} vrstev k exportu.")
    
    # Připrav název souboru bez cesty pro použití v názvech souborů
    base_filename = os.path.basename(output_filename)
    
    # Cesta pro DXF soubor v UTF-8
    dxf_file = os.path.join(output_dir, f"{base_filename}.dxf")
    
    # Získání rozsahu pro export (kombinace všech vrstev)
    extent = QgsRectangle()
    for layer in layers_to_export:
        if extent.isEmpty():
            extent = layer.extent()
        else:
            extent.combineExtentWith(layer.extent())
    
    try:
        # Vytvoření exportéru DXF
        dxf_exporter = QgsDxfExport()
        
        # Nastavení měřítka
        try:
            if hasattr(dxf_exporter, "setSymbologyScale"):
                dxf_exporter.setSymbologyScale(scale)
        except Exception as e:
            print(f"Varování: Nepodařilo se nastavit měřítko symbologie: {e}")
               
        
        # Nastavení souřadnicového systému na Křovák (EPSG:5514)
        try:
            krovak_crs = QgsCoordinateReferenceSystem("EPSG:5514")
            if hasattr(dxf_exporter, "setSourceCrs"):
                dxf_exporter.setSourceCrs(krovak_crs)
            elif hasattr(dxf_exporter, "setCrs"):
                dxf_exporter.setCrs(krovak_crs)
        except Exception as e:
            print(f"Varování: Nepodařilo se nastavit CRS: {e}")
        
        # Nastavení vrstev pro export - kompatibilita s různými verzemi QGIS
        try:
            map_settings = iface.mapCanvas().mapSettings()
            dxf_exporter.setMapSettings(map_settings)
        except Exception as e:
            print(f"Varování: Nepodařilo se nastavit nastavení mapy: {e}")
        
        layers_dict = {}
        for idx, layer in enumerate(layers_to_export):
            if layer.type() == QgsMapLayer.VectorLayer:
                layers_dict[layer.id()] = layer
        
        if not layers_dict:
            print("Žádné vektorové vrstvy ke zpracování!")
            return False
        
        # Starší verze QGIS používají jiné metody
        try:
            if hasattr(dxf_exporter, "setVectorLayers"):
                dxf_exporter.setVectorLayers(list(layers_dict.values()))
            elif hasattr(dxf_exporter, "setLayers"):
                dxf_exporter.setLayers(list(layers_dict.values()))
        except Exception as e:
            print(f"Varování: Nepodařilo se nastavit vrstvy: {e}")
        
        # Export souboru
        try:
            result = None
            # Různé verze QGIS mohou mít různé signatury metody writeToFile
            try:
                # Nejprve zkusíme verzi, která očekává QIODevice
                from PyQt5.QtCore import QFile, QIODevice
                
                # Přímo exportujeme do finálního DXF souboru
                output_file = QFile(os.path.join(output_dir, f"{base_filename}.dxf"))
                
                if output_file.open(QIODevice.WriteOnly | QIODevice.Text):
                    result = dxf_exporter.writeToFile(output_file, "UTF-8")
                    output_file.close()
                else:
                    print(f"Nelze otevřít soubor pro zápis: {dxf_file}")
                    return False
            except Exception as e1:
                print(f"První metoda exportu selhala: {e1}")
                try:
                    # Zkusíme alternativní metodu pro starší QGIS
                    result = dxf_exporter.writeToFile(os.path.join(output_dir, f"{base_filename}.dxf"))
                except Exception as e2:
                    print(f"Druhá metoda exportu selhala: {e2}")
                    return False
            
            if result is None:
                print("Chyba při exportu do DXF!")
                return False

             # Konverze AC1015 na AC1021 a nastavení jednotek na metry
            try:
                dxf_path = os.path.join(output_dir, f"{base_filename}.dxf")
                # Čtení obsahu souboru
                with open(dxf_path, 'r', encoding='utf-8') as file:
                    content = file.read()
                
                # Nahrazení AC1015 za AC1021
                new_content = content.replace('AC1015', 'AC1021')
                
                # Nastavení jednotek na metry (6)
                # Hledáme sekci $INSUNITS a její hodnotu
                if '$INSUNITS' in new_content:
                    # Pokud existuje, upravíme hodnotu na 6 (metry)
                    import re
                    pattern = r'(\$INSUNITS\n\s*70\n\s*)\d+'
                    new_content = re.sub(pattern, r'\g<1>6', new_content)
                else:
                    # Pokud neexistuje, přidáme ji do HEADER sekce
                    header_end = new_content.find('ENDSEC', new_content.find('HEADER'))
                    if header_end > -1:
                        units_def = "\n$INSUNITS\n 70\n     6\n"
                        new_content = new_content[:header_end] + units_def + new_content[header_end:]
                
                # Zápis upraveného obsahu zpět do souboru
                with open(dxf_path, 'w', encoding='utf-8') as file:
                    file.write(new_content)
                    
                print(f"Verze DXF souboru byla změněna na AC1021 (AutoCAD 2007)")
                print(f"Jednotky DXF souboru byly nastaveny na metry")
            except Exception as e:
                print(f"Varování: Nepodařilo se upravit DXF soubor: {e}")



                
            print(f"DXF soubor byl úspěšně vytvořen v UTF-8: {os.path.join(output_dir, f'{base_filename}.dxf')}")
            return True
            
        except Exception as e:
            print(f"Chyba při exportu DXF: {e}")
            return False
            
    except Exception as e:
        print(f"Neočekávaná chyba: {e}")
        return False
