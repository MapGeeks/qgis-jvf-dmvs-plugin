"""
@brief Implementation of the CzechDTMParser class, 
designed to parse and process large JVF files in the context of QGIS. 
It efficiently handles XML data using streaming parsing techniques and 
supports the processing of geometries, attributes, and other data elements. 

Classes:
 - ScaleRange
 - CzechDTMParser

(C) 2024-2025 by MapGeeks
@author Petr Barandovski petr.barandovski@gmail.com
@author Linda Karlovska linda.karlovska@seznam.cz

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.    
"""

import logging
import json
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict
from lxml import etree as ET

from qgis.core import (
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsRuleBasedRenderer,
    QgsApplication
)

from .style_manager import StyleManager
from .geometry_processor import GeometryProcessor
from .attribute_processor import AttributeProcessor
from .batch_processor import BatchFeatureProcessor
from .symbol_processor import SymbolProcessor
from .helpers import load_type_mapping
from .dtm_parser_task import DTMParserTask





logger = logging.getLogger(__name__)

# XML namespaces
NAMESPACES = {
    "objtyp": "objtyp",
    "gml": "http://www.opengis.net/gml/3.2",
    "dopinf": "dopinf",
    "trdpvs": "trdpvs",
    "trdpes": "trdpes",
    "atr": "atr",
    "cmn": "cmn",
    "x": "*",
    "xs": "http://www.w3.org/2001/XMLSchema",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

# Konfigurace vrstev
LAYER_CONFIG = {
    "01": "Point",
    "02": "LineString",
    "03": "Polygon",
    "04": "DB",
}


@dataclass
class ScaleRange:
    """Represents a scale range with minimum and maximum values."""

    min_scale: int
    max_scale: int

    def __str__(self) -> str:
        if self.min_scale == 0:
            return f"0 - 1:{self.max_scale}"
        return f"1:{self.min_scale} - 1:{self.max_scale}"


# Definice rozsahů měřítek
SCALE_RANGES = {
    "500": ScaleRange(0, 500),
    "5000": ScaleRange(501, 5000),
    "10000": ScaleRange(5001, 10000),
    "25000": ScaleRange(10001, 25000),
}


class CzechDTMParser:
    """Parser for processing JVF file."""

    def __init__(self, iface):
        """
        Parser init.

        Args:
            iface: QGIS interface
        """
        self.iface = iface

        # Inicializace pomocných tříd
        self.style_manager = StyleManager()
        self.geom_processor = GeometryProcessor()
        self.feature_processor = BatchFeatureProcessor()
        self.symbol_processor = SymbolProcessor()
        self.attr_processor = AttributeProcessor()

        # Načtení konfigurace
        self.type_mapping_df = load_type_mapping()
        
        # Dočasné úložiště pro vrstvy a skupiny
        self.temp_tree_structure = {}
        self.temp_layers = []
        self.root_groups = {}


    def parse_file(self, filename: str) -> Tuple[bool, str]:
        """
        Hlavní metoda pro parsování DTM souboru - používá neblokující přístup.
        """
        from PyQt5.QtCore import QEventLoop, QTimer
        
        
        # Resetujeme struktury
        self.temp_tree_structure = {}
        self.temp_layers = []
        self.root_groups = {}
        
        # Informace pro uživatele
        self.iface.messageBar().pushInfo("DTM Parser", "Načítání dat zahájeno...")
        
        # Vytvoříme event loop, který bude "čekat" na dokončení úlohy
        loop = QEventLoop()
        
        # Vytvoříme a spustíme úlohu
        task = DTMParserTask("Parsování DTM dat", self, filename, NAMESPACES)
        
        # Připojíme výsledek úlohy k ukončení event loop
        task.taskCompleted.connect(loop.quit)
        
        # Přidáme úlohu do správce úloh
        QgsApplication.taskManager().addTask(task)
        
        # "Čekáme" na dokončení úlohy - ale ve skutečnosti necháváme Qt zpracovávat události
        # To znamená, že UI zůstává responzivní
        loop.exec_()
        
        # Nyní je úloha dokončena, můžeme finalizovat vrstvy v hlavním vlákně
        if hasattr(task, 'success') and task.success:
            try:
                # Finalizace projektu
                self._finalize_project_tree()
                self._zoom_to_data()
                self.iface.messageBar().pushSuccess("DTM Parser", "Data byla úspěšně načtena")
            except Exception as e:
                logger.error(f"Chyba při finalizaci dat: {e}", exc_info=True)
                self.iface.messageBar().pushWarning("DTM Parser", f"Chyba: {str(e)}")
        else:
            message = getattr(task, 'message', "Neznámá chyba")
            self.iface.messageBar().pushWarning("DTM Parser", message)
        
        return True, "Zpracování dokončeno"

    
    def _finalize_project_tree(self):
        """
        Hromadně přidá všechny vrstvy do projektu a struktury skupin.
        Tato metoda musí běžet v hlavním vlákně.
        """
        from PyQt5.QtCore import QCoreApplication
        
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        
        # Pro jistotu zpracujeme události aplikace
        QCoreApplication.processEvents()
        
        # Nejprve vytvoříme celou strukturu skupin
        for group_path, group_name in self.temp_tree_structure.items():
            QCoreApplication.processEvents()  # Zpracování UI událostí
            self._ensure_group_path(group_path, group_name)
        
        # Pak přidáme vrstvy do projektu
        layers_to_add = []
        for layer, parent_path, visible in self.temp_layers:
            if layer and layer.isValid():
                layers_to_add.append((layer, False))  # False = nepřidat do legendy
        
        # Hromadné přidání vrstev do projektu
        project.addMapLayers([layer for layer, _ in layers_to_add], False)
        
        # Zpracujeme UI události
        QCoreApplication.processEvents()
        
        # Přidání vrstev do skupin
        for i, (layer, parent_path, visible) in enumerate(self.temp_layers):
            if i % 10 == 0:  # Každých 10 vrstev zpracovat události UI
                QCoreApplication.processEvents()
                
            if not layer or not layer.isValid():
                continue
                
            parent_group = self._get_group_by_path(parent_path)
            if parent_group:
                layer_node = parent_group.addLayer(layer)
                if layer_node:
                    layer_node.setItemVisibilityChecked(visible)
        
        # Nakonec refresh
        self.iface.mapCanvas().refresh()
        QCoreApplication.processEvents()
    
    def _ensure_group_path(self, path, name):
        """
        Zajistí, že existuje cesta ke skupině ve stromě.
        
        Args:
            path: Cesta ke skupině ve formátu 'obsah/kategorie/skupina'
            name: Název skupiny
        """
        parts = path.split('/')
        current_path = ""
        parent = QgsProject.instance().layerTreeRoot()
        
        for i, part in enumerate(parts):
            if not current_path:
                current_path = part
            else:
                current_path += f"/{part}"
            
            if current_path in self.root_groups:
                parent = self.root_groups[current_path]
            else:
                # Vytvoříme novou skupinu
                new_group = self.create_group(part, parent)
                self.root_groups[current_path] = new_group
                parent = new_group
    
    def _get_group_by_path(self, path):
        """
        Získá skupinu podle cesty.
        
        Args:
            path: Cesta ke skupině ve formátu 'obsah/kategorie/skupina'
            
        Returns:
            QgsLayerTreeGroup: Instance skupiny nebo None
        """
        return self.root_groups.get(path)

    def _process_data_element(self, root_elem: ET.Element) -> int:
        """Zpracování hlavního datového elementu"""
        processed_count = 0

        # Získání a seřazení datových objektů
        data_objects = root_elem.findall("./objtyp:Data/*", NAMESPACES)
        data_objects.sort(
            key=lambda x: x.find("x:ObjektovyTypNazev", NAMESPACES).get("code_base")
            if x.find("x:ObjektovyTypNazev", NAMESPACES) is not None
            else ""
        )

        for data_obj in data_objects:
            try:
                processed_count += self._process_data_object(data_obj)
            except Exception as e:
                logger.error(f"Error processing data object: {e}")
                continue

        return processed_count

    def _process_data_object(self, data_obj: ET.Element) -> int:
        """Zpracování jednotlivého datového objektu"""
        obj_type = data_obj.find("x:ObjektovyTypNazev", NAMESPACES)
        if obj_type is None:
            return 0

        # Získání kódů a názvů
        code_num = obj_type.get("code_base")[7:]
        code_suffix = obj_type.get("code_suffix")
        config_type = LAYER_CONFIG.get(code_suffix, "")

        # Vytvoření struktury skupin
        groups = self._create_group_structure(data_obj)

        # Zpracování záznamů
        records = data_obj.findall("x:ZaznamyObjektu/x:ZaznamObjektu", NAMESPACES)
        if not records:
            return 0

        return self._process_records(records, obj_type, code_num, config_type, groups)


    def create_group(self, group_name: str, parent=None):
        """
        Vytvoří novou skupinu v daném rodičovském elementu.
        
        Args:
            group_name: Název skupiny
            parent: Rodičovský element (výchozí je kořen projektu)
            
        Returns:
            QgsLayerTreeGroup: Nová nebo existující skupina
        """
        if parent is None:
            parent = QgsProject.instance().layerTreeRoot()

        existing_group = parent.findGroup(group_name)
        if existing_group:
            return existing_group

        return parent.addGroup(group_name)


    def _create_group_structure(self, data_obj):
        """
        Vytvoří strukturu skupin a uloží je do dočasné struktury.
        
        Returns:
            Dict: Slovník s cestami ke skupinám
        """
        obsahova_cast = data_obj.find("x:ObsahovaCast", NAMESPACES).text
        kategorie = data_obj.find("x:KategorieObjektu", NAMESPACES).text
        skupina = data_obj.find("x:SkupinaObjektu", NAMESPACES).text
        
        # Vytvoříme cesty k jednotlivým úrovním skupin
        obsah_path = obsahova_cast
        kategorie_path = f"{obsah_path}/{kategorie}"
        skupina_path = f"{kategorie_path}/{skupina}"
        
        # Přidáme do dočasné struktury, pokud tam ještě nejsou
        if obsah_path not in self.temp_tree_structure:
            self.temp_tree_structure[obsah_path] = obsahova_cast
        
        if kategorie_path not in self.temp_tree_structure:
            self.temp_tree_structure[kategorie_path] = kategorie
        
        if skupina_path not in self.temp_tree_structure:
            self.temp_tree_structure[skupina_path] = skupina
        
        return {
            "obsah": obsah_path,
            "kategorie": kategorie_path,
            "skupina": skupina_path,
        }

    def _process_records(
        self,
        records: List[ET.Element],
        obj_type: ET.Element,
        code_num: str,
        config_type: str,
        groups: Dict[str, str]
    ) -> int:
        """Zpracování záznamů objektů"""
        type_features = defaultdict(
            lambda: {
                "features": [],
                "scale_features": [],
                "first_element": None,
                "tag_name": None,
                "type_value": None,
                "geom_type": None,
                "second_geom_type": None,
                "has_second_geom": False,
            }
        )
        # print(f"\nProcessing records for: {code_num} {obj_type.text}")

        processed_count = 0
        for record in records:
            try:
                processed_count += self._process_single_record(
                    record, obj_type, code_num, type_features
                )
            except Exception as e:
                print(f"Error processing record: {e}")
                continue

        # print(f"Found {len(type_features)} different types:")

        # Vytvoření vrstev pro každý typ
        self._create_layers_for_types(type_features, obj_type, config_type, groups)

        return processed_count

    def _process_single_record(
        self,
        record: ET.Element,
        obj_type: ET.Element,
        code_num: str,
        type_features: dict,
    ) -> int:
        """Zpracování jednotlivého záznamu"""
        try:
            # Získání typu a atributů
            attributes = record.find("x:AtributyObjektu", NAMESPACES)
            record_type, tag_name = self._determine_record_type(
                attributes, code_num, obj_type.text
            )

            # Zpracování geometrií
            geom_list = list(
                self.geom_processor.process_geometries(
                    record.find("x:GeometrieObjektu", NAMESPACES)
                )
            )

            if not geom_list:
                return 0

            # Uložení dat do type_features
            return self._store_geometries(
                geom_list, record, record_type, tag_name, type_features
            )
        except Exception as e:
            print(f"Detailní chyba při zpracování záznamu: {str(e)}")
            import traceback
            traceback.print_exc()  # Toto vytiskne celý stack trace
            return 0

    def _determine_record_type(
        self, attributes: ET.Element, code_num: str, obj_type_text: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """Určení typu záznamu"""
        if attributes is None or self.type_mapping_df is None:
            return None, None

        tag_name = None
        record_type = None
        mapping_row = self.type_mapping_df[
            self.type_mapping_df["code"] == f"{code_num} {obj_type_text}"
        ]

        if not mapping_row.empty:
            expected_attributes = mapping_row.iloc[0]["attributes"]
            for expected_attribute in expected_attributes:
                for child in attributes:
                    current_tag = child.tag.split("}")[-1]

                    if current_tag == expected_attribute:
                        # and child.text != '0':
                        if record_type is None:
                            record_type = self.attr_processor.value_mappings[
                                current_tag
                            ][child.text]
                            tag_name = current_tag
                        else:
                            record_type = f"{record_type} {self.attr_processor.value_mappings[current_tag][child.text]}"
                        break

        return record_type, tag_name

    def _on_processing_finished(self, progress_dialog, success, message):
        """Handler for processing completion"""
        progress_dialog.close()
        if success:
            self.iface.messageBar().pushSuccess("DTM Parser", message)
        else:
            self.iface.messageBar().pushWarning("DTM Parser", message)


    def _zoom_to_data(self):
        """
        Funkce pro zoom na data - centruje mapu na všechna načtená data
        a nastavuje měřítko podle největšího rozsahu z rendererů.
        """
        from PyQt5.QtCore import QTimer
        
        def do_zoom():
            # Získáme všechny vektorové vrstvy
            layers = [layer for layer in QgsProject.instance().mapLayers().values() 
                    if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.featureCount() > 0]
            
            if not layers:
                return  # Nemáme žádná data
            
            # Vytvoříme kombinovaný extent všech vrstev
            combined_extent = None
            
            # Najdeme největší měřítko z rendererů všech vrstev
            largest_scale = 0
            
            for layer in layers:
                # Aktualizujeme extent
                if combined_extent is None:
                    combined_extent = layer.extent()
                else:
                    combined_extent.combineExtentWith(layer.extent())
                
                # Zkontrolujeme renderer
                renderer = layer.renderer()
                if isinstance(renderer, QgsRuleBasedRenderer):
                    # Procházíme pravidla rendereru
                    root_rule = renderer.rootRule()
                    for child_rule in root_rule.children():
                        # Největší minimumScale() hodnota určuje nejmenší detailní měřítko
                        if child_rule.minimumScale() > largest_scale:
                            largest_scale = child_rule.minimumScale()
            
            if combined_extent is None or combined_extent.isNull():
                return  # Nemáme platný extent
            
            # Rozšíříme extent o 10% pro lepší vizualizaci
            combined_extent.grow(0.1)
            
            # Nastavíme pohled
            canvas = self.iface.mapCanvas()
            canvas.setExtent(combined_extent)
            
            # Použijeme největší nalezené měřítko, nebo výchozí hodnotu
            if largest_scale == 0:
                # Použijeme hodnotu z posledního rozsahu v SCALE_RANGES
                largest_scale = SCALE_RANGES["25000"].max_scale
            
            # Přidáme trochu rezervu, aby bylo měřítko o něco větší
            # a zajistilo zobrazení dat
            target_scale = largest_scale * 0.99
            
            canvas.zoomScale(target_scale)
            canvas.refresh()
            
            #print(f"Zoom was completed successfully to scale 1:{target_scale}")
        
        # Použijeme delší zpoždění pro jistotu
        QTimer.singleShot(500, do_zoom)



    def _store_geometries(
        self,
        geom_list: List[Tuple[QgsGeometry, str, str]],
        record: ET.Element,
        record_type: Optional[str],
        tag_name: Optional[str],
        type_features: dict,
    ) -> int:
        """Uložení geometrií do type_features"""
        if len(geom_list) == 2:
            first_geom = geom_list[0]
            second_geom = geom_list[1]

            type_key = (
                f"{tag_name}_{record_type}_{first_geom[2]}"
                if tag_name and record_type
                else f"{tag_name}_{first_geom[2]}"
            )

            features_dict = type_features[type_key]
            if  features_dict["first_element"] is None:
                features_dict.update(
                    {
                        "first_element": record,
                        "tag_name": tag_name,
                        "type_value": record_type,
                        "geom_type": first_geom[2],
                        "second_geom_type": second_geom[2],
                        "has_second_geom": True,
                    }
                )

            features_dict["scale_features"].append(
                (record, first_geom[0], first_geom[1])
            )
            features_dict["features"].append((record, second_geom[0], second_geom[1]))
            return 1

        elif len(geom_list) == 1:
            geom, gml_id, geom_type = geom_list[0]
            type_key = (
                f"{tag_name}_{record_type}_{geom_type}"
                if tag_name and record_type
                else f"{tag_name}_{geom_type}"
            )

            features_dict = type_features[type_key]
            if features_dict["first_element"] is None:
                features_dict.update(
                    {
                        "first_element": record,
                        "tag_name": tag_name,
                        "type_value": record_type,
                        "geom_type": geom_type,
                        "has_second_geom": False,
                    }
                )

            features_dict["scale_features"].append((record, geom, gml_id))
            return 1

        return 0

    def _create_layers_for_types(
        self,
        type_features: dict,
        obj_type: ET.Element,
        config_type: str,
        groups: Dict[str, str]
    ):
        """Vytvoření vrstev pro všechny typy"""
        # print(f"\nCreating layers for {len(type_features)} types")

        for type_key, data in type_features.items():

            # print(f"{type_key} {data['tag_name']} {data['type_value']}")

            """
            print(f"\nProcessing type: {type_key}")
            print(f"Type value: {data['type_value']}")
            print(f"Geom type: {data['geom_type']}")
            """
            try:
                self._create_type_layers(type_key, data, obj_type, config_type, groups)
            except Exception as e:
                logger.error(f"Error creating layers for type {type_key}: {e}")
                print(f"Full error details: {str(e)}")

                continue

    def _create_type_layers(
        self,
        type_key: str,
        data: dict,
        obj_type: ET.Element,
        config_type: str,
        groups: Dict[str, str]
    ):
        """
        Vytvoření vrstev pro konkrétní typ.

        Args:
            type_key: Klíč typu
            data: Data pro vytvoření vrstev
            obj_type: Typ objektu
            config_type: Typ konfigurace
            groups: Slovník skupin pro organizaci vrstev
        """
        # Vytvoření základního názvu vrstvy
        code_num = obj_type.get("code_base")[7:]
        base_layer_name = f"{code_num} {obj_type.text}_{data['geom_type']}"

        base_layer_name1 = self._get_layer_name(
            obj_type, data["tag_name"], data["type_value"]
        )
        base_layer_name2 = base_layer_name1 + "_" + data["geom_type"]

        layer_name = f"{base_layer_name1}_{config_type}"

        scale_layer = self._create_scale_layer(
            base_layer_name, base_layer_name2, layer_name, data
        )

        # base_layer_name2 = base_layer_name + "_" + data['geom_type']

        second_geom_layer = None

        if data["has_second_geom"]:
            base_layer_name3 = base_layer_name1 + "_" + data["second_geom_type"]
            second_geom_layer = self._create_vector_layer(
                data["second_geom_type"], base_layer_name3
            )
            # Přidání atributů a features
            self.attr_processor.create_fields(second_geom_layer, data["first_element"])
            self.feature_processor.process_features(
                self._create_features(second_geom_layer, data["features"]),
                second_geom_layer,
            )

        self._add_layers_to_groups(second_geom_layer, scale_layer, layer_name, groups)

    def _create_vector_layer(
        self, geom_type: str, name: str
    ) -> Optional[QgsVectorLayer]:
        """
        Vytvoření vektorové vrstvy.

        Args:
            geom_type: Typ geometrie
            name: Název vrstvy

        Returns:
            QgsVectorLayer nebo None při chybě
        """
        layer = QgsVectorLayer(f"{geom_type}?crs=EPSG:5514", name, "memory")
        if not layer.isValid():
            logger.error(f"Error creating layer: {name}")
            return None
        return layer

    def _create_scale_layer(
        self, base_name: str, base_name2: str, layer_name: str, data: dict
    ) -> Optional[QgsVectorLayer]:
        """
        Vytvoření vrstvy pro měřítkové styly.
        """
        scale_layer = self._create_vector_layer(data["geom_type"], layer_name)
        if not scale_layer:
            # print("not scale_layer")
            return None

        self.attr_processor.create_fields(scale_layer, data["first_element"])
        self.feature_processor.process_features(
            self._create_features(scale_layer, data["scale_features"]), scale_layer
        )
        # print(f"Scale layer feature count after adding: {scale_layer.featureCount()}")

        # Nastavíme renderer před vrácením vrstvy
        renderer = self._create_scale_based_renderer(scale_layer, base_name, base_name2)
        if renderer:
            # print(f"Created renderer with {len(renderer.rootRule().children())} rules")
            scale_layer.setRenderer(renderer)

        return scale_layer

    def _create_features(
        self,
        layer: QgsVectorLayer,
        feature_data: List[Tuple[ET.Element, QgsGeometry, str]],
    ) -> List[QgsFeature]:

        features = []
        for elem, geom, gml_id in feature_data:
            feat = QgsFeature(layer.fields())
            attributes = self.attr_processor.get_attributes(elem, layer)
            attributes[0] = gml_id
            feat.setAttributes(attributes)
            feat.setGeometry(geom)
            features.append(feat)

        return features

    def _add_layers_to_groups(
        self,
        second_geom_layer: QgsVectorLayer,
        scale_layer: QgsVectorLayer,
        base_name: str,
        groups: Dict[str, str]
    ):
        """
        Přidání vrstev do dočasné struktury.
        """
        # Získáme cestu k rodičovské skupině
        parent_path = groups["skupina"]
        
        if parent_path not in self.temp_tree_structure:
            logger.error(f"Skupina s cestou {parent_path} není v temp_tree_structure")
        
        if second_geom_layer:
            if second_geom_layer.featureCount() > 0:
                self.temp_layers.append((second_geom_layer, parent_path, False))
            else:
                logger.warning(f"Druhá geometrická vrstva {second_geom_layer.name()} nemá žádné prvky - nebude přidána")

        if scale_layer:
            if scale_layer.featureCount() > 0:
                self.temp_layers.append((scale_layer, parent_path, True))
            else:
                logger.warning(f"Hlavní vrstva {scale_layer.name()} nemá žádné prvky - nebude přidána")
                
    def _get_layer_name(
        self, obj_type: ET.Element, tag_name: Optional[str], type_value: Optional[str]
    ) -> str:
        """
        Získání názvu vrstvy.

        Args:
            obj_type: Typ objektu
            tag_name: Název tagu
            type_value: Hodnota typu
            geom_type: Typ geometrie

        Returns:
            Název vrstvy
        """
        code_num = obj_type.get("code_base")[7:]
        base_name = f"{code_num} {obj_type.text}"

        if (
            tag_name
            and type_value
            and tag_name in self.attr_processor.value_mappings
            and type_value != "neveřejný údaj"
        ):
            return f"{base_name} - {type_value}"
        # print(f"{tag_name}")
        # Zkusíme najít výchozí typ pro tento objekt
        default_type = None

        if tag_name in self.attr_processor.value_mappings:
            # print("tag_name in self.attr_processor.value_mappings")
            # Hledáme výchozí hodnotu (často '0' nebo podobné)
            default_mapping = self.attr_processor.value_mappings.get(tag_name, {})
            if "0" in default_mapping and default_mapping["0"] != "neveřejný údaj":
                default_type = default_mapping["0"]
            else:
                if (
                    "98" in default_mapping
                ):  # někdy se používá 99 pro výchozí hodnotu nezjištěno/neurčeno
                    default_type = default_mapping["98"]
                elif (
                    "99" in default_mapping
                ):  # někdy se používá 98 pro výchozí hodnotu jiné
                    default_type = default_mapping["99"]

        if default_type:
            return f"{base_name} - {default_type}"

        return f"{base_name}"
    
    def _create_scale_based_renderer(
        self, layer: QgsVectorLayer, base_layer_name: str, base_layer_name2: str
    ) -> QgsRuleBasedRenderer:
        """Vytvoří renderer s pravidly pro různá měřítka"""
        # print(f"\nCreating renderer for layer: {layer.name()}")
        # print(f"Base layer name: {base_layer_name}")

        # Získáme typ geometrie z vrstvy
        """        geom_type = {
                0: "Point",         # QgsWkbTypes.Point
                1: "LineString",    # QgsWkbTypes.Line
                2: "Polygon"        # QgsWkbTypes.Polygon
            }.get(layer.geometryType())
            
        if not geom_type:
            print(f"Unknown geometry type: {layer.geometryType()}")
            return None
        """
        rules = []
        # print("\n")

        for scale, scale_range in SCALE_RANGES.items():
            min_scale = scale_range.min_scale
            max_scale = scale_range.max_scale

            meritkovy_rozsah = None
            if min_scale == 0:
                meritkovy_rozsah = f"0 - 1:{max_scale}"
            else:
                meritkovy_rozsah = f"1:{min_scale} - 1:{max_scale}"

            # Přidáme typ geometrie do klíče
            style_key = f"{base_layer_name2}_{scale}"
            # print(f"Looking for style with key 1: {style_key}")

            style_found = False
            if style_key in self.style_manager.style_df["key"].values:
                # print(f"Found direct match for style: {style_key}")
                style_row = self.style_manager.style_df[
                    self.style_manager.style_df["key"] == style_key
                ].iloc[0]
                style_found = True
            else:
                # Pokud obsahuje "nezjištěno/neurčeno", zkusíme varianty
                if "nezjištěno/neurčeno" in style_key:
                    # Zkusíme variantu s "nezjištěno"
                    style_key_nezjisteno = style_key.replace(
                        "nezjištěno/neurčeno", "nezjištěno"
                    )
                    # print(f"Looking for style with key 2: {style_key_nezjisteno}")
                    if (
                        style_key_nezjisteno
                        in self.style_manager.style_df["key"].values
                    ):
                        style_row = self.style_manager.style_df[
                            self.style_manager.style_df["key"] == style_key_nezjisteno
                        ].iloc[0]
                        style_found = True
                    else:
                        # Zkusíme variantu s "neurčeno"
                        style_key_neurceno = style_key.replace(
                            "nezjištěno/neurčeno", "neurčeno"
                        )
                        # print(f"Looking for style with key 3: {style_key_neurceno}")

                        if (
                            style_key_neurceno
                            in self.style_manager.style_df["key"].values
                        ):
                            style_row = self.style_manager.style_df[
                                self.style_manager.style_df["key"] == style_key_neurceno
                            ].iloc[0]
                            style_found = True

                # Pokud stále nemáme styl, zkusíme verzi bez typu
                if not style_found:

                    style_key_without_type = f"{base_layer_name}_{scale}"
                    # print(f"Looking for style with key 4: {style_key_without_type}")

                    if (
                        style_key_without_type
                        in self.style_manager.style_df["key"].values
                    ):
                        style_row = self.style_manager.style_df[
                            self.style_manager.style_df["key"] == style_key_without_type
                        ].iloc[0]
                        style_found = True

            if style_found:
                # print(f"Creating rule for scale {scale}")
                qgis_symbol_json = style_row.get("qgis_symbol")
                if qgis_symbol_json:
                    symbol_dict = json.loads(qgis_symbol_json)
                    symbol = self.symbol_processor.create_symbol_from_json(symbol_dict)
                    if symbol:
                        rule = QgsRuleBasedRenderer.Rule(symbol)
                        rule.setMinimumScale(max_scale)
                        rule.setMaximumScale(min_scale)
                        rule.setLabel(meritkovy_rozsah)
                        rule.setDescription(meritkovy_rozsah)
                        rules.append(rule)
                        # print(f"Rule created successfully")
                    else:
                        print("Failed to create symbol")
                else:
                    print("No symbol JSON found")

        root_rule = QgsRuleBasedRenderer.Rule(None)
        for rule in rules:
            root_rule.appendChild(rule)

        renderer = QgsRuleBasedRenderer(root_rule)
        if not renderer.rootRule().children():  # pokud renderer nemá žádná pravidla
            layer.setName(layer.name() + " (nenalezen styl)")

        # print(f"Created renderer with {len(rules)} rules")
        return renderer