# -*- coding: utf-8 -*-
import arcpy
import os
import re
import math
import urllib.parse
import xml.etree.ElementTree as ET
from src.utils import HttpClient, load_teryt_db


def extract_id(val):
    if not val:
        return None
    s = str(val).strip()
    if s.startswith("(") or s == "#":
        return None
    if "(" in s and ")" in s:
        return s.split("(")[-1].split(")")[0].strip()
    return s.strip()


def build_teryt_lookups(db):
    woj_lookup = {}
    pow_lookup = {}
    gmi_lookup = {}

    if not db:
        return woj_lookup, pow_lookup, gmi_lookup

    for w_code, w_name in db.get("wojewodztwa", {}).items():
        woj_lookup[str(w_code).strip()] = w_name

    for w_code, pows in db.get("powiaty", {}).items():
        for p_code, p_name in pows.items():
            pow_lookup[str(p_code).strip()] = p_name

    for p_code, gmis in db.get("gminy", {}).items():
        for g_code, g_name in gmis.items():
            clean_g = str(g_code).strip()
            gmi_lookup[clean_g] = g_name
            if "_" in clean_g:
                gmi_lookup[clean_g.split("_")[0]] = g_name

    return woj_lookup, pow_lookup, gmi_lookup


class PobierzPunktyAdresoweKINAImpl(object):
    WFS_URL = "https://mapy.geoportal.gov.pl/wss/ext/wfs/KrajowaIntegracjaNumeracjiAdresowej"
    DEFAULT_TILE_SIZE = 1500

    # Pola w 100% zgodne ze strukturą A07_Punkty_adresowe (QGIS) oraz danymi administracyjnymi
    FIELDS_DEFINITION = [
        ["WOJEWODZTWO", "TEXT", "Województwo", 60],
        ["POWIAT", "TEXT", "Powiat", 60],
        ["NAZWA_GMINY", "TEXT", "Nazwa gminy", 60],
        ["ID_GMINY", "TEXT", "ID gminy", 20],
        ["NAZWA_MIEJSCOWOSCI", "TEXT", "Nazwa miejscowości", 80],
        ["ID_MIEJSCOWOSCI", "TEXT", "ID miejscowości", 20],
        ["NAZWA_ULICY", "TEXT", "Nazwa ulicy", 100],
        ["ID_ULICY", "TEXT", "ID ulicy", 20],
        ["NUMER_PORZADKOWY", "TEXT", "Numer porządkowy", 20],
        ["KOD_POCZTOWY", "TEXT", "Kod pocztowy", 10],
        ["ID_IIP", "TEXT", "ID IIP", 150]
    ]

    FALLBACK_TYPENAME = "A07_Punkty_adresowe"

    def getParameterInfo(self):
        param_method = arcpy.Parameter(
            displayName="Metoda określenia obszaru",
            name="area_method",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_method.filter.list = [
            "Wskaż obszar na mapie lub wybierz warstwę",
            "Kaskadowy wybór z listy TERYT (Woj -> Pow -> Gmi)"
        ]
        param_method.value = "Wskaż obszar na mapie lub wybierz warstwę"

        param_geom = arcpy.Parameter(
            displayName="Wskaż obszar (ołówek) lub wybierz warstwę",
            name="input_features",
            datatype="GPFeatureRecordSetLayer",
            parameterType="Optional",
            direction="Input"
        )

        param_woj = arcpy.Parameter(
            displayName="Województwo",
            name="wojewodztwo",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_woj.filter.list = ["(Ładowanie słownika...)"]

        param_pow = arcpy.Parameter(
            displayName="Powiat",
            name="powiat",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_pow.filter.list = ["(Ładowanie słownika...)"]

        param_gmi = arcpy.Parameter(
            displayName="Gmina",
            name="gmina",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_gmi.filter.list = ["(Ładowanie słownika...)"]

        param_out_ws = arcpy.Parameter(
            displayName="Geobaza docelowa",
            name="out_ws",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            if aprx.defaultGeodatabase:
                param_out_ws.value = aprx.defaultGeodatabase
        except Exception:
            if arcpy.env.workspace:
                param_out_ws.value = arcpy.env.workspace

        param_out_name = arcpy.Parameter(
            displayName="Nazwa warstwy docelowej",
            name="out_name",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_out_name.value = "Pobrane_Punkty_Adresowe_KINA"

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
            param_method, param_geom, param_woj, param_pow, param_gmi,
            param_out_ws, param_out_name, param_author, param_derived
        ]

    def updateParameters(self, p):
        if not p or not p[0].value:
            return

        method = p[0].valueAsText
        is_geom = (method == "Wskaż obszar na mapie lub wybierz warstwę")
        is_teryt = (method == "Kaskadowy wybór z listy TERYT (Woj -> Pow -> Gmi)")

        p[1].enabled = is_geom
        p[2].enabled = is_teryt
        p[3].enabled = is_teryt
        p[4].enabled = is_teryt

        if len(p) > 7:
            p[7].enabled = False

        if not p[5].value:
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                if aprx.defaultGeodatabase:
                    p[5].value = aprx.defaultGeodatabase
            except Exception:
                if arcpy.env.workspace:
                    p[5].value = arcpy.env.workspace

        if is_teryt:
            db = load_teryt_db()
            if not db:
                p[2].setErrorMessage("Błąd: Plik 'slownik_teryt.json' nie został odnaleziony!")
                return
            else:
                p[2].clearMessage()

            woj_list = sorted([f"{v} ({k})" for k, v in db.get("wojewodztwa", {}).items()])
            if p[2].filter.list != woj_list:
                p[2].filter.list = woj_list

            woj_id = extract_id(p[2].valueAsText)

            pow_list = []
            if woj_id and woj_id in db.get("powiaty", {}):
                pow_list = sorted([f"{v} ({k})" for k, v in db["powiaty"][woj_id].items()])

            if p[3].filter.list != pow_list:
                p[3].filter.list = pow_list
                if p[3].valueAsText and p[3].valueAsText not in pow_list:
                    p[3].value = None

            pow_id = extract_id(p[3].valueAsText)

            gmi_list = []
            if pow_id and pow_id in db.get("gminy", {}):
                gmi_list = sorted([f"{v} ({k})" for k, v in db["gminy"][pow_id].items()])

            if p[4].filter.list != gmi_list:
                p[4].filter.list = gmi_list
                if p[4].valueAsText and p[4].valueAsText not in gmi_list:
                    p[4].value = None

    def _resolve_target_geometry(self, method, in_features, woj_id, pow_id, gmi_id, sr_2180, errors):
        if method == "Wskaż obszar na mapie lub wybierz warstwę":
            if not in_features:
                errors.append("Brak geometrii lub warstwy wejściowej.")
                return None
            geoms = []
            with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as cur:
                for row in cur:
                    g = row[0]
                    if not g:
                        continue
                    if g.spatialReference and g.spatialReference.factoryCode != 2180:
                        g = g.projectAs(sr_2180)
                    geoms.append(g)
            if not geoms:
                errors.append("Brak obiektów o prawidłowej geometrii.")
                return None
            combined = geoms[0]
            for gn in geoms[1:]:
                combined = combined.union(gn)
            return combined

        target_id = gmi_id or pow_id or woj_id
        if not target_id:
            errors.append("Nie wybrano żadnej jednostki ze słownika TERYT.")
            return None

        target_type = "Commune" if gmi_id else ("County" if pow_id else "Voivodeship")
        url = f"https://uldk.gugik.gov.pl/?request=Get{target_type}ById&id={target_id}&result=geom_wkt"

        try:
            resp = HttpClient.fetch_text(url, timeout=30)
            lines = [ln.strip() for ln in resp.split('\n') if ln.strip()]
            if lines and lines[0] == "0" and len(lines) >= 2:
                wkt = lines[1].split(';')[-1].strip()
                geom = arcpy.FromWKT(wkt, sr_2180)
                if geom and geom.area > 0:
                    return geom
        except Exception as e:
            errors.append(f"Błąd pobierania obrysu jednostki TERYT {target_id}: {e}")
            return None

        errors.append(f"Nie udało się uzyskać geometrii dla jednostki {target_id}.")
        return None

    def _generate_tiles(self, geom, tile_size):
        ext = geom.extent
        xmin, ymin, xmax, ymax = ext.XMin, ext.YMin, ext.XMax, ext.YMax

        cols = int(math.ceil((xmax - xmin) / float(tile_size))) or 1
        rows = int(math.ceil((ymax - ymin) / float(tile_size))) or 1

        tiles = []
        sr = geom.spatialReference

        for i in range(cols):
            x1 = xmin + i * tile_size
            x2 = min(x1 + tile_size, xmax)
            for j in range(rows):
                y1 = ymin + j * tile_size
                y2 = min(y1 + tile_size, ymax)

                tile_ext = arcpy.Extent(x1, y1, x2, y2)
                tile_poly = arcpy.Polygon(tile_ext.polygon.getPart(), sr)

                if not tile_poly.disjoint(geom):
                    tiles.append((x1, y1, x2, y2))
        return tiles

    def _extract_attr_value(self, attrs, f_name, context_woj=None, context_pow=None, context_gmi=None, lookups=None):
        woj_lookup, pow_lookup, gmi_lookup = lookups if lookups else ({}, {}, {})

        mapping = {
            "WOJEWODZTWO": ["wojewodztwo", "woj", "nazwa_wojewodztwa"],
            "POWIAT": ["powiat", "pow", "nazwa_powiatu"],
            "NAZWA_GMINY": ["nazwa_gminy", "gmina", "gmi"],
            "ID_GMINY": ["id_gminy", "kod_gminy", "teryt", "simc_gmi"],
            "NAZWA_MIEJSCOWOSCI": ["nazwa_miejscowosci", "miejscowosc", "czmiejscowosci", "nazwamiejscowosci"],
            "ID_MIEJSCOWOSCI": ["id_miejscowosci", "id_miejsc", "sym_miejsc", "sym"],
            "NAZWA_ULICY": ["nazwa_ulicy", "ulica", "nazwaulicy", "glownyczonulicy", "nazwaglowa"],
            "ID_ULICY": ["id_ulicy", "sym_ul", "symul", "identyfikator", "kod_ulicy"],
            "NUMER_PORZADKOWY": ["numer_porzadkowy", "numerporzadkowy", "numer", "nr"],
            "KOD_POCZTOWY": ["kod_pocztowy", "kodpocztowy", "kod"],
            "ID_IIP": ["id_iip", "gml_id", "lokalnyid", "iip_identyfikator"]
        }

        val = None
        for candidate in mapping.get(f_name, []):
            if candidate in attrs and attrs[candidate]:
                val = attrs[candidate].strip()
                break

        teryt_code = attrs.get("id_gminy") or attrs.get("kod_gminy") or attrs.get("teryt") or ""
        clean_teryt = str(teryt_code).strip()

        if f_name == "WOJEWODZTWO" and not val:
            if context_woj:
                val = context_woj
            elif len(clean_teryt) >= 2:
                val = woj_lookup.get(clean_teryt[:2])

        elif f_name == "POWIAT" and not val:
            if context_pow:
                val = context_pow
            elif len(clean_teryt) >= 4:
                val = pow_lookup.get(clean_teryt[:4])

        elif f_name == "NAZWA_GMINY" and not val:
            if context_gmi:
                val = context_gmi
            elif clean_teryt:
                val = gmi_lookup.get(clean_teryt)

        elif f_name == "ID_GMINY" and not val and clean_teryt:
            val = clean_teryt

        return val[:150] if val else None

    def _parse_gml_points(self, xml_bytes, errors):
        try:
            root = ET.fromstring(xml_bytes)
        except Exception as e:
            errors.append(f"Parsowanie GML punktów: {e}")
            return []

        features = []
        for el in root.iter():
            local = el.tag.split('}')[-1]
            local_l = local.lower()
            is_direct_feature = "punkt" in local_l and "adres" in local_l
            if local in ("member", "featureMember") or is_direct_feature:
                feat_node = el if is_direct_feature else list(el)[0] if len(list(el)) > 0 else None
                if feat_node is None:
                    continue

                attrs = {}
                for k, v in feat_node.attrib.items():
                    attr_name = k.split('}')[-1].lower()
                    if attr_name in ("id", "gml_id"):
                        attrs["gml_id"] = v

                wkt_geom = None
                for node in feat_node.iter():
                    tag = node.tag.split('}')[-1].lower()
                    if tag in ("pos", "poslist", "coordinates") and node.text:
                        coords = [float(c) for c in re.findall(r"[-+]?\d*\.\d+|\d+", node.text)]
                        if len(coords) >= 2:
                            x, y = coords[1], coords[0]  # Northing, Easting -> X, Y
                            wkt_geom = f"POINT ({x} {y})"

                    val = node.text.strip() if node.text else None
                    if val and len(list(node)) == 0:
                        attrs[tag] = val

                if wkt_geom:
                    features.append((wkt_geom, attrs))
        return features

    def execute(self, p, messages):
        method = p[0].valueAsText
        in_features = p[1].value
        raw_woj = p[2].valueAsText
        raw_pow = p[3].valueAsText
        raw_gmi = p[4].valueAsText

        woj_id = extract_id(raw_woj)
        pow_id = extract_id(raw_pow)
        gmi_id = extract_id(raw_gmi)

        ctx_woj = raw_woj.split('(')[0].strip() if raw_woj and '(' in raw_woj else raw_woj
        ctx_pow = raw_pow.split('(')[0].strip() if raw_pow and '(' in raw_pow else raw_pow
        ctx_gmi = raw_gmi.split('(')[0].strip() if raw_gmi and '(' in raw_gmi else raw_gmi

        out_ws = p[5].valueAsText
        raw_out_name = p[6].valueAsText

        safe_name = re.sub(r'[^\w]', '_', raw_out_name)
        if safe_name and safe_name[0].isdigit():
            safe_name = "L_" + safe_name

        out_name = arcpy.ValidateTableName(safe_name, out_ws)
        out_fc = os.path.join(out_ws, out_name)
        sr_2180 = arcpy.SpatialReference(2180)

        db = load_teryt_db()
        lookups = build_teryt_lookups(db)
        errors = []

        arcpy.SetProgressor("step", "Faza 1/3: Ustalanie geometrii obszaru pobierania...", 0, 1, 1)
        arcpy.AddMessage("Faza 1/3: Ustalanie geometrii obszaru pobierania...")
        geom = self._resolve_target_geometry(method, in_features, woj_id, pow_id, gmi_id, sr_2180, errors)
        if not geom:
            if errors:
                for err in errors: arcpy.AddError(err)
            return

        tiles = self._generate_tiles(geom, self.DEFAULT_TILE_SIZE)
        total_tiles = len(tiles)
        arcpy.AddMessage(f"Podzielono obszar na siatkę {total_tiles} kafelków.")

        processed_ids = set()
        if not arcpy.Exists(out_fc):
            arcpy.CreateFeatureclass_management(out_ws, out_name, "POINT", spatial_reference=sr_2180)
            arcpy.management.AddFields(out_fc, self.FIELDS_DEFINITION)
        else:
            try:
                check_field = "ID_IIP" if "ID_IIP" in [f.name for f in arcpy.ListFields(out_fc)] else "LOKALNYID"
                with arcpy.da.SearchCursor(out_fc, [check_field]) as cur:
                    for row in cur:
                        if row[0]:
                            processed_ids.add(str(row[0]).strip())
                arcpy.AddMessage(f"Wczytano {len(processed_ids)} istniejących obiektów z warstwy.")
            except Exception as e:
                errors.append(f"Odczyt istniejących obiektów: {e}")

        initial_count = len(processed_ids)
        field_names = [f[0] for f in self.FIELDS_DEFINITION]
        insert_fields = ["SHAPE@"] + field_names

        actual_typename = self.FALLBACK_TYPENAME
        try:
            cap_url = f"{self.WFS_URL}?SERVICE=WFS&REQUEST=GetCapabilities&VERSION=1.1.0"
            cap_xml = HttpClient.fetch_text(cap_url, timeout=20)
            found_names = re.findall(r"<(?:\w+:)?Name>([^<]+)</(?:\w+:)?Name>", cap_xml, re.IGNORECASE)
            for name in found_names:
                if "punkt" in name.lower() and "adres" in name.lower():
                    actual_typename = name.strip()
                    break
        except Exception:
            pass

        total_steps = 1 + total_tiles + 1
        current_step = 1
        arcpy.SetProgressor("step", f"Faza 2/3: Odpytywanie WFS KINA (0/{total_tiles})...", 0, total_steps, 1)
        arcpy.SetProgressorPosition(current_step)

        with arcpy.da.InsertCursor(out_fc, insert_fields) as cursor:
            for idx, (x1, y1, x2, y2) in enumerate(tiles, start=1):
                arcpy.SetProgressorLabel(f"Odpytywanie WFS KINA [{idx}/{total_tiles}]...")
                bbox_str = f"{y1},{x1},{y2},{x2},urn:ogc:def:crs:EPSG::2180"
                params = {
                    "SERVICE": "WFS",
                    "REQUEST": "GetFeature",
                    "VERSION": "1.1.0",
                    "TYPENAME": actual_typename,
                    "SRSNAME": "urn:ogc:def:crs:EPSG::2180",
                    "BBOX": bbox_str
                }
                url = f"{self.WFS_URL}?{urllib.parse.urlencode(params)}"

                try:
                    xml_data = HttpClient.fetch_bytes(url, timeout=40)
                    items = self._parse_gml_points(xml_data, errors)

                    for wkt_str, attrs in items:
                        unique_id = (
                            attrs.get("id_iip") or
                            attrs.get("gml_id") or
                            attrs.get("lokalnyid") or
                            attrs.get("id") or
                            f"{wkt_str}"
                        )
                        if unique_id in processed_ids:
                            continue

                        feat_geom = arcpy.FromWKT(wkt_str, sr_2180)
                        if not feat_geom or feat_geom.disjoint(geom):
                            continue

                        row_vals = [feat_geom]
                        for f_name in field_names:
                            val = self._extract_attr_value(
                                attrs, f_name,
                                context_woj=ctx_woj,
                                context_pow=ctx_pow,
                                context_gmi=ctx_gmi,
                                lookups=lookups
                            )
                            row_vals.append(val)

                        try:
                            cursor.insertRow(row_vals)
                            processed_ids.add(unique_id)
                        except Exception as ins_err:
                            errors.append(f"Zapis punktu {unique_id}: {ins_err}")

                except Exception as e:
                    errors.append(f"Kafel [{idx}/{total_tiles}] pominięty: {e}")

                current_step += 1
                arcpy.SetProgressorPosition(current_step)

        arcpy.AddMessage(f"Faza 3/3: Dodano nowych punktów adresowych: {len(processed_ids) - initial_count}")

        if len(p) > 8:
            p[8].value = out_fc

        arcpy.SetProgressorLabel("Dodawanie warstwy do aktywnej mapy...")
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_map = aprx.activeMap
            if active_map:
                layers = active_map.listLayers(out_name)
                if not layers:
                    active_map.addDataFromPath(out_fc)
        except Exception as e:
            errors.append(f"Błąd dodawania do widoku mapy: {e}")

        arcpy.SetProgressorPosition(total_steps)
        arcpy.ResetProgressor()

        if errors:
            arcpy.AddWarning(f"--- Raport ostrzeżeń i błędów ({len(errors)}) ---")
            for err in errors:
                arcpy.AddWarning(f"  • {err}")

        arcpy.AddMessage("Zakończono pomyślnie!")