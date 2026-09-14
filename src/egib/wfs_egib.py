# -*- coding: utf-8 -*-
import arcpy
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from src.utils import HttpClient


class PobierzSkorowidzEGIBImpl(object):
    """
    Pobiera geometrie działek i/lub budynków ze Zbiorczej Usługi WFS EGiB GUGiK
    dla wskazanego obszaru. Rozbija atrybuty WFS na dedykowane kolumny tabeli.
    """

    WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza"

    IGNORED_TAGS = {
        "boundedby", "envelope", "lowercorner", "uppercorner",
        "poslist", "polygon", "multipolygon", "surface", "multisurface",
        "exterior", "interior", "linearring", "patch", "patches",
        "polygonpatch", "geometry", "geom", "the_geom", "shape"
    }

    def getParameterInfo(self):
        param_geom = arcpy.Parameter(
            displayName="Wskaż obszar na mapie (ołówek) lub wybierz warstwę",
            name="input_features",
            datatype="GPFeatureRecordSetLayer",
            parameterType="Required",
            direction="Input"
        )

        param_typ = arcpy.Parameter(
            displayName="Typ obiektów do pobrania",
            name="typ_obiektu",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_typ.filter.list = ["Działki", "Budynki"]
        param_typ.value = "Działki"

        param_out_ws = arcpy.Parameter(
            displayName="Geobaza docelowa / Folder",
            name="out_ws",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )
        try:
            if arcpy.env.workspace:
                param_out_ws.value = arcpy.env.workspace
        except Exception:
            pass

        param_out_name = arcpy.Parameter(
            displayName="Nazwa warstwy (istniejącej lub nowej)",
            name="out_name",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_out_name.value = "Pobrane_obiekty_WFS"

        param_author = arcpy.Parameter(
            displayName="Informacje o narzędziu",
            name="author_info",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_author.value = "Autor: Mateusz Lubański"

        param_derived = arcpy.Parameter(
            displayName="Wynikowa warstwa (Ukryta)",
            name="out_layer",
            datatype="GPFeatureLayer",
            parameterType="Derived",
            direction="Output"
        )

        return [
            param_geom, param_typ, param_out_ws,
            param_out_name, param_author, param_derived
        ]

    def updateParameters(self, p):
        if len(p) > 4:
            p[4].enabled = False

    def _split_bbox(self, ext, tile_size=500.0):
        xmin, xmax = ext.XMin, ext.XMax
        ymin, ymax = ext.YMin, ext.YMax

        if xmin == xmax:
            xmin -= 2.0
            xmax += 2.0
        if ymin == ymax:
            ymin -= 2.0
            ymax += 2.0

        bboxes = []
        x = xmin
        while x < xmax:
            next_x = min(x + tile_size, xmax)
            y = ymin
            while y < ymax:
                next_y = min(y + tile_size, ymax)
                bboxes.append((x, y, next_x, next_y))
                y = next_y
            x = next_x
        return bboxes

    def _get_wfs_features(self, bbox_2180, type_name, errors):
        xmin, ymin, xmax, ymax = bbox_2180
        bbox_param = f"{ymin},{xmin},{ymax},{xmax},urn:ogc:def:crs:EPSG::2180"

        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": type_name,
            "bbox": bbox_param,
        }
        url = f"{self.WFS_URL}?{urllib.parse.urlencode(params)}"

        try:
            data = HttpClient.fetch_bytes(url, timeout=45)
        except Exception as e:
            errors.append(f"Zapytanie WFS (BBOX: {bbox_param}): {e}")
            return []

        if not data or b"ExceptionReport" in data:
            return []

        try:
            root = ET.fromstring(data)
        except Exception as e:
            errors.append(f"Błąd parsowania XML z WFS: {e}")
            return []

        features_data = []
        for feat in root.iter():
            local = feat.tag.split("}")[-1]
            if local in ("member", "featureMember"):
                children = list(feat)
                if not children:
                    continue
                child_elem = children[0]

                attrs = {}
                wkt_geom = None

                pos_lists = child_elem.findall(".//{*}posList")
                if pos_lists:
                    rings = []
                    for pl in pos_lists:
                        if not pl.text:
                            continue
                        coords = pl.text.strip().split()
                        pts = []
                        for i in range(0, len(coords), 2):
                            northing, easting = float(coords[i]), float(coords[i+1])
                            pts.append(f"{easting} {northing}")
                        if pts:
                            rings.append(f"(({', '.join(pts)}))")

                    if rings:
                        wkt_geom = f"POLYGON{rings[0]}" if len(rings) == 1 else f"MULTIPOLYGON({', '.join(rings)})"

                for child in child_elem.iter():
                    tag = child.tag.split("}")[-1]
                    if tag.lower() in self.IGNORED_TAGS:
                        continue
                    if not list(child) and child.text and child.text.strip():
                        attrs[tag.upper()] = child.text.strip()

                if wkt_geom:
                    features_data.append((wkt_geom, attrs))

        return features_data

    def _ensure_fields(self, out_fc, out_ws, field_names):
        existing_fields = {f.name.upper(): f.name for f in arcpy.ListFields(out_fc)}
        field_map = {}

        for attr_name in field_names:
            valid_name = arcpy.ValidateFieldName(attr_name, out_ws)
            val_upper = valid_name.upper()

            if val_upper not in existing_fields:
                arcpy.management.AddField(out_fc, valid_name, "TEXT", field_length=500)
                existing_fields[val_upper] = valid_name
                field_map[attr_name] = valid_name
            else:
                field_map[attr_name] = existing_fields[val_upper]

        return field_map

    def execute(self, p, messages):
        in_features = p[0].value
        typ_obj = p[1].valueAsText
        type_name = "ms:dzialki" if "Działki" in typ_obj else "ms:budynki"

        out_ws = p[2].valueAsText
        raw_out_name = p[3].valueAsText

        # Zachowanie polskich znaków diakrytycznych
        safe_name = re.sub(r'[^\w]', '_', raw_out_name)
        if safe_name and safe_name[0].isdigit():
            safe_name = "L_" + safe_name

        out_name = arcpy.ValidateTableName(safe_name, out_ws)
        out_fc = os.path.join(out_ws, out_name)
        sr_2180 = arcpy.SpatialReference(2180)

        processed_ids = set()
        errors = []

        if not arcpy.Exists(out_fc):
            arcpy.CreateFeatureclass_management(out_ws, out_name, "POLYGON", spatial_reference=sr_2180)
            arcpy.management.AddField(out_fc, "ID_OBIEKTU", "TEXT", field_length=100)
            arcpy.AddMessage(f"Utworzono nową warstwę: {out_name}")
        else:
            try:
                with arcpy.da.SearchCursor(out_fc, ["ID_OBIEKTU"]) as sc:
                    for r in sc:
                        if r[0]:
                            processed_ids.add(r[0])
                arcpy.AddMessage(f"Warstwa '{out_name}' istnieje. Wczytano {len(processed_ids)} istniejących obiektów.")
            except Exception as e:
                arcpy.AddError(f"Błąd odczytu istniejącej warstwy: {e}")
                return

        src_sr = arcpy.Describe(in_features).spatialReference
        initial_count = len(processed_ids)

        arcpy.AddMessage(f"Pobieranie obiektów z WFS EGiB ({type_name})...")

        # Zbieranie geometrii wejściowych i kafelków
        all_work_tiles = []
        with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as search_cur:
            for row in search_cur:
                input_geom = row[0]
                if not input_geom:
                    continue

                if src_sr and src_sr.factoryCode != 2180:
                    input_geom = input_geom.projectAs(sr_2180)

                ext = input_geom.extent
                sub_bboxes = self._split_bbox(ext, tile_size=500.0)

                for bbox in sub_bboxes:
                    tile_poly = arcpy.Polygon(arcpy.Array([
                        arcpy.Point(bbox[0], bbox[1]),
                        arcpy.Point(bbox[0], bbox[3]),
                        arcpy.Point(bbox[2], bbox[3]),
                        arcpy.Point(bbox[2], bbox[1])
                    ]), sr_2180)

                    if not input_geom.disjoint(tile_poly):
                        all_work_tiles.append((bbox, input_geom))

        total_tiles = len(all_work_tiles)
        if total_tiles == 0:
            return arcpy.AddWarning("Brak kafelków do przetworzenia.")

        # Total steps: pobieranie kafli + zapis do bazy + dodanie do mapy
        total_steps = total_tiles + 2
        current_step = 0
        arcpy.SetProgressor("step", f"Odpytywanie WFS EGiB (0/{total_tiles})...", 0, total_steps, 1)

        features_to_save = []
        all_attr_keys = set()

        for idx, (bbox, input_geom) in enumerate(all_work_tiles, start=1):
            arcpy.SetProgressorLabel(f"Odpytywanie WFS EGiB [{idx}/{total_tiles}]...")
            features = self._get_wfs_features(bbox, type_name, errors)

            for wkt, attrs in features:
                try:
                    poly_geom = arcpy.FromWKT(wkt, sr_2180)
                    if poly_geom and not poly_geom.disjoint(input_geom):
                        obj_id = (
                            attrs.get("ID_DZIALKI") or
                            attrs.get("IDDZIALKI") or
                            attrs.get("ID_BUDYNKU") or
                            attrs.get("IDBUDYNKU") or
                            attrs.get("GML_ID") or
                            "Nieznany"
                        )

                        if obj_id in processed_ids and obj_id != "Nieznany":
                            continue

                        processed_ids.add(obj_id)
                        all_attr_keys.update(attrs.keys())
                        features_to_save.append((poly_geom, obj_id, attrs))
                except Exception as ge:
                    errors.append(f"Geometria WKT obiektu: {ge}")

            current_step += 1
            arcpy.SetProgressorPosition(current_step)

        # Zapis do geobazy
        current_step += 1
        arcpy.SetProgressorLabel(f"Zapisywanie {len(features_to_save)} obiektów do geobazy...")
        arcpy.SetProgressorPosition(current_step)

        if features_to_save:
            field_map = self._ensure_fields(out_fc, out_ws, sorted(list(all_attr_keys)))
            attr_cols = sorted(list(field_map.keys()))
            db_cols = [field_map[col] for col in attr_cols]

            insert_fields = ["SHAPE@", "ID_OBIEKTU"] + db_cols
            with arcpy.da.InsertCursor(out_fc, insert_fields) as cursor:
                for poly_geom, obj_id, attrs in features_to_save:
                    try:
                        row_vals = [poly_geom, obj_id] + [attrs.get(k) for k in attr_cols]
                        cursor.insertRow(row_vals)
                    except Exception as ins_err:
                        errors.append(f"Błąd zapisu obiektu {obj_id}: {ins_err}")

        added_count = len(processed_ids) - initial_count
        arcpy.AddMessage(f"Zakończono! Łącznie pobrano i dopisano obiektów: {added_count}")

        if added_count == 0:
            arcpy.AddWarning("Brak pobranych obiektów. Sprawdź stan usługi dla wybranego powiatu na stronie EZiUDP.")

        if len(p) > 5:
            p[5].value = out_fc

            # Dodanie do aktywnej mapy
            current_step += 1
            arcpy.SetProgressorLabel("Aktualizacja widoku mapy...")
            arcpy.SetProgressorPosition(current_step)

            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                active_map = aprx.activeMap

                if active_map:
                    layers = active_map.listLayers(out_name)
                    if not layers:
                        active_map.addDataFromPath(out_fc)
                        arcpy.AddMessage("Dodano warstwę do aktywnej mapy.")
            except Exception as map_err:
                errors.append(f"Nie udało się zaktualizować mapy: {map_err}")

        arcpy.SetProgressorPosition(total_steps)
        arcpy.ResetProgressor()

        if errors:
            arcpy.AddWarning(f"--- Raport ostrzeżeń i błędów ({len(errors)}) ---")
            for err in errors:
                arcpy.AddWarning(f"  • {err}")

        arcpy.AddMessage("Zakończono pomyślnie!")