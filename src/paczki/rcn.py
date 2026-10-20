# -*- coding: utf-8 -*-
try:
    import arcpy
except ImportError:
    arcpy = None
import os
import re
import math
import datetime
import urllib.parse
import xml.etree.ElementTree as ET
from src.utils import HttpClient, ToolCancelled, load_teryt_db


def extract_id(val):
    if not val:
        return None
    s = str(val).strip()
    if s.startswith("(") or s == "#":
        return None
    if "(" in s and ")" in s:
        return s.split("(")[-1].split(")")[0].strip()
    return s.strip()


def parse_date_str(s):
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{4})$", s)
    if m:
        return m.group(1)
    return None


def parse_date_range(text):
    if not text:
        return None, None
    s = str(text).strip()
    s_lower = s.lower()
    today = datetime.date.today()

    if "wszystk" in s_lower or s == "#" or not s or "kalendarz" in s_lower:
        return None, None

    if "ostatni rok" in s_lower or "1 rok" in s_lower:
        d_from = (today - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        return d_from, today.strftime("%Y-%m-%d")
    if "2 lata" in s_lower or "2 lat" in s_lower:
        d_from = (today - datetime.timedelta(days=730)).strftime("%Y-%m-%d")
        return d_from, today.strftime("%Y-%m-%d")
    if "5 lat" in s_lower:
        d_from = (today - datetime.timedelta(days=1825)).strftime("%Y-%m-%d")
        return d_from, today.strftime("%Y-%m-%d")

    # Zakres YYYY - YYYY (np. 2020-2024 lub 2020 - 2024)
    m = re.match(r"^\s*(\d{4})\s*[-–—:]\s*(\d{4})\s*$", s)
    if m:
        return f"{m.group(1)}-01-01", f"{m.group(2)}-12-31"

    # Pojedynczy rok YYYY
    m = re.match(r"^\s*(\d{4})\s*$", s)
    if m:
        return f"{m.group(1)}-01-01", f"{m.group(1)}-12-31"

    # Zakres z separatorem np. 2022-05-01 - 2024-01-01 lub 01.05.2022 do 01.01.2024
    if " do " in s_lower:
        parts = re.split(r"\s+do\s+", s, flags=re.IGNORECASE)
    elif any(sep in s for sep in [" - ", " – ", " — "]):
        parts = re.split(r"\s+[-–—]\s+", s)
    elif ":" in s:
        parts = s.split(":")
    else:
        parts = None

    if parts and len(parts) >= 2:
        p1, p2 = parse_date_str(parts[0]), parse_date_str(parts[1])
        d1 = f"{p1}-01-01" if (p1 and len(p1) == 4) else p1
        d2 = f"{p2}-12-31" if (p2 and len(p2) == 4) else p2
        return d1, d2

    # od / do
    m_od = re.match(r"^(?:od|>=|>)\s*(.+)$", s_lower)
    if m_od:
        p1 = parse_date_str(m_od.group(1))
        return (f"{p1}-01-01" if (p1 and len(p1) == 4) else p1), None
    m_do = re.match(r"^(?:do|<=|<)\s*(.+)$", s_lower)
    if m_do:
        p2 = parse_date_str(m_do.group(1))
        return None, (f"{p2}-12-31" if (p2 and len(p2) == 4) else p2)

    return None, None


def format_date_param(val):
    """Konwertuje parametr daty (datetime / string) na format YYYY-MM-DD."""
    if not val:
        return None
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return s[:10]


class PobierzRCNImpl(object):
    """
    Pobiera dane z Rejestru Cen Nieruchomości (RCN) GUGiK za pomocą usługi WFS.
    Obsługuje warstwy: Działki, Lokale, Budynki.
    Umożliwia wybór powiatu ze słownika TERYT lub wskazanie obszaru na mapie,
    a także opcjonalne filtrowanie po dacie transakcji.
    """

    WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/rcn"
    BATCH_SIZE = 500

    IGNORED_TAGS = {
        "boundedby", "envelope", "lowercorner", "uppercorner",
        "poslist", "pos", "coordinates", "polygon", "multipolygon",
        "surface", "multisurface", "exterior", "interior", "linearring",
        "patch", "patches", "polygonpatch", "geometry", "geom", "the_geom",
        "shape", "point", "surfacemember", "pointmember"
    }

    LAYER_CONFIG = {
        "Działki": {
            "typename": "ms:dzialki",
            "geom_type": "POLYGON",
            "suffix": "Dzialki",
            "id_field": "DZI_ID_DZIALKI"
        },
        "Lokale": {
            "typename": "ms:lokale",
            "geom_type": "POINT",
            "suffix": "Lokale",
            "id_field": "LOK_ID_LOKALU"
        },
        "Budynki": {
            "typename": "ms:budynki",
            "geom_type": "POLYGON",
            "suffix": "Budynki",
            "id_field": "BUD_ID_BUDYNKU"
        }
    }

    def _get_number_matched(self, type_name, filter_xml=None):
        """Szybkie zapytanie WFS RESULTTYPE=hits w celu ustalenia całkowitej liczby obiektów."""
        try:
            url_params = {
                "SERVICE": "WFS",
                "REQUEST": "GetFeature",
                "VERSION": "2.0.0",
                "TYPENAMES": type_name,
                "RESULTTYPE": "hits"
            }
            if filter_xml:
                url_params["FILTER"] = filter_xml
            req_url = f"{self.WFS_URL}?{urllib.parse.urlencode(url_params)}"
            xml_text = HttpClient.fetch_text(req_url, timeout=15)
            if "numberMatched=" in xml_text:
                val = xml_text.split("numberMatched=")[1].split()[0].replace('"', "").replace("'", "")
                return int(val)
        except Exception:
            pass
        return None

    def getParameterInfo(self):
        # 0. Metoda określenia obszaru
        param_method = arcpy.Parameter(
            displayName="Metoda określenia obszaru",
            name="area_method",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_method.filter.list = [
            "Wybór powiatu z listy TERYT",
            "Wskaż obszar na mapie lub wybierz warstwę"
        ]
        param_method.value = "Wybór powiatu z listy TERYT"

        # 1. Województwo
        db = load_teryt_db()
        woj_list = []
        if db and isinstance(db, dict) and "wojewodztwa" in db:
            woj_list = sorted([f"{v} ({k})" for k, v in db.get("wojewodztwa", {}).items()])

        param_woj = arcpy.Parameter(
            displayName="Województwo",
            name="wojewodztwo",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_woj.filter.list = woj_list

        # 2. Powiat
        param_pow = arcpy.Parameter(
            displayName="Powiat",
            name="powiat",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_pow.filter.list = []

        # 3. Gmina (opcjonalna – jeśli pusta, pobierane są dane dla całego powiatu)
        param_gmina = arcpy.Parameter(
            displayName="Gmina (opcjonalna – pozostaw puste aby pobrać cały powiat)",
            name="gmina",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_gmina.filter.list = []

        # 4. Obszar na mapie
        param_geom = arcpy.Parameter(
            displayName="Wskaż obszar na mapie (ołówek) lub wybierz warstwę",
            name="input_features",
            datatype="GPFeatureRecordSetLayer",
            parameterType="Optional",
            direction="Input"
        )

        # 4. Warstwy do pobrania
        param_layers = arcpy.Parameter(
            displayName="Warstwy transakcji do pobrania",
            name="warstwy",
            datatype="GPString",
            parameterType="Required",
            direction="Input",
            multiValue=True
        )
        param_layers.filter.list = ["Działki", "Lokale", "Budynki"]
        param_layers.value = ["Działki", "Lokale", "Budynki"]

        # 5. Data transakcji od (kalendarz, tylko data)
        param_date_from = arcpy.Parameter(
            displayName="Data transakcji od",
            name="date_from",
            datatype="GPDate",
            parameterType="Optional",
            direction="Input"
        )

        # 6. Data transakcji do (kalendarz, tylko data)
        param_date_to = arcpy.Parameter(
            displayName="Data transakcji do",
            name="date_to",
            datatype="GPDate",
            parameterType="Optional",
            direction="Input"
        )

        # 7. Geobaza docelowa
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

        # 8. Nadpisz istniejące
        param_overwrite = arcpy.Parameter(
            displayName="Nadpisz istniejące warstwy w geobazie",
            name="overwrite",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_overwrite.value = False

        # 9. Wynik (ukryty)
        param_derived = arcpy.Parameter(
            displayName="Wynikowa warstwa (Ukryta)",
            name="out_layer",
            datatype="GPFeatureLayer",
            parameterType="Derived",
            direction="Output"
        )

        return [
            param_method, param_woj, param_pow, param_gmina, param_geom,
            param_layers, param_date_from, param_date_to,
            param_out_ws, param_overwrite, param_derived
        ]


    def updateParameters(self, p):
        if not p or len(p) < 8:
            return

        method = p[0].valueAsText or "Wybór powiatu z listy TERYT"
        is_teryt = (method == "Wybór powiatu z listy TERYT")

        p[1].enabled = is_teryt   # woj
        p[2].enabled = is_teryt   # pow
        p[3].enabled = is_teryt   # gmina
        p[4].enabled = not is_teryt  # geom

        if is_teryt:
            db = load_teryt_db()
            if db and isinstance(db, dict):
                # Kaskada powiatów z województwa
                woj_val = p[1].valueAsText
                woj_id = woj_val.split('(')[-1].replace(')', '').strip() if woj_val and '(' in woj_val else None

                pow_list = []
                if woj_id and woj_id in db.get("powiaty", {}):
                    pow_list = sorted([f"{v} ({k})" for k, v in db["powiaty"][woj_id].items()])

                if p[2].filter.list != pow_list:
                    p[2].filter.list = pow_list
                    if p[2].valueAsText and p[2].valueAsText not in pow_list:
                        p[2].value = None
                        p[3].value = None

                # Kaskada gmin z powiatu
                pow_val = p[2].valueAsText
                pow_id = pow_val.split('(')[-1].replace(')', '').strip() if pow_val and '(' in pow_val else None

                gmina_list = []
                if pow_id and pow_id in db.get("gminy", {}):
                    gmina_list = sorted([f"{v} ({k})" for k, v in db["gminy"][pow_id].items()])

                if p[3].filter.list != gmina_list:
                    p[3].filter.list = gmina_list
                    if p[3].valueAsText and p[3].valueAsText not in gmina_list:
                        p[3].value = None

    def updateMessages(self, p):
        if not p or len(p) < 8:
            return

        d_from = p[6].value
        d_to = p[7].value

        # Jeśli nie określono żadnej daty
        if not d_from and not d_to:
            p[6].setWarningMessage(
                "Brak ograniczenia dat: pobieranie wszystkich archiwalnych transakcji z RCN dla wybranego obszaru może potrwać od kilku do kilkunastu minut. Wskazanie zakresu dat przyspieszy pobieranie."
            )
            return

        # Jeśli podano obie daty
        if d_from and d_to:
            try:
                date_f = d_from.date() if hasattr(d_from, "date") else d_from
                date_t = d_to.date() if hasattr(d_to, "date") else d_to
                if date_f > date_t:
                    p[6].setErrorMessage("Data początkowa nie może być późniejsza niż data końcowa.")
                    return
                delta_days = (date_t - date_f).days
                if delta_days > 365 * 3:
                    years = round(delta_days / 365.25, 1)
                    p[7].setWarningMessage(
                        f"Wybrano szeroki zakres czasowy ({years} lat). Pobieranie dużej liczby transakcji przez WFS GUGiK może potrwać kilka minut."
                    )
            except Exception:
                pass




    def _parse_geometry(self, elem):
        """
        Parsuje geometrię GML w układzie EPSG:2180.
        W usłudze GUGiK RCN kolejność współrzędnych to Northing (Y), Easting (X).
        Zwraca WKT z kolejnością OGC (X Y).
        """
        # 1. Point
        point_elem = elem.find(".//{*}Point")
        if point_elem is not None:
            pos_elem = point_elem.find(".//{*}pos")
            if pos_elem is not None and pos_elem.text:
                parts = pos_elem.text.strip().split()
                if len(parts) >= 2:
                    northing, easting = float(parts[0]), float(parts[1])
                    return f"POINT ({easting} {northing})"

        # 2. Polygons & MultiSurface
        polygons = []
        poly_elems = elem.findall(".//{*}Polygon")
        for poly_el in poly_elems:
            rings = []
            ext_el = poly_el.find(".//{*}exterior")
            if ext_el is not None:
                pl = ext_el.find(".//{*}posList")
                if pl is not None and pl.text:
                    coords = pl.text.strip().split()
                    pts = []
                    for i in range(0, len(coords) - 1, 2):
                        northing, easting = float(coords[i]), float(coords[i+1])
                        pts.append(f"{easting} {northing}")
                    if pts:
                        rings.append(f"({', '.join(pts)})")

            for int_el in poly_el.findall(".//{*}interior"):
                pl = int_el.find(".//{*}posList")
                if pl is not None and pl.text:
                    coords = pl.text.strip().split()
                    pts = []
                    for i in range(0, len(coords) - 1, 2):
                        northing, easting = float(coords[i]), float(coords[i+1])
                        pts.append(f"{easting} {northing}")
                    if pts:
                        rings.append(f"({', '.join(pts)})")

            if rings:
                polygons.append(f"({', '.join(rings)})")

        if len(polygons) == 1:
            return f"POLYGON {polygons[0]}"
        elif len(polygons) > 1:
            return f"MULTIPOLYGON ({', '.join(polygons)})"

        # Fallback na bezpośredni posList
        pos_lists = elem.findall(".//{*}posList")
        if pos_lists:
            rings = []
            for pl in pos_lists:
                if not pl.text:
                    continue
                coords = pl.text.strip().split()
                pts = []
                for i in range(0, len(coords) - 1, 2):
                    northing, easting = float(coords[i]), float(coords[i+1])
                    pts.append(f"{easting} {northing}")
                if pts:
                    rings.append(f"(({', '.join(pts)}))")
            if len(rings) == 1:
                return f"POLYGON{rings[0]}"
            elif len(rings) > 1:
                return f"MULTIPOLYGON({', '.join(rings)})"

        return None

    def _build_fes_filter(self, teryt_code=None, bbox=None, date_from=None, date_to=None):
        """Buduje filtr OGC FES 2.0.0 dla zapytania WFS."""
        conditions = []

        if teryt_code:
            conditions.append(
                f"<fes:PropertyIsEqualTo>"
                f"<fes:ValueReference>TERYT</fes:ValueReference>"
                f"<fes:Literal>{teryt_code}</fes:Literal>"
                f"</fes:PropertyIsEqualTo>"
            )
        elif bbox:
            xmin, ymin, xmax, ymax = bbox
            conditions.append(
                f"<fes:BBOX>"
                f"<fes:ValueReference>GEOMETRIA</fes:ValueReference>"
                f"<gml:Envelope srsName=\"urn:ogc:def:crs:EPSG::2180\">"
                f"<gml:lowerCorner>{ymin} {xmin}</gml:lowerCorner>"
                f"<gml:upperCorner>{ymax} {xmax}</gml:upperCorner>"
                f"</gml:Envelope>"
                f"</fes:BBOX>"
            )

        if date_from:
            conditions.append(
                f"<fes:PropertyIsGreaterThanOrEqualTo>"
                f"<fes:ValueReference>DOK_DATA</fes:ValueReference>"
                f"<fes:Literal>{date_from}</fes:Literal>"
                f"</fes:PropertyIsGreaterThanOrEqualTo>"
            )

        if date_to:
            conditions.append(
                f"<fes:PropertyIsLessThanOrEqualTo>"
                f"<fes:ValueReference>DOK_DATA</fes:ValueReference>"
                f"<fes:Literal>{date_to}</fes:Literal>"
                f"</fes:PropertyIsLessThanOrEqualTo>"
            )

        if not conditions:
            return None

        if len(conditions) == 1:
            body = conditions[0]
        else:
            body = f"<fes:And>{''.join(conditions)}</fes:And>"

        return (
            f'<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" '
            f'xmlns:gml="http://www.opengis.net/gml/3.2">'
            f'{body}'
            f'</fes:Filter>'
        )

    def _ensure_fields(self, out_fc, out_ws, field_names):
        existing_fields = {f.name.upper(): f for f in arcpy.ListFields(out_fc)}
        field_map = {}

        for attr_name in field_names:
            valid_name = arcpy.ValidateFieldName(attr_name, out_ws)
            val_upper = valid_name.upper()

            if val_upper not in existing_fields:
                try:
                    arcpy.management.AddField(out_fc, valid_name, "TEXT", field_length=10000)
                    field_map[attr_name] = valid_name
                except Exception:
                    pass
            else:
                existing_f = existing_fields[val_upper]
                field_map[attr_name] = existing_f.name
                # Jeśli pole już istnieje w tabeli i ma mniejszy rozmiar niż 10000, powiększ je
                if existing_f.type == "String" and existing_f.length < 10000:
                    try:
                        arcpy.management.AlterField(out_fc, existing_f.name, field_length=10000)
                    except Exception:
                        pass

        return field_map


    def execute(self, p, messages):
        method = p[0].valueAsText
        raw_woj = p[1].valueAsText
        raw_pow = p[2].valueAsText
        raw_gmina = p[3].valueAsText
        in_features = p[4].value

        # Warstwy
        raw_layers = []
        if hasattr(p[5], "values") and p[5].values:
            raw_layers = [str(v).strip().strip("'\"") for v in p[5].values]
        elif p[5].valueAsText:
            raw_layers = [v.strip().strip("'\"") for v in p[5].valueAsText.split(";") if v.strip()]

        selected_layers = [l for l in raw_layers if l in self.LAYER_CONFIG]
        if not selected_layers:
            arcpy.AddError("Nie wybrano żadnej prawidłowej warstwy do pobrania.")
            return

        date_from = format_date_param(p[6].value)
        date_to = format_date_param(p[7].value)

        out_ws = p[8].valueAsText
        overwrite = bool(p[9].value)

        sr_2180 = arcpy.SpatialReference(2180)
        teryt_code = None
        gmina_teryt6 = None   # 6-cyfrowy kod gminy do filtrowania po id pola
        bbox = None
        input_geom = None
        location_label = "Obszar"

        if method == "Wybór powiatu z listy TERYT":
            if not raw_pow:
                arcpy.AddError("Wymagany jest wybór powiatu ze słownika TERYT.")
                return
            teryt_code = extract_id(raw_pow)
            if not teryt_code or len(teryt_code) < 4:
                arcpy.AddError(f"Nie udało się ustalić kodu TERYT powiatu z wartości: {raw_pow}")
                return
            teryt_code = teryt_code[:4]

            # Nazwa lokalizacji do nazwy warstwy
            pow_name = raw_pow.split('(')[0].strip()
            pow_name = re.sub(r'(?i)^powiat\s+', '', pow_name).strip()
            location_label = re.sub(r'[^\w]', '_', pow_name).strip('_')

            # Gmina (opcjonalna)
            if raw_gmina:
                gmina_raw_id = extract_id(raw_gmina)       # np. '026401_1'
                if gmina_raw_id:
                    gmina_teryt6 = gmina_raw_id[:6]        # pierwsze 6 cyfr = kod gminy
                    gmina_name = raw_gmina.split('(')[0].strip()
                    gmina_name = re.sub(r'\(.*\)', '', gmina_name).strip()
                    location_label = re.sub(r'[^\w]', '_', gmina_name).strip('_')
                    arcpy.AddMessage(f"Filtrowanie po gminie: {raw_gmina} (TERYT6: {gmina_teryt6})")

            arcpy.AddMessage(f"Wybrany powiat: {raw_pow} (kod TERYT: {teryt_code})")

        else:
            if not in_features:
                arcpy.AddError("Wskaż obszar na mapie lub wybierz warstwę wejściową.")
                return
            src_sr = arcpy.Describe(in_features).spatialReference
            with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as sc:
                for row in sc:
                    g = row[0]
                    if g:
                        if src_sr and src_sr.factoryCode != 2180:
                            g = g.projectAs(sr_2180)
                        input_geom = g if input_geom is None else input_geom.union(g)

            if not input_geom:
                arcpy.AddError("Wskazana warstwa obszaru nie zawiera prawidłowej geometrii.")
                return

            ext = input_geom.extent
            xmin, ymin, xmax, ymax = ext.XMin, ext.YMin, ext.XMax, ext.YMax
            if xmin == xmax:
                xmin -= 10.0; xmax += 10.0
            if ymin == ymax:
                ymin -= 10.0; ymax += 10.0
            bbox = (xmin, ymin, xmax, ymax)
            arcpy.AddMessage(f"Obszar pobierania: BBOX ({xmin:.1f}, {ymin:.1f}) - ({xmax:.1f}, {ymax:.1f})")

        if not date_from and not date_to:
            arcpy.AddWarning(
                "UWAGA: Nie określono zakresu dat transakcji (pobieranie całego rejestru). "
                "W zależności od wielkości powiatu pobieranie może potrwać od kilku do kilkunastu minut."
            )
        elif date_from or date_to:
            arcpy.AddMessage(f"Filtrowanie dat transakcji: od {date_from or 'początku rejestru'} do {date_to or 'teraz'}")
            if date_from and date_to:
                try:
                    d1 = datetime.datetime.strptime(date_from, "%Y-%m-%d")
                    d2 = datetime.datetime.strptime(date_to, "%Y-%m-%d")
                    delta = (d2 - d1).days
                    if delta > 365 * 3:
                        years = round(delta / 365.25, 1)
                        arcpy.AddWarning(
                            f"UWAGA: Wybrano szeroki zakres czasowy ({years} lat: {date_from} do {date_to}). "
                            f"Pobieranie dużej liczby transakcji przez WFS GUGiK może potrwać kilka minut."
                        )
                except Exception:
                    pass

        filter_xml = self._build_fes_filter(
            teryt_code=teryt_code,
            bbox=bbox,
            date_from=date_from,
            date_to=date_to
        )

        total_layers = len(selected_layers)

        # 1. Płynny pasek postępu: oszacuj liczbę obiektów i paczek dla każdej warstwy
        arcpy.SetProgressor("default", "Szacowanie liczby obiektów w usłudze WFS...")
        arcpy.AddMessage("Sprawdzanie dostępności danych w usłudze WFS...")

        layer_stats = {}
        total_steps = 0

        for layer_name in selected_layers:
            cfg = self.LAYER_CONFIG[layer_name]
            type_name = cfg["typename"]
            hits = self._get_number_matched(type_name, filter_xml)
            if hits is not None and hits > 0:
                batches = math.ceil(hits / self.BATCH_SIZE)
            else:
                batches = 1
            # Każda paczka = 1 krok paska + 1 krok na zapis do Geobazy
            steps = batches + 1
            layer_stats[layer_name] = {
                "hits": hits,
                "batches": batches,
                "steps": steps
            }
            total_steps += steps

        total_est_hits = sum(info["hits"] for info in layer_stats.values() if info.get("hits") is not None)
        if total_est_hits > 2000:
            est_mins = max(1, math.ceil(total_est_hits / 500 * 3.0 / 60))
            arcpy.AddWarning(
                f"UWAGA: Znaleziono łącznie około {total_est_hits} obiektów do pobrania. "
                f"Z uwagi na ograniczenia serwera WFS GUGiK pobieranie może zająć około {est_mins}-{est_mins * 2} min."
            )

        total_steps = max(total_steps, total_layers * 2)
        arcpy.SetProgressor("step", "Pobieranie danych z Rejestru Cen Nieruchomości...", 0, total_steps, 1)


        created_fcs = []
        errors = []
        current_step = 0

        for layer_idx, layer_name in enumerate(selected_layers, start=1):
            if HttpClient.is_cancelled():
                arcpy.AddWarning("Przerwano przez użytkownika.")
                break

            cfg = self.LAYER_CONFIG[layer_name]
            type_name = cfg["typename"]
            geom_type = cfg["geom_type"]
            raw_fc_name = f"RCN_{cfg['suffix']}_{location_label}" if location_label else f"RCN_{cfg['suffix']}"
            fc_safe_name = arcpy.ValidateTableName(raw_fc_name, out_ws)
            out_fc = os.path.join(out_ws, fc_safe_name)

            l_info = layer_stats.get(layer_name, {})
            hits = l_info.get("hits")
            est_batches = l_info.get("batches", 1)

            if hits is not None:
                arcpy.AddMessage(f"\n--- Przetwarzanie warstwy: {layer_name} ({type_name}) - znaleziono: {hits} obiektów ---")
            else:
                arcpy.AddMessage(f"\n--- Przetwarzanie warstwy: {layer_name} ({type_name}) ---")

            processed_ids = set()
            if arcpy.Exists(out_fc):
                if overwrite:
                    arcpy.management.Delete(out_fc)
                    arcpy.CreateFeatureclass_management(out_ws, fc_safe_name, geom_type, spatial_reference=sr_2180)
                    arcpy.management.AddField(out_fc, "ID_OBIEKTU", "TEXT", field_length=100)
                    arcpy.AddMessage(f"Nadpisano istniejącą warstwę: {fc_safe_name}")
                else:
                    try:
                        with arcpy.da.SearchCursor(out_fc, ["ID_OBIEKTU"]) as sc:
                            for r in sc:
                                if r[0]:
                                    processed_ids.add(str(r[0]).strip())
                        arcpy.AddMessage(f"Warstwa '{fc_safe_name}' istnieje. Pomijanie {len(processed_ids)} już pobranych obiektów.")
                    except Exception as ex:
                        arcpy.AddWarning(f"Nie udało się odczytać istniejących obiektów z {fc_safe_name}: {ex}")
            else:
                arcpy.CreateFeatureclass_management(out_ws, fc_safe_name, geom_type, spatial_reference=sr_2180)
                arcpy.management.AddField(out_fc, "ID_OBIEKTU", "TEXT", field_length=100)
                arcpy.AddMessage(f"Utworzono nową warstwę: {fc_safe_name}")

            features_to_save = []
            all_attr_keys = set()
            start_index = 0
            page_num = 1

            total_fetched = 0
            total_added = 0
            total_skip_dup = 0
            total_skip_outside = 0
            total_skip_geom = 0
            total_skip_gmina = 0


            while True:
                if HttpClient.is_cancelled():
                    arcpy.AddWarning("Przerwano przez użytkownika.")
                    break

                url_params = {
                    "SERVICE": "WFS",
                    "REQUEST": "GetFeature",
                    "VERSION": "2.0.0",
                    "TYPENAMES": type_name,
                    "COUNT": str(self.BATCH_SIZE),
                    "STARTINDEX": str(start_index)
                }
                if filter_xml:
                    url_params["FILTER"] = filter_xml

                req_url = f"{self.WFS_URL}?{urllib.parse.urlencode(url_params)}"

                try:
                    data = HttpClient.fetch_bytes(req_url, timeout=90)
                except Exception as net_err:
                    errors.append(f"Warstwa {layer_name}, strona {page_num}: {net_err}")
                    arcpy.AddWarning(f"Błąd sieci podczas pobierania paczki {page_num}: {net_err}")
                    break

                try:
                    root = ET.fromstring(data)
                except Exception as parse_err:
                    errors.append(f"Warstwa {layer_name}, strona {page_num}: błąd XML: {parse_err}")
                    break

                # Pobierz elementy obiektów z wfs:member
                member_elems = []
                for el in root.iter():
                    tag = el.tag.split("}")[-1]
                    if tag in ("member", "featureMember"):
                        member_elems.extend(list(el))

                if not member_elems:
                    break

                total_fetched += len(member_elems)
                batch_new = 0
                batch_dup = 0
                batch_outside = 0
                batch_geom = 0
                batch_gmina = 0


                for feat in member_elems:
                    wkt = self._parse_geometry(feat)
                    if not wkt:
                        batch_geom += 1
                        continue

                    # Ekstrakcja atrybutów
                    attrs = {}
                    gml_id = feat.attrib.get("{http://www.opengis.net/gml/3.2}id") or feat.attrib.get("id")

                    for child in feat.iter():
                        tag = child.tag.split("}")[-1]
                        if tag.lower() in self.IGNORED_TAGS:
                            continue
                        if not list(child) and child.text and child.text.strip():
                            t_upper = tag.upper()
                            val = child.text.strip()
                            attrs[t_upper] = val

                    # Unikalny identyfikator: gml:id lub gid (unikalny klucz rekordu w WFS)
                    gid_val = attrs.get("GID")
                    tran_id = attrs.get("TRAN_LOKALNY_ID_IIP")
                    specific_id = attrs.get(cfg.get("id_field", ""))

                    if gml_id:
                        unique_id = gml_id
                    elif gid_val:
                        unique_id = f"{type_name}.{gid_val}"
                    elif tran_id and specific_id:
                        unique_id = f"{tran_id}_{specific_id}"
                    elif specific_id:
                        unique_id = f"{specific_id}_{len(features_to_save)}"
                    else:
                        unique_id = f"{type_name}_{start_index}_{len(features_to_save)}"

                    if unique_id in processed_ids:
                        batch_dup += 1
                        continue

                    # Filtrowanie po gminie – sprawdź prefiks id pola jednostki
                    if gmina_teryt6:
                        obj_id = attrs.get(cfg.get("id_field", ""), "")
                        if not obj_id.startswith(gmina_teryt6):
                            batch_gmina += 1
                            continue

                    try:
                        geom = arcpy.FromWKT(wkt, sr_2180)
                    except Exception as ge:
                        batch_geom += 1
                        continue

                    if not geom:
                        batch_geom += 1
                        continue

                    # Dokładne docięcie do narysowanego poligonu/obszaru
                    if input_geom is not None and geom.disjoint(input_geom):
                        batch_outside += 1
                        continue

                    processed_ids.add(unique_id)
                    all_attr_keys.update(attrs.keys())
                    features_to_save.append((geom, unique_id, attrs))
                    batch_new += 1

                total_added += batch_new
                total_skip_dup += batch_dup
                total_skip_outside += batch_outside
                total_skip_geom += batch_geom
                total_skip_gmina += batch_gmina

                skip_details = []
                if batch_dup > 0:
                    skip_details.append(f"{batch_dup} duplikatów serwera WFS [ten sam GID / już w tabeli]")
                if batch_gmina > 0:
                    skip_details.append(f"{batch_gmina} spoza wybranej gminy")
                if batch_outside > 0:
                    skip_details.append(f"{batch_outside} poza obrysem obszaru")
                if batch_geom > 0:
                    skip_details.append(f"{batch_geom} błędów geometrii")


                skip_str = f" [pominięto: {', '.join(skip_details)}]" if skip_details else ""
                arcpy.AddMessage(f"  Paczka {page_num} (rekordy {start_index + 1}-{start_index + len(member_elems)}): pobrano {len(member_elems)}, dodano {batch_new}{skip_str}")

                # Aktualizacja paska postępu z każdą paczką
                current_step += 1
                arcpy.SetProgressorPosition(min(current_step, total_steps - 1))

                if hits and hits > 0:
                    pct = int(min(total_fetched, hits) / hits * 100)
                    prog_label = f"[{layer_idx}/{total_layers}] Pobieranie {layer_name}: {min(total_fetched, hits)}/{hits} ({pct}%) [Paczka {page_num}/{est_batches}]..."
                else:
                    prog_label = f"[{layer_idx}/{total_layers}] Pobieranie {layer_name}: pobrano {total_fetched} obiektów [Paczka {page_num}]..."
                arcpy.SetProgressorLabel(prog_label)

                if len(member_elems) < self.BATCH_SIZE:
                    break

                start_index += self.BATCH_SIZE
                page_num += 1

            if total_skip_dup > 0 or total_skip_outside > 0 or total_skip_geom > 0 or total_skip_gmina > 0:
                arcpy.AddMessage(
                    f"  -> Łącznie w warstwie {layer_name}: pobrano {total_fetched}, dodano {total_added} "
                    f"(duplikaty WFS [identyczny GID]: {total_skip_dup}, spoza gminy: {total_skip_gmina}, "
                    f"poza obrysem: {total_skip_outside}, błędy geometrii: {total_skip_geom})"
                )
            if total_skip_dup > 0:
                arcpy.AddMessage(
                    "     [INFO] Pominięte 'duplikaty WFS' to identyczny fizyczny rekord transakcji w usłudze (ten sam identyfikator GID powtórzony przez serwer WFS "
                    "na przełomie stron lub obiekt już wcześniej zapisany w geobazie). Wielokrotne transakcje tej samej działki (zmiany właściciela) "
                    "posiadają odrębne identyfikatory GID oraz daty aktów i są w całości zapisywane do geobazy."
                )



            # Zapis do geobazy
            if features_to_save:
                arcpy.SetProgressorLabel(f"[{layer_idx}/{total_layers}] Zapisywanie {len(features_to_save)} obiektów {layer_name} do geobazy...")
                arcpy.AddMessage(f"Zapisywanie {len(features_to_save)} obiektów do geobazy...")
                field_map = self._ensure_fields(out_fc, out_ws, sorted(list(all_attr_keys)))
                attr_cols = sorted(list(field_map.keys()))
                db_cols = [field_map[col] for col in attr_cols]

                insert_fields = ["SHAPE@", "ID_OBIEKTU"] + db_cols
                field_lengths = {f.name: f.length for f in arcpy.ListFields(out_fc) if f.type == "String"}

                with arcpy.da.InsertCursor(out_fc, insert_fields) as cursor:
                    for geom, obj_id, attrs in features_to_save:
                        try:
                            row_vals = [geom, obj_id]
                            for k, col in zip(attr_cols, db_cols):
                                val = attrs.get(k)
                                if val is not None and isinstance(val, str):
                                    max_l = field_lengths.get(col)
                                    if max_l and len(val) > max_l:
                                        val = val[:max_l]
                                row_vals.append(val)
                            cursor.insertRow(row_vals)
                        except Exception as ins_err:
                            errors.append(f"Zapis {obj_id} do {fc_safe_name}: {ins_err}")

                arcpy.AddMessage(f"Pomyślnie zapisano warstwę: {fc_safe_name} ({len(features_to_save)} nowych transakcji).")
                created_fcs.append(out_fc)
            else:
                arcpy.AddMessage(f"Brak nowych obiektów do zapisania dla warstwy: {layer_name}.")
                if arcpy.Exists(out_fc):
                    created_fcs.append(out_fc)

            current_step += 1
            arcpy.SetProgressorPosition(min(current_step, total_steps))

        # Dodanie warstw do aktywnej mapy ArcGIS Pro
        if created_fcs:
            arcpy.SetProgressorLabel("Dodawanie warstw do widoku aktywnej mapy...")
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                active_map = aprx.activeMap
                if active_map:
                    for fc in created_fcs:
                        fc_name = os.path.basename(fc)
                        existing_layers = active_map.listLayers(fc_name)
                        if not existing_layers:
                            active_map.addDataFromPath(fc)
                            arcpy.AddMessage(f"Dodano warstwę '{fc_name}' do aktywnej mapy.")
            except Exception as map_err:
                arcpy.AddWarning(f"Nie udało się automatycznie dodać warstw do mapy: {map_err}")

        if len(p) > 11 and created_fcs:
            p[11].value = created_fcs[0]

        arcpy.SetProgressorPosition(total_steps)
        arcpy.ResetProgressor()

        if errors:
            arcpy.AddWarning(f"Wystąpiły ostrzeżenia ({len(errors)}):")
            for err in errors[:10]:
                arcpy.AddWarning(f"  • {err}")
            if len(errors) > 10:
                arcpy.AddWarning(f"  • ... i {len(errors) - 10} dalszych.")

        arcpy.AddMessage("\nZakończono pobieranie danych z Rejestru Cen Nieruchomości!")

