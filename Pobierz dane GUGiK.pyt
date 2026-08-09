# -*- coding: utf-8 -*-
import arcpy
import urllib.request
import urllib.parse
import os
import re
import zipfile
import xml.etree.ElementTree as ET
import csv
import datetime
import time
import json

TERYT_DB = None

def load_teryt_db():
    global TERYT_DB
    if TERYT_DB is None:
        try:
            try:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            except NameError:
                import inspect
                base_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))

            db_path = os.path.join(base_dir, "slownik_teryt.json")
            if os.path.exists(db_path):
                with open(db_path, 'r', encoding='utf-8') as f:
                    TERYT_DB = json.load(f)
            else:
                TERYT_DB = False
        except Exception:
            TERYT_DB = False
    return TERYT_DB

# ==============================================================================
# DEDYKOWANE KLASY POMOCNICZE HTTP ORAZ WFS
# ==============================================================================
class HttpClient:
    """Klasa odpowiedzialna za bezpieczne połączenia HTTPS, timeouty oraz pobieranie."""

    DEFAULT_TIMEOUT = 60
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ArcGISPro-GUGiKTools/1.0"
    }

    @classmethod
    def fetch_bytes(cls, url, headers=None, timeout=None, retries=3, delay=2.0):
        req_headers = cls.DEFAULT_HEADERS.copy()
        if headers:
            req_headers.update(headers)
        to = timeout or cls.DEFAULT_TIMEOUT

        req = urllib.request.Request(url, headers=req_headers)
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=to) as resp:
                    return resp.read()
            except Exception as e:
                if attempt == retries - 1:
                    raise e
                time.sleep(delay)

    @classmethod
    def fetch_text(cls, url, headers=None, timeout=None, retries=3, delay=2.0, encoding='utf-8'):
        data = cls.fetch_bytes(url, headers=headers, timeout=timeout, retries=retries, delay=delay)
        return data.decode(encoding, errors='ignore').strip()

    @classmethod
    def download_stream(cls, url, out_path, headers=None, timeout=None, retries=3, delay=2.0, chunk_size=128*1024, messages=None):
        """Pobiera plik częściami (chunk-by-chunk), zapobiegając wyczerpaniu pamięci RAM."""
        req_headers = cls.DEFAULT_HEADERS.copy()
        if headers:
            req_headers.update(headers)
        to = timeout or cls.DEFAULT_TIMEOUT

        temp_path = out_path + ".tmp"
        req = urllib.request.Request(url, headers=req_headers)

        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=to) as resp:
                    total_size = resp.headers.get('content-length')
                    total_bytes = int(total_size) if total_size and total_size.isdigit() else None
                    downloaded = 0
                    last_reported = 0

                    with open(temp_path, "wb") as f_out:
                        while True:
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            f_out.write(chunk)
                            downloaded += len(chunk)

                            # Raportuj postęp co 5 MB
                            if messages and (downloaded - last_reported >= 5 * 1024 * 1024):
                                mb = downloaded / (1024 * 1024)
                                if total_bytes:
                                    pct = int((downloaded / total_bytes) * 100)
                                    messages.addMessage(f"Pobieranie w toku: {mb:.1f} MB ({pct}%)...")
                                else:
                                    messages.addMessage(f"Pobieranie w toku: {mb:.1f} MB...")
                                last_reported = downloaded

                if os.path.exists(out_path):
                    os.remove(out_path)
                os.rename(temp_path, out_path)
                return out_path

            except Exception as e:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                if attempt == retries - 1:
                    raise e
                time.sleep(delay)


class WFSClient:
    """Klasa obsługująca komunikację z usługami WFS (GetCapabilities, GetFeature)."""

    def __init__(self, http_client=HttpClient):
        self.http = http_client

    def get_all_typenames(self, base_url):
        url = f"{base_url}?SERVICE=WFS&REQUEST=GetCapabilities&VERSION=2.0.0"
        xml_text = self.http.fetch_text(url)

        names = sorted(set(re.findall(
            r"<(?:\w+:)?Name>([^<]*Skorowidz[^<]*)</(?:\w+:)?Name>",
            xml_text, re.IGNORECASE
        )))
        if not names:
            raise RuntimeError("Nie znaleziono żadnej warstwy skorowidza w GetCapabilities.")

        def year_of(name):
            years = re.findall(r"(?:19|20)\d{2}", name)
            return int(years[-1]) if years else None

        dated = [(year_of(n), n) for n in names]
        dated.sort(key=lambda t: (t[0] is None, -(t[0] or 0)))
        return dated

    def get_features(self, base_url, typename, bbox_2180):
        xmin, ymin, xmax, ymax = bbox_2180
        bbox_param = f"{ymin},{xmin},{ymax},{xmax},urn:ogc:def:crs:EPSG::2180"
        url = (
            f"{base_url}?SERVICE=WFS&REQUEST=GetFeature&VERSION=2.0.0"
            f"&TYPENAMES={urllib.parse.quote(typename)}"
            f"&SRSNAME=urn:ogc:def:crs:EPSG::2180&BBOX={bbox_param}"
        )
        data = self.http.fetch_bytes(url)
        root = ET.fromstring(data)

        feature_elems = []
        for el in root.iter():
            local = el.tag.split("}")[-1]
            if local in ("member", "featureMember"):
                feature_elems.extend(list(el))

        results = []
        for feat in feature_elems:
            attrs = {}
            for child in feat:
                local = child.tag.split("}")[-1].lower()
                if child.text and child.text.strip():
                    attrs[local] = child.text.strip()
            if attrs:
                results.append(attrs)
        return results

# ==============================================================================
# TOOLBOX
# ==============================================================================
class Toolbox(object):
    def __init__(self):
        self.label = "Pobierz dane GUGiK"
        self.alias = "GUGiK_tools"
        self.tools = [PobierzDzialkeULDK, PobierzOrtofotomape, PobierzChmuryPunktow, PobierzNMTNMPT, PobierzSkorowidzEGIB]

# ==============================================================================
# NARZĘDZIE 1: ULDK
# ==============================================================================
class PobierzDzialkeULDK(object):
    def __init__(self):
        self.label = "Pobierz obrys działek/obrębów/gmin/powiatów/ województw (ULDK)"
        self.description = "Pobiera poligony działek, obrębów, gmin, powiatów lub województw za pomocą Usługi Lokalizacji Działek Katastralnych."
        self.canRunInBackground = False

    def getParameterInfo(self):
        param_method = arcpy.Parameter(
            displayName="Metoda wyszukiwania",
            name="search_method",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_method.filter.list = [
            "Kliknięcie na mapie",
            "Identyfikator TERYT",
            "Kaskadowy wybór z listy (Woj -> Pow -> Gmi -> Obr)"
        ]
        param_method.value = "Kaskadowy wybór z listy (Woj -> Pow -> Gmi -> Obr)"

        param_typ = arcpy.Parameter(
            displayName="Typ pobieranego obiektu",
            name="typ_obiektu",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_typ.filter.list = ["Działka", "Obręb", "Gmina", "Powiat", "Województwo"]
        param_typ.value = "Działka"

        param_point = arcpy.Parameter(
            displayName="Wskaż obiekt na mapie (ołówek)",
            name="click_points",
            datatype="GPFeatureRecordSetLayer",
            parameterType="Optional",
            direction="Input"
        )
        param_point.filter.list = ["Point"]

        param_id = arcpy.Parameter(
            displayName="Identyfikator TERYT (np. 141201_1.0001.1867/2 lub 141201_1). Kolejne obiekty oddziel przecinkiem.",
            name="parcel_id",
            datatype="GPString",
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

        param_obr = arcpy.Parameter(
            displayName="Obręb",
            name="obreb",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_obr.filter.list = ["(Ładowanie słownika...)"]

        param_dz_nr = arcpy.Parameter(
            displayName="Arkusz + Numer działki np. AR_6.1/7",
            name="dzialka_nr",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )

        param_out_ws = arcpy.Parameter(
            displayName="Geobaza docelowa",
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
        param_out_name.value = "Pobrane_Obiekty_ULDK"

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
            param_method, param_typ, param_point, param_id,
            param_woj, param_pow, param_gmi, param_obr, param_dz_nr,
            param_out_ws, param_out_name, param_author, param_derived
        ]

    def updateParameters(self, p):
        if not p or not p[0].value: return

        method = p[0].valueAsText
        is_click = (method == "Kliknięcie na mapie")
        is_teryt = (method == "Identyfikator TERYT")
        is_list = (method == "Kaskadowy wybór z listy (Woj -> Pow -> Gmi -> Obr)")

        p[2].enabled = is_click
        p[3].enabled = is_teryt
        p[4].enabled = is_list
        p[5].enabled = is_list
        p[6].enabled = is_list
        p[7].enabled = is_list

        p[8].enabled = is_list and p[1].valueAsText == "Działka"

        if len(p) > 11:
            p[11].enabled = False

        if is_list:
            db = load_teryt_db()
            if not db:
                p[4].setErrorMessage("Błąd: Plik 'slownik_teryt.json' nie został odnaleziony!")
                return
            else:
                p[4].clearMessage()

            # Województwa
            woj_list = sorted([f"{v} ({k})" for k, v in db.get("wojewodztwa", {}).items()])
            if p[4].filter.list != woj_list:
                p[4].filter.list = woj_list

            woj_val = p[4].valueAsText
            woj_id = woj_val.split('(')[-1].replace(')','') if woj_val and '(' in woj_val else None

            # Powiaty
            pow_list = []
            if woj_id and woj_id in db.get("powiaty", {}):
                pow_list = sorted([f"{v} ({k})" for k, v in db["powiaty"][woj_id].items()])

            if p[5].filter.list != pow_list:
                p[5].filter.list = pow_list
                if p[5].valueAsText and p[5].valueAsText not in pow_list:
                    p[5].value = None

            pow_val = p[5].valueAsText
            pow_id = pow_val.split('(')[-1].replace(')','') if pow_val and '(' in pow_val else None

            # Gminy
            gmi_list = []
            if pow_id and pow_id in db.get("gminy", {}):
                gmi_list = sorted([f"{v} ({k})" for k, v in db["gminy"][pow_id].items()])

            if p[6].filter.list != gmi_list:
                p[6].filter.list = gmi_list
                if p[6].valueAsText and p[6].valueAsText not in gmi_list:
                    p[6].value = None

            gmi_val = p[6].valueAsText
            gmi_id = gmi_val.split('(')[-1].replace(')','') if gmi_val and '(' in gmi_val else None

            # Obręby
            obr_list = []
            if gmi_id and gmi_id in db.get("obreby", {}):
                obr_list = sorted([f"{v} ({k})" for k, v in db["obreby"][gmi_id].items()])

            if p[7].filter.list != obr_list:
                p[7].filter.list = obr_list
                if p[7].valueAsText and p[7].valueAsText not in obr_list:
                    p[7].value = None

    def execute(self, p, messages):
        method = p[0].valueAsText
        typ_obiektu = p[1].valueAsText
        point_fs = p[2].value
        parcel_id = p[3].valueAsText

        obr_val = p[7].valueAsText
        dz_nr_val = p[8].valueAsText

        out_ws = p[9].valueAsText
        raw_out_name = p[10].valueAsText

        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', raw_out_name)
        if safe_name and safe_name[0].isdigit():
            safe_name = "L_" + safe_name

        out_name = arcpy.ValidateTableName(safe_name, out_ws)
        if out_name != raw_out_name:
            arcpy.AddMessage(f"Skorygowano nazwę warstwy do: '{out_name}'")

        out_fc = os.path.join(out_ws, out_name)
        url_base = "https://uldk.gugik.gov.pl/"
        sr_2180 = arcpy.SpatialReference(2180)

        api_map = {
            "Działka": ("Parcel", "id,wojewodztwo,powiat,gmina,obreb,numer,geom_wkt"),
            "Obręb": ("Region", "id,wojewodztwo,powiat,gmina,nazwa,geom_wkt"),
            "Gmina": ("Commune", "id,wojewodztwo,powiat,nazwa,geom_wkt"),
            "Powiat": ("County", "id,wojewodztwo,nazwa,geom_wkt"),
            "Województwo": ("Voivodeship", "id,nazwa,geom_wkt")
        }
        api_endpoint, result_param = api_map[typ_obiektu]

        processed_ids = set()
        full_extent = None

        if not arcpy.Exists(out_fc):
            arcpy.CreateFeatureclass_management(out_ws, out_name, "POLYGON", spatial_reference=sr_2180)
            arcpy.management.AddFields(out_fc, [
                ["ID_TERYT", "TEXT", "ID_TERYT", 50], ["TYP_OBIEKTU", "TEXT", "TYP_OBIEKTU", 20],
                ["WOJEWODZTWO", "TEXT", "WOJ", 50], ["POWIAT", "TEXT", "POW", 50],
                ["GMINA", "TEXT", "GMI", 50], ["OBREB", "TEXT", "OBR", 50],
                ["NUMER_DZIALKI", "TEXT", "NUMER", 20], ["NAZWA", "TEXT", "NAZWA", 100]
            ])
        else:
            try:
                with arcpy.da.SearchCursor(out_fc, ["ID_TERYT"]) as search_cursor:
                    for row in search_cursor:
                        if row[0]: processed_ids.add(row[0])
                arcpy.AddMessage(f"Wczytano {len(processed_ids)} wcześniej pobranych obiektów z warstwy.")
            except Exception as e:
                arcpy.AddError(f"Warstwa istnieje, ale ma nieprawidłową strukturę. Wybierz inną nazwę. Błąd: {e}")
                return

        initial_count = len(processed_ids)
        insert_fields = ["SHAPE@", "ID_TERYT", "TYP_OBIEKTU", "WOJEWODZTWO", "POWIAT", "GMINA", "OBREB", "NUMER_DZIALKI", "NAZWA"]

        with arcpy.da.InsertCursor(out_fc, insert_fields) as cursor:
            if method == "Kliknięcie na mapie":
                if not point_fs: return arcpy.AddError("Proszę wskazać punkt.")
                srid = arcpy.Describe(point_fs).spatialReference.factoryCode or 2180
                with arcpy.da.SearchCursor(point_fs, ["SHAPE@XY"]) as s_cur:
                    for row in s_cur:
                        if not row[0] or None in row[0]: continue
                        xy = f"{row[0][0]},{row[0][1]}" if str(srid) == "2180" else f"{row[0][0]},{row[0][1]},{srid}"
                        url = f"{url_base}?request=Get{api_endpoint}ByXY&xy={xy}&result={result_param}"
                        full_extent = self.fetch_and_insert(url, cursor, processed_ids, full_extent, sr_2180, typ_obiektu)

            elif method == "Identyfikator TERYT":
                if not parcel_id: return arcpy.AddError("Proszę podać identyfikator.")
                for p_id in [i.strip() for i in parcel_id.split(',') if i.strip()]:
                    url = f"{url_base}?request=Get{api_endpoint}ById&id={p_id}&result={result_param}"
                    full_extent = self.fetch_and_insert(url, cursor, processed_ids, full_extent, sr_2180, typ_obiektu)

            elif method == "Kaskadowy wybór z listy (Woj -> Pow -> Gmi -> Obr)":
                if typ_obiektu != "Działka":
                    full_teryt = None
                    if typ_obiektu == "Województwo" and p[4].valueAsText: full_teryt = p[4].valueAsText.split('(')[-1].replace(')','')
                    elif typ_obiektu == "Powiat" and p[5].valueAsText: full_teryt = p[5].valueAsText.split('(')[-1].replace(')','')
                    elif typ_obiektu == "Gmina" and p[6].valueAsText: full_teryt = p[6].valueAsText.split('(')[-1].replace(')','')
                    elif typ_obiektu == "Obręb" and obr_val: full_teryt = obr_val.split('(')[-1].replace(')','')

                    if full_teryt:
                        url = f"{url_base}?request=Get{api_endpoint}ById&id={full_teryt}&result={result_param}"
                        full_extent = self.fetch_and_insert(url, cursor, processed_ids, full_extent, sr_2180, typ_obiektu)
                else:
                    if not obr_val or not dz_nr_val or obr_val.startswith("("):
                        return arcpy.AddError("Wybierz obręb z list rozwijanych i podaj numer działki.")
                    obr_id = obr_val.split('(')[-1].replace(')','')
                    for nr in dz_nr_val.split(','):
                        if nr.strip():
                            url = f"{url_base}?request=GetParcelById&id={obr_id}.{nr.strip()}&result={result_param}"
                            full_extent = self.fetch_and_insert(url, cursor, processed_ids, full_extent, sr_2180, typ_obiektu)

        arcpy.AddMessage(f"Dodano {len(processed_ids) - initial_count} nowych obiektów.")

        if len(p) > 12:
            p[12].value = out_fc

        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_view = aprx.activeView
            active_map = aprx.activeMap

            if active_map:
                layers = active_map.listLayers(out_name)
                lyr = layers[0] if layers else active_map.addDataFromPath(out_fc)

                if full_extent and active_view:
                    bx = (full_extent.XMax - full_extent.XMin) * 0.1
                    by = (full_extent.YMax - full_extent.YMin) * 0.1
                    if bx == 0: bx = 10
                    if by == 0: by = 10
                    full_extent.XMin -= bx; full_extent.YMin -= by
                    full_extent.XMax += bx; full_extent.YMax += by
                    active_view.camera.setExtent(full_extent)
        except Exception:
            pass

    def fetch_and_insert(self, req_url, cursor, processed_ids, extent, sr_2180, typ_obiektu):
        try:
            resp_text = HttpClient.fetch_text(req_url, timeout=30)
        except Exception as e:
            arcpy.AddWarning(f"Błąd sieci: {e}")
            return extent

        lines = resp_text.split('\n')
        if not lines or lines[0].strip() != "0" or len(lines) < 2:
            return extent

        for data_line in lines[1:]:
            parts = data_line.strip().split('|')
            if len(parts) < 3: continue

            d_id = parts[0]
            if d_id in processed_ids:
                arcpy.AddMessage(f"Pominięto duplikat: {d_id}")
                continue

            try:
                geom = arcpy.FromWKT(parts[-1].split(';')[-1], sr_2180)
                woj = pow = gmi = obr = num = nazwa = None

                if typ_obiektu == "Działka" and len(parts) >= 7:
                    woj, pow, gmi, obr, num = parts[1:6]
                elif typ_obiektu == "Obręb" and len(parts) >= 6:
                    woj, pow, gmi, nazwa = parts[1:5]
                elif typ_obiektu == "Gmina" and len(parts) >= 5:
                    woj, pow, nazwa = parts[1:4]
                elif typ_obiektu == "Powiat" and len(parts) >= 4:
                    woj, nazwa = parts[1:3]
                elif typ_obiektu == "Województwo" and len(parts) >= 3:
                    nazwa = parts[1]

                cursor.insertRow((geom, d_id, typ_obiektu, woj, pow, gmi, obr, num, nazwa))
                processed_ids.add(d_id)

                if extent is None:
                    extent = geom.extent
                else:
                    extent.XMin = min(extent.XMin, geom.extent.XMin)
                    extent.YMin = min(extent.YMin, geom.extent.YMin)
                    extent.XMax = max(extent.XMax, geom.extent.XMax)
                    extent.YMax = max(extent.YMax, geom.extent.YMax)
            except Exception as e:
                arcpy.AddWarning(f"Błąd geometrii dla {d_id}: {e}")
        return extent

# ==============================================================================
# NARZĘDZIE 2: ORTOFOTOMAPA
# ==============================================================================
class PobierzOrtofotomape(object):
    WFS_ENDPOINTS = {
        "Standardowa": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WFS/Skorowidze",
        "Prawdziwa (true-ortho)": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WFS/SkorowidzPrawdziwejOrtofotomapy",
    }

    SCENE_CANDIDATE_KEYS = [
        "kolor", "typ_barw", "barwa", "rodzaj_zdjecia",
        "typ_kanalow", "spektrum", "typ", "rodzaj", "typ_ortofotomapy"
    ]
    SCENE_VALUE_KEYWORDS = {
        "RGB": ["rgb"],
        "CIR (podczerwień)": ["cir", "ir", "podczerwie"],
        "Czarno-biała": ["czarno", "panchromat", "szaro", "bw", "b/w", "cz-b", "cz/b", "cz_b"],
    }

    def __init__(self):
        self.label = "Pobierz ortofotomapę"
        self.description = "Narysuj obszar lub wybierz warstwę, aby pobrać rastry z WFS dla wskazanych lat."
        self.canRunInBackground = False
        self.wfs_client = WFSClient()

    def getParameterInfo(self):
        param_geom = arcpy.Parameter(
            displayName="Wskaż obszar na mapie (ołówek) lub wybierz warstwę",
            name="input_features",
            datatype="GPFeatureRecordSetLayer",
            parameterType="Required",
            direction="Input"
        )

        param_rodzaj = arcpy.Parameter(
            displayName="Rodzaj skorowidza",
            name="rodzaj_skorowidza",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_rodzaj.filter.list = list(self.WFS_ENDPOINTS.keys())
        param_rodzaj.value = "Standardowa"

        param_tylko_aktualna = arcpy.Parameter(
            displayName="Pobierz najbardziej aktualne dane dla wybranego skorowidza",
            name="tylko_aktualna",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input"
        )
        param_tylko_aktualna.value = True

        param_lata = arcpy.Parameter(
            displayName="Wybierz interesujące Cię lata (jeśli odznaczono opcję wyżej)",
            name="wybrane_lata",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True
        )
        obecny_rok = datetime.datetime.now().year
        param_lata.filter.list = [str(rok) for rok in range(obecny_rok, 1995, -1)]

        param_scena = arcpy.Parameter(
            displayName="Typ barwny sceny",
            name="typ_barwny",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_scena.filter.list = ["Dowolna"] + list(self.SCENE_VALUE_KEYWORDS.keys())
        param_scena.value = "RGB"

        param_piksel = arcpy.Parameter(
            displayName="Wielkość piksela (m) [opcjonalnie]",
            name="wielkosc_piksela",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input"
        )
        param_piksel.filter.type = "Range"
        param_piksel.filter.list = [0.01, 10.0]

        param_out_folder = arcpy.Parameter(
            displayName="Folder docelowy na pobrane pliki",
            name="out_folder",
            datatype="DEFolder",
            parameterType="Required",
            direction="Input"
        )

        param_add_to_map = arcpy.Parameter(
            displayName="Dodaj wynik do aktywnej mapy",
            name="add_to_map",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_add_to_map.value = True

        param_mosaic = arcpy.Parameter(
            displayName="Utwórz mozaikę z pobranych rastrów (Mosaic Dataset)",
            name="create_mosaic",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_mosaic.value = False

        param_mosaic_name = arcpy.Parameter(
            displayName="Nazwa dla tworzonej mozaiki",
            name="mosaic_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_mosaic_name.value = "Mozaika_ortofotmapa_GUGiK"

        param_out_gdb = arcpy.Parameter(
            displayName="Geobaza docelowa dla mozaiki",
            name="out_gdb",
            datatype="DEWorkspace",
            parameterType="Optional",
            direction="Input"
        )
        param_out_gdb.filter.list = ["Local Database", "Remote Database"]

        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            if aprx.defaultGeodatabase:
                param_out_gdb.value = aprx.defaultGeodatabase
        except Exception:
            if arcpy.env.workspace:
                param_out_gdb.value = arcpy.env.workspace

        param_stats = arcpy.Parameter(
            displayName="Oblicz statystyki dla mozaiki",
            name="calc_stats",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_stats.value = False

        param_overwrite = arcpy.Parameter(
            displayName="Pobierz ponownie, jeśli plik już istnieje",
            name="overwrite",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_overwrite.value = False

        param_pelny = arcpy.Parameter(
            displayName="Pobierz tylko w pełni wypełnione arkusze",
            name="pelny_arkusz",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_pelny.value = False

        param_author = arcpy.Parameter(
            displayName="Informacje o narzędziu",
            name="author_info",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_author.value = "Autor: Mateusz Lubański"

        param_derived = arcpy.Parameter(
            displayName="Wynikowe rastry/mozaika (Ukryta)",
            name="out_rasters",
            datatype="DERasterDataset",
            parameterType="Derived",
            direction="Output",
            multiValue=True
        )

        return [
            param_geom, param_rodzaj, param_tylko_aktualna, param_lata,
            param_scena, param_piksel, param_out_folder, param_add_to_map, param_mosaic, param_mosaic_name, param_out_gdb,
            param_stats, param_overwrite, param_pelny, param_author, param_derived
        ]

    def updateParameters(self, p):
        tylko_aktualna = p[2].value
        p[3].enabled = not tylko_aktualna

        tworz_mozaike = p[8].value
        p[9].enabled = tworz_mozaike
        p[10].enabled = tworz_mozaike
        p[11].enabled = tworz_mozaike

        if len(p) > 14:
            p[14].enabled = False

    def _parse_feature_scene(self, attrs):
        for key in self.SCENE_CANDIDATE_KEYS:
            val = attrs.get(key)
            if val:
                val_lower = val.lower()
                if "rgb" in val_lower: return "RGB"
                if any(kw in val_lower for kw in ["cir", "ir", "podczerwie"]): return "CIR"
                if any(kw in val_lower for kw in ["czarno", "panchromat", "szaro", "bw"]): return "BM"
        return "UNKNOWN"

    def _extract_year(self, attrs):
        for k, v in attrs.items():
            if k in ("rok", "rok_nalotu", "rok_zdjecia", "data_zdjecia", "rok_wydania") or "rok" in k:
                match = re.search(r"(19|20)\d{2}", str(v))
                if match: return match.group(0)
        return attrs.get("rok_skorowidza", "")

    def _filter_by_scene(self, features, scena):
        if not scena or scena == "Dowolna":
            return features
        keywords = self.SCENE_VALUE_KEYWORDS.get(scena, [])
        if not keywords:
            return features

        matched = []
        for attrs in features:
            for key in self.SCENE_CANDIDATE_KEYS:
                val = attrs.get(key)
                if val and any(kw in val.lower() for kw in keywords):
                    matched.append(attrs)
                    break
        return matched

    def _find_tiles(self, base_url, bbox_2180, tylko_aktualna, wybrane_lata, scena, piksel, pelny_arkusz, messages):
        editions = self.wfs_client.get_all_typenames(base_url)

        if not tylko_aktualna and wybrane_lata:
            editions = [(y, tn) for y, tn in editions if str(y) in wybrane_lata]

        all_found_features = []

        for yr, typename in editions:
            try:
                features = self.wfs_client.get_features(base_url, typename, bbox_2180)
            except Exception as e:
                messages.addWarningMessage(f"Ostrzeżenie: Błąd pobierania danych dla warstwy {typename}: {e}")
                continue

            if features:
                for f in features:
                    if yr: f['rok_skorowidza'] = str(yr)

                if pelny_arkusz:
                    features = [f for f in features if str(f.get('czy_ark_wypelniony', '')).strip().lower() in ['1', 'true', 'tak', 't']]

                features = self._filter_by_scene(features, scena)

                if piksel is not None:
                    filtered_by_pix = []
                    for f in features:
                        pix_val = f.get('piksel', '')
                        if pix_val:
                            try:
                                match = re.search(r"(\d+(?:[\.,]\d+)?)", str(pix_val))
                                if match:
                                    parsed_pix = float(match.group(1).replace(',', '.'))
                                    if abs(parsed_pix - piksel) < 1e-4:
                                        filtered_by_pix.append(f)
                            except ValueError:
                                pass
                    features = filtered_by_pix

                if features:
                    if tylko_aktualna:
                        return features
                    all_found_features.extend(features)

        if not all_found_features:
            raise RuntimeError("Nie znaleziono kafli spełniających podane kryteria we wszystkich przeszukiwanych edycjach skorowidza.")

        return all_found_features

    def _download(self, url, out_folder, prefix, messages, overwrite):
        os.makedirs(out_folder, exist_ok=True)
        parsed_name = os.path.basename(urllib.parse.urlparse(url).path)
        if not parsed_name:
            parsed_name = "ortofotomapa_pobrana"

        fname = f"{prefix}_{parsed_name}" if prefix else parsed_name
        out_path = os.path.join(out_folder, fname)

        if os.path.exists(out_path) and not overwrite:
            messages.addMessage(f"Plik już istnieje, pomijam pobieranie: {fname}")
            return out_path

        messages.addMessage(f"Rozpoczynanie pobierania: {fname}...")
        HttpClient.download_stream(url, out_path, messages=messages)
        messages.addMessage(f"Pobrano pomyślnie: {fname}")
        return out_path

    def _extract_rasters(self, path, out_folder, prefix):
        raster_ext = (".tif", ".tiff", ".jp2", ".ecw")
        out_folder_abs = os.path.abspath(out_folder)

        if path.lower().endswith(".zip") and zipfile.is_zipfile(path):
            extracted = []
            with zipfile.ZipFile(path) as z:
                raster_members = [n for n in z.namelist() if n.lower().endswith(raster_ext)]
                members_to_extract = raster_members or z.namelist()
                for name in members_to_extract:
                    base_inside = os.path.basename(name)
                    out_name_prefixed = f"{prefix}_{base_inside}" if prefix else base_inside
                    out_full_path = os.path.abspath(os.path.join(out_folder, out_name_prefixed))

                    if not os.path.commonpath([out_folder_abs, out_full_path]).startswith(out_folder_abs):
                        continue

                    with open(out_full_path, "wb") as f_out:
                        f_out.write(z.read(name))
                    extracted.append(out_full_path)
            return extracted

        if path.lower().endswith(raster_ext):
            return [path]
        return []

    def execute(self, p, messages):
        in_features = p[0].value
        rodzaj = p[1].valueAsText
        tylko_aktualna = p[2].value

        wybrane_lata = [rok.strip("'\"") for rok in (p[3].valueAsText or "").split(';')] if p[3].valueAsText else []
        scena = p[4].valueAsText
        piksel = float(p[5].value) if p[5].value is not None else None

        out_folder = p[6].valueAsText
        add_to_map = p[7].value if p[7].value is not None else True
        create_mosaic = p[8].value if p[8].value is not None else False
        mosaic_name = p[9].valueAsText if p[9].valueAsText else f"Mozaika_{datetime.datetime.now().strftime('%H%M%S')}"
        out_gdb = p[10].valueAsText
        calc_stats = p[11].value if p[11].value is not None else False
        overwrite = p[12].value if p[12].value is not None else False
        pelny_arkusz = p[13].value if p[13].value is not None else False

        if not in_features:
            return arcpy.AddError("Proszę wskazać obszar na mapie lub warstwę.")

        base_url = self.WFS_ENDPOINTS.get(rodzaj)
        if not base_url:
            return arcpy.AddError(f"Nieznany rodzaj skorowidza: {rodzaj}")

        sr_2180 = arcpy.SpatialReference(2180)
        src_sr = arcpy.Describe(in_features).spatialReference

        tiles_to_download = {}
        arcpy.AddMessage("Faza 1/2: Przeszukiwanie wybranych skorowidzów WFS GUGiK...")

        with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as cur:
            for row in cur:
                geom = row[0]
                if not geom: continue
                if src_sr and src_sr.factoryCode and src_sr.factoryCode != 2180:
                    geom = geom.projectAs(sr_2180)

                ext = geom.extent
                bbox = (ext.XMin - 5.0, ext.YMin - 5.0, ext.XMax + 5.0, ext.YMax + 5.0) if geom.type.lower() == "point" else (ext.XMin, ext.YMin, ext.XMax, ext.YMax)

                try:
                    features = self._find_tiles(base_url, bbox, tylko_aktualna, wybrane_lata, scena, piksel, pelny_arkusz, messages)
                    for attrs in features:
                        url_key = next((k for k in attrs if "url_do_pobrania" in k), None)
                        if not url_key or not attrs[url_key]: continue

                        download_url = attrs[url_key]
                        if download_url not in tiles_to_download:
                            tiles_to_download[download_url] = attrs
                except Exception as e:
                    arcpy.AddWarning(f"Błąd wyszukiwania kafli dla geometrii: {e}")
                    continue

        if not tiles_to_download:
            return arcpy.AddWarning("Nie znaleziono żadnych rastrów spełniających kryteria we wskazanej lokalizacji.")

        arcpy.AddMessage(f"Zakończono wyszukiwanie. Pliki spełniające warunki pobierania: {len(tiles_to_download)}")
        arcpy.AddMessage("Faza 2/2: Pobieranie i rozpakowywanie plików...")

        all_downloaded_rasters = set()
        all_metadata = []

        for url, attrs in tiles_to_download.items():
            try:
                scene_type = self._parse_feature_scene(attrs)
                year_str = self._extract_year(attrs)
                prefix_parts = [p for p in [year_str, scene_type] if p]
                full_prefix = "_".join(prefix_parts)

                local_path = self._download(url, out_folder, full_prefix, messages, overwrite)
                attrs['lokalna_sciezka'] = local_path
                all_metadata.append(attrs)

                rasters = self._extract_rasters(local_path, out_folder, full_prefix)
                all_downloaded_rasters.update(rasters)
            except Exception as e:
                arcpy.AddWarning(f"Błąd pobierania z adresu {url}: {e}")

        all_downloaded_rasters = list(all_downloaded_rasters)

        if all_metadata:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = os.path.join(out_folder, f"metadane_pobierania_{timestamp}.csv")
            fieldnames = sorted(list(set(k for md in all_metadata for k in md.keys())))
            try:
                with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
                    writer.writeheader()
                    writer.writerows(all_metadata)
                arcpy.AddMessage(f"Zapisano zbiorcze metadane: {csv_path}")
            except Exception as e:
                arcpy.AddWarning(f"Błąd podczas zapisu pliku CSV: {e}")

        layers_to_add = all_downloaded_rasters

        if create_mosaic and all_downloaded_rasters:
            if not out_gdb or not arcpy.Exists(out_gdb):
                arcpy.AddError("Brak poprawnej geobazy docelowej dla mozaiki.")
            else:
                arcpy.AddMessage("Tworzenie mozaiki rastrowej (Mosaic Dataset)...")
                try:
                    md_name = arcpy.ValidateTableName(mosaic_name, out_gdb)
                    md_path = os.path.join(out_gdb, md_name)

                    arcpy.management.CreateMosaicDataset(out_gdb, md_name, sr_2180)
                    arcpy.management.AddRastersToMosaicDataset(
                        in_mosaic_dataset=md_path, raster_type="Raster Dataset", input_path=all_downloaded_rasters
                    )
                    if calc_stats:
                         arcpy.management.CalculateStatistics(md_path)
                    layers_to_add = [md_path]
                    arcpy.AddMessage(f"Utworzono mozaikę: {md_path}")
                except Exception as e:
                    arcpy.AddError(f"Błąd podczas tworzenia mozaiki: {e}")

        if len(p) > 15:
            p[15].values = layers_to_add

        if add_to_map and layers_to_add:
            try:
                active_map = arcpy.mp.ArcGISProject("CURRENT").activeMap
                if active_map:
                    for layer_path in layers_to_add:
                        active_map.addDataFromPath(layer_path)
                    arcpy.AddMessage("Dodano wyniki do aktywnej mapy.")
            except Exception as e:
                arcpy.AddWarning(f"Nie udało się dodać warstw do mapy: {e}")

# ==============================================================================
# NARZĘDZIE 3: CHMURY PUNKTÓW
# ==============================================================================
class PobierzChmuryPunktow(object):
    WFS_ENDPOINTS = {
        "PL-KRON86-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarKRON86/WFS/Skorowidze",
        "PL-EVRF2007-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze",
    }

    def __init__(self):
        self.label = "Pobierz chmury punktów LIDAR"
        self.description = "Narysuj obszar lub wybierz warstwę, aby pobrać chmury punktów z WFS dla wybranych układów wysokościowych."
        self.canRunInBackground = False
        self.wfs_client = WFSClient()

    def getParameterInfo(self):
        param_geom = arcpy.Parameter(
            displayName="Wskaż obszar na mapie (ołówek) lub wybierz warstwę",
            name="input_features",
            datatype="GPFeatureRecordSetLayer",
            parameterType="Required",
            direction="Input"
        )

        param_uklad = arcpy.Parameter(
            displayName="Układ wysokościowy",
            name="uklad_wysokosciowy",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_uklad.filter.list = list(self.WFS_ENDPOINTS.keys())
        param_uklad.value = "PL-EVRF2007-NH"

        param_tylko_aktualna = arcpy.Parameter(
            displayName="Pobierz najbardziej aktualne dane dla wybranego skorowidza",
            name="tylko_aktualna",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input"
        )
        param_tylko_aktualna.value = True

        param_lata = arcpy.Parameter(
            displayName="Wybierz interesujące Cię lata (jeśli odznaczono opcję wyżej)",
            name="wybrane_lata",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True
        )
        obecny_rok = datetime.datetime.now().year
        param_lata.filter.list = [str(rok) for rok in range(obecny_rok, 2010, -1)]

        param_out_folder = arcpy.Parameter(
            displayName="Folder docelowy na pobrane pliki chmur punktów",
            name="out_folder",
            datatype="DEFolder",
            parameterType="Required",
            direction="Input"
        )

        param_mosaic = arcpy.Parameter(
            displayName="Utwórz strukturę LAS Dataset z pobranych plików i dodaj do mapy",
            name="create_las_dataset",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_mosaic.value = False

        param_mosaic_name = arcpy.Parameter(
            displayName="Nazwa tworzonego pliku LAS Dataset (.lasd)",
            name="las_dataset_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_mosaic_name.value = "Mozaika_LIDAR.lasd"

        param_overwrite = arcpy.Parameter(
            displayName="Pobierz ponownie, jeśli plik już istnieje",
            name="overwrite",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_overwrite.value = False

        param_pelny = arcpy.Parameter(
            displayName="Pobierz tylko w pełni wypełnione arkusze",
            name="pelny_arkusz",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_pelny.value = False

        param_derived = arcpy.Parameter(
            displayName="Wynikowe pliki/zbiory danych (Ukryta)",
            name="out_datasets",
            datatype="DELasDataset",
            parameterType="Derived",
            direction="Output",
            multiValue=True
        )

        return [
            param_geom, param_uklad, param_tylko_aktualna, param_lata,
            param_out_folder, param_mosaic, param_mosaic_name,
            param_overwrite, param_pelny, param_derived
        ]

    def updateParameters(self, p):
        tylko_aktualna = p[2].value
        p[3].enabled = not tylko_aktualna

        tworz_mozaike = p[5].value
        p[6].enabled = tworz_mozaike

    def _extract_year(self, attrs):
        for k, v in attrs.items():
            if k in ("rok", "rok_nalotu", "rok_zdjecia", "data_zdjecia", "rok_wydania") or "rok" in k:
                match = re.search(r"(19|20)\d{2}", str(v))
                if match: return match.group(0)
        return attrs.get("rok_skorowidza", "")

    def _find_tiles(self, base_url, bbox_2180, tylko_aktualna, wybrane_lata, pelny_arkusz, messages):
        editions = self.wfs_client.get_all_typenames(base_url)

        if not tylko_aktualna and wybrane_lata:
            editions = [(y, tn) for y, tn in editions if str(y) in wybrane_lata]

        all_found = []
        for yr, typename in editions:
            try:
                features = self.wfs_client.get_features(base_url, typename, bbox_2180)

                if features:
                    for f in features:
                        if yr: f['rok_skorowidza'] = str(yr)

                    if pelny_arkusz:
                        features = [f for f in features if str(f.get('czy_ark_wypelniony', '')).strip().lower() in ['1', 'true', 'tak', 't']]

                    if features:
                        if tylko_aktualna: return features
                        all_found.extend(features)
            except Exception as e:
                messages.addWarningMessage(f"Ostrzeżenie (warstwa {typename}): {e}")

        if not all_found:
            raise RuntimeError("Nie znaleziono kafli spełniających kryteria we wszystkich przeszukiwanych edycjach skorowidza.")
        return all_found

    def _download_and_extract(self, url, out_folder, prefix, overwrite, messages):
        os.makedirs(out_folder, exist_ok=True)
        parsed_name = os.path.basename(urllib.parse.urlparse(url).path)
        fname = f"{prefix}_{parsed_name}" if prefix else parsed_name
        out_path = os.path.join(out_folder, fname)

        if not (os.path.exists(out_path) and not overwrite):
            messages.addMessage(f"Rozpoczynanie pobierania pliku LIDAR: {fname}...")
            HttpClient.download_stream(url, out_path, messages=messages)
            messages.addMessage(f"Pobrano: {fname}")

        extracted_files = []
        point_ext = (".las", ".laz")
        out_folder_abs = os.path.abspath(out_folder)

        if out_path.lower().endswith(".zip") and zipfile.is_zipfile(out_path):
            with zipfile.ZipFile(out_path) as z:
                members = [n for n in z.namelist() if n.lower().endswith(point_ext)]
                for name in members:
                    ext_name = f"{prefix}_{os.path.basename(name)}" if prefix else os.path.basename(name)
                    ext_path = os.path.abspath(os.path.join(out_folder, ext_name))

                    if not os.path.commonpath([out_folder_abs, ext_path]).startswith(out_folder_abs):
                        continue

                    if not os.path.exists(ext_path) or overwrite:
                        with open(ext_path, "wb") as f_out:
                            f_out.write(z.read(name))
                    extracted_files.append(ext_path)
        elif out_path.lower().endswith(point_ext):
            extracted_files.append(out_path)

        return extracted_files

    def execute(self, p, messages):
        in_features = p[0].value
        uklad = p[1].valueAsText
        tylko_aktualna = p[2].value

        wybrane_lata = [rok.strip("'\"") for rok in (p[3].valueAsText or "").split(';')] if p[3].valueAsText else []

        out_folder = p[4].valueAsText
        create_lasd = p[5].value if p[5].value is not None else False
        lasd_name = p[6].valueAsText if p[6].valueAsText else "Mozaika_LIDAR.lasd"
        if not lasd_name.lower().endswith('.lasd'): lasd_name += '.lasd'
        overwrite = p[7].value if p[7].value is not None else False
        pelny_arkusz = p[8].value if p[8].value is not None else False

        if not in_features:
            return arcpy.AddError("Proszę wskazać obszar na mapie lub warstwę.")

        base_url = self.WFS_ENDPOINTS.get(uklad)
        sr_2180 = arcpy.SpatialReference(2180)
        src_sr = arcpy.Describe(in_features).spatialReference

        tiles_to_download = {}
        arcpy.AddMessage("Wyszukiwanie arkuszy chmur punktów...")

        with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as cur:
            for row in cur:
                geom = row[0]
                if not geom: continue
                if src_sr and src_sr.factoryCode != 2180: geom = geom.projectAs(sr_2180)

                ext = geom.extent
                bbox = (ext.XMin - 5.0, ext.YMin - 5.0, ext.XMax + 5.0, ext.YMax + 5.0) if geom.type.lower() == "point" else (ext.XMin, ext.YMin, ext.XMax, ext.YMax)

                try:
                    features = self._find_tiles(base_url, bbox, tylko_aktualna, wybrane_lata, pelny_arkusz, messages)

                    for attrs in features:
                        url_key = next((k for k in attrs if "url_do_pobrania" in k), None)
                        if url_key and attrs[url_key]:
                            tiles_to_download[attrs[url_key]] = attrs
                except Exception as e:
                    arcpy.AddWarning(f"Błąd wyszukiwania: {e}")

        if not tiles_to_download:
            return arcpy.AddWarning("Nie znaleziono chmur punktów we wskazanej lokalizacji.")

        arcpy.AddMessage(f"Zakończono wyszukiwanie. Pliki spełniające warunki pobierania: {len(tiles_to_download)}. Rozpoczynam pobieranie...")
        all_point_clouds = set()
        all_metadata = []

        for url, attrs in tiles_to_download.items():
            try:
                year_str = self._extract_year(attrs)
                files = self._download_and_extract(url, out_folder, year_str, overwrite, messages)
                all_point_clouds.update(files)

                attrs['lokalna_sciezka'] = ", ".join(files)
                all_metadata.append(attrs)
            except Exception as e:
                arcpy.AddWarning(f"Błąd pobierania z {url}: {e}")

        all_point_clouds = list(all_point_clouds)

        if all_metadata:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = os.path.join(out_folder, f"metadane_lidar_{timestamp}.csv")
            fieldnames = sorted(list(set(k for md in all_metadata for k in md.keys())))
            try:
                with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
                    writer.writeheader()
                    writer.writerows(all_metadata)
                arcpy.AddMessage(f"Zapisano zbiorcze metadane: {csv_path}")
            except Exception as e:
                arcpy.AddWarning(f"Błąd podczas zapisu pliku CSV: {e}")

        layers_to_add = []

        if create_lasd and all_point_clouds:
            lasd_path = os.path.join(out_folder, lasd_name)
            arcpy.AddMessage(f"Tworzenie pliku LAS Dataset: {lasd_path}...")
            try:
                arcpy.management.CreateLasDataset(
                    input=all_point_clouds,
                    out_las_dataset=lasd_path,
                    spatial_reference=sr_2180
                )
                layers_to_add = [lasd_path]
            except Exception as e:
                arcpy.AddError(f"Błąd tworzenia LAS Dataset: {e}")

        if len(p) > 9: p[9].values = layers_to_add

        if create_lasd and layers_to_add:
            try:
                active_map = arcpy.mp.ArcGISProject("CURRENT").activeMap
                if active_map:
                    for layer in layers_to_add:
                        active_map.addDataFromPath(layer)
                    arcpy.AddMessage("Dodano plik LAS Dataset do aktywnej mapy.")
            except Exception as e:
                arcpy.AddWarning(f"Błąd dodawania wyników do mapy: {e}")

# ==============================================================================
# NARZĘDZIE 4: NMT / NMPT
# ==============================================================================
class PobierzNMTNMPT(object):
    WFS_ENDPOINTS = {
        "NMT": {
            "PL-EVRF2007-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NumerycznyModelTerenuEVRF2007/WFS/Skorowidze",
            "PL-KRON86-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NumerycznyModelTerenuKRON86/WFS/Skorowidze",
        },
        "NMPT": {
            "PL-EVRF2007-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NumerycznyModelPokryciaTerenuEVRF2007/WFS/Skorowidze",
            "PL-KRON86-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NumerycznyModelPokryciaTerenuKRON86/WFS/Skorowidze",
        }
    }

    def __init__(self):
        self.label = "Pobierz NMT / NMPT"
        self.description = "Pobiera pliki NMT/NMPT z WFS GUGiK dla wybranego obszaru, nadaje układ 2180 i opcjonalnie tworzy mozaikę."
        self.canRunInBackground = False
        self.wfs_client = WFSClient()

    def getParameterInfo(self):
        param_geom = arcpy.Parameter(
            displayName="Wskaż obszar na mapie (ołówek) lub wybierz warstwę",
            name="input_features",
            datatype="GPFeatureRecordSetLayer",
            parameterType="Required",
            direction="Input"
        )

        param_typ_danych = arcpy.Parameter(
            displayName="Typ danych",
            name="typ_danych",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_typ_danych.filter.list = ["NMT (Model Terenu)", "NMPT (Model Pokrycia Terenu)"]
        param_typ_danych.value = "NMT (Model Terenu)"

        param_uklad = arcpy.Parameter(
            displayName="Układ wysokościowy",
            name="uklad_wysokosciowy",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_uklad.filter.list = ["PL-EVRF2007-NH", "PL-KRON86-NH"]
        param_uklad.value = "PL-EVRF2007-NH"

        param_tylko_aktualna = arcpy.Parameter(
            displayName="Pobierz najbardziej aktualne dane dla wybranego skorowidza",
            name="tylko_aktualna",
            datatype="GPBoolean",
            parameterType="Required",
            direction="Input"
        )
        param_tylko_aktualna.value = True

        param_lata = arcpy.Parameter(
            displayName="Wybierz interesujące Cię lata (jeśli odznaczono opcję wyżej)",
            name="wybrane_lata",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
            multiValue=True
        )
        obecny_rok = datetime.datetime.now().year
        param_lata.filter.list = [str(rok) for rok in range(obecny_rok, 2000, -1)]

        param_format = arcpy.Parameter(
            displayName="Format danych (opcjonalny filtr)",
            name="format_danych",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_format.filter.list = ["Wszystkie", "Arc/Info ASCII Grid (.asc)", "TIFF (.tif / .tiff)"]
        param_format.value = "Wszystkie"

        param_rozdzielczosc = arcpy.Parameter(
            displayName="Rozdzielczość przestrzenna (m) [opcjonalnie]",
            name="rozdzielczosc",
            datatype="GPDouble",
            parameterType="Optional",
            direction="Input"
        )
        param_rozdzielczosc.filter.type = "Range"
        param_rozdzielczosc.filter.list = [0.1, 100.0]

        param_out_folder = arcpy.Parameter(
            displayName="Folder docelowy na pobrane pliki",
            name="out_folder",
            datatype="DEFolder",
            parameterType="Required",
            direction="Input"
        )

        param_mosaic = arcpy.Parameter(
            displayName="Zmozaikuj pobrane rastry do nowego pliku TIF i dodaj do mapy",
            name="create_mosaic",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_mosaic.value = False

        param_mosaic_name = arcpy.Parameter(
            displayName="Nazwa pliku mozaiki (.tif)",
            name="mosaic_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_mosaic_name.value = "Mozaika_NMT.tif"

        param_overwrite = arcpy.Parameter(
            displayName="Pobierz ponownie, jeśli plik już istnieje",
            name="overwrite",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_overwrite.value = False

        param_pelny = arcpy.Parameter(
            displayName="Pobierz tylko w pełni wypełnione arkusze",
            name="pelny_arkusz",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_pelny.value = False

        param_derived = arcpy.Parameter(
            displayName="Wynikowe pliki/zbiory danych (Ukryta)",
            name="out_datasets",
            datatype="DERasterDataset",
            parameterType="Derived",
            direction="Output",
            multiValue=True
        )

        return [
            param_geom, param_typ_danych, param_uklad, param_tylko_aktualna, param_lata,
            param_format, param_rozdzielczosc, param_out_folder, param_mosaic, param_mosaic_name,
            param_overwrite, param_pelny, param_derived
        ]

    def updateParameters(self, p):
        tylko_aktualna = p[3].value
        p[4].enabled = not tylko_aktualna

        tworz_mozaike = p[8].value
        p[9].enabled = tworz_mozaike

    def _extract_year(self, attrs):
        for k, v in attrs.items():
            if k in ("rok", "rok_nalotu", "rok_zdjecia", "data_zdjecia", "rok_wydania") or "rok" in k:
                match = re.search(r"(19|20)\d{2}", str(v))
                if match: return match.group(0)
        return attrs.get("rok_skorowidza", "")

    def _find_tiles(self, base_url, bbox_2180, tylko_aktualna, wybrane_lata, format_danych, rozdzielczosc, pelny_arkusz, messages):
        editions = self.wfs_client.get_all_typenames(base_url)

        if not tylko_aktualna and wybrane_lata:
            editions = [(y, tn) for y, tn in editions if str(y) in wybrane_lata]

        all_found = []
        for yr, typename in editions:
            try:
                features = self.wfs_client.get_features(base_url, typename, bbox_2180)

                if features:
                    for f in features:
                        if yr: f['rok_skorowidza'] = str(yr)

                    if pelny_arkusz:
                        features = [f for f in features if str(f.get('czy_ark_wypelniony', '')).strip().lower() in ['1', 'true', 'tak', 't']]

                    if format_danych and format_danych != "Wszystkie":
                        fmt_key = "asc" if "ASCII" in format_danych else "tif"
                        features = [f for f in features if fmt_key in str(f.get('format', '')).lower() or fmt_key in str(f.get('url_do_pobrania', '')).lower()]

                    if rozdzielczosc is not None:
                        filtered_by_res = []
                        for f in features:
                            res_val = f.get('char_przestrz', '')
                            if res_val:
                                try:
                                    match = re.search(r"(\d+(?:[\.,]\d+)?)", str(res_val))
                                    if match:
                                        parsed_res = float(match.group(1).replace(',', '.'))
                                        if abs(parsed_res - rozdzielczosc) < 1e-4:
                                            filtered_by_res.append(f)
                                except ValueError:
                                    pass
                        features = filtered_by_res

                    if features:
                        if tylko_aktualna:
                            return features
                        all_found.extend(features)
            except Exception as e:
                messages.addWarningMessage(f"Ostrzeżenie (warstwa {typename}): {e}")

        if not all_found:
            raise RuntimeError("Nie znaleziono kafli spełniających kryteria.")
        return all_found

    def _download_and_extract(self, url, out_folder, prefix, overwrite, sr_2180, messages):
        os.makedirs(out_folder, exist_ok=True)
        parsed_name = os.path.basename(urllib.parse.urlparse(url).path)
        fname = f"{prefix}_{parsed_name}" if prefix else parsed_name
        out_path = os.path.join(out_folder, fname)

        if not (os.path.exists(out_path) and not overwrite):
            messages.addMessage(f"Pobieranie: {fname}...")
            HttpClient.download_stream(url, out_path, messages=messages)
            messages.addMessage(f"Pobrano: {fname}")

        extracted_files = []
        raster_ext = (".asc", ".tif", ".tiff", ".img", ".xyz")
        out_folder_abs = os.path.abspath(out_folder)

        if out_path.lower().endswith(".zip") and zipfile.is_zipfile(out_path):
            with zipfile.ZipFile(out_path) as z:
                members = [n for n in z.namelist() if n.lower().endswith(raster_ext)]
                for name in members:
                    ext_name = f"{prefix}_{os.path.basename(name)}" if prefix else os.path.basename(name)
                    ext_path = os.path.abspath(os.path.join(out_folder, ext_name))

                    if not os.path.commonpath([out_folder_abs, ext_path]).startswith(out_folder_abs):
                        continue

                    if not os.path.exists(ext_path) or overwrite:
                        with open(ext_path, "wb") as f_out:
                            f_out.write(z.read(name))
                    extracted_files.append(ext_path)
        elif out_path.lower().endswith(raster_ext):
            extracted_files.append(out_path)

        prj_wkt = sr_2180.exportToString()
        for r_file in extracted_files:
            if r_file.lower().endswith(".asc"):
                prj_file = os.path.splitext(r_file)[0] + ".prj"
                if not os.path.exists(prj_file) or overwrite:
                    try:
                        with open(prj_file, "w", encoding="utf-8") as f_prj:
                            f_prj.write(prj_wkt)
                    except Exception:
                        pass

        return extracted_files

    def execute(self, p, messages):
        in_features = p[0].value
        typ_danych_val = p[1].valueAsText
        key_typ = "NMPT" if "NMPT" in typ_danych_val else "NMT"
        uklad = p[2].valueAsText
        tylko_aktualna = p[3].value
        wybrane_lata = [rok.strip("'\"") for rok in (p[4].valueAsText or "").split(';')] if p[4].valueAsText else []
        format_danych = p[5].valueAsText

        rozdzielczosc = float(p[6].value) if p[6].value is not None else None

        out_folder = p[7].valueAsText
        create_mosaic = p[8].value if p[8].value is not None else False
        mosaic_name = p[9].valueAsText if p[9].valueAsText else f"Mozaika_{key_typ}.tif"
        if not mosaic_name.lower().endswith('.tif'):
            mosaic_name += '.tif'
        overwrite = p[10].value if p[10].value is not None else False
        pelny_arkusz = p[11].value if p[11].value is not None else False

        if not in_features:
            return arcpy.AddError("Proszę wskazać obszar na mapie lub warstwę.")

        if not out_folder:
            return arcpy.AddError("Nie wskazano folderu docelowego dla pobieranych plików.")

        base_url = self.WFS_ENDPOINTS[key_typ].get(uklad)
        sr_2180 = arcpy.SpatialReference(2180)
        src_sr = arcpy.Describe(in_features).spatialReference

        tiles_to_download = {}
        arcpy.AddMessage(f"Wyszukiwanie arkuszy {key_typ}...")

        with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as cur:
            for row in cur:
                geom = row[0]
                if not geom:
                    continue
                if src_sr and src_sr.factoryCode != 2180:
                    geom = geom.projectAs(sr_2180)

                ext = geom.extent
                bbox = (ext.XMin - 5.0, ext.YMin - 5.0, ext.XMax + 5.0, ext.YMax + 5.0) if geom.type.lower() == "point" else (ext.XMin, ext.YMin, ext.XMax, ext.YMax)

                try:
                    features = self._find_tiles(base_url, bbox, tylko_aktualna, wybrane_lata, format_danych, rozdzielczosc, pelny_arkusz, messages)
                    for attrs in features:
                        url_key = next((k for k in attrs if "url_do_pobrania" in k), None)
                        if url_key and attrs[url_key]:
                            tiles_to_download[attrs[url_key]] = attrs
                except Exception as e:
                    arcpy.AddWarning(f"Błąd wyszukiwania: {e}")

        if not tiles_to_download:
            return arcpy.AddWarning(f"Nie znaleziono plików {key_typ} we wskazanej lokalizacji z podanymi kryteriami.")

        arcpy.AddMessage(f"Znaleziono {len(tiles_to_download)} unikalnych plików {key_typ}. Rozpoczynanie pobierania...")
        all_rasters = set()
        all_metadata = []

        for url, attrs in tiles_to_download.items():
            try:
                year_str = self._extract_year(attrs)
                files = self._download_and_extract(url, out_folder, year_str, overwrite, sr_2180, messages)
                all_rasters.update(files)

                attrs['lokalna_sciezka'] = ", ".join(files)
                all_metadata.append(attrs)
            except Exception as e:
                arcpy.AddWarning(f"Błąd pobierania z {url}: {e}")

        all_rasters = list(all_rasters)

        if all_metadata:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = os.path.join(out_folder, f"metadane_{key_typ.lower()}_{timestamp}.csv")
            fieldnames = sorted(list(set(k for md in all_metadata for k in md.keys())))
            try:
                with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
                    writer.writeheader()
                    writer.writerows(all_metadata)
                arcpy.AddMessage(f"Zapisano metadane {key_typ}: {csv_path}")
            except Exception as e:
                arcpy.AddWarning(f"Błąd podczas zapisu pliku CSV: {e}")

        layers_to_add = []

        if create_mosaic and all_rasters:
            mosaic_path = os.path.join(out_folder, mosaic_name)
            arcpy.AddMessage(f"Tworzenie mozaiki rastrowej: {mosaic_path}...")
            try:
                arcpy.management.MosaicToNewRaster(
                    input_rasters=all_rasters,
                    output_location=out_folder,
                    raster_dataset_name_with_extension=mosaic_name,
                    coordinate_system_for_the_raster=sr_2180,
                    pixel_type="32_BIT_FLOAT",
                    number_of_bands=1,
                    mosaic_method="LAST"
                )
                layers_to_add = [mosaic_path]
            except Exception as e:
                arcpy.AddError(f"Błąd podczas tworzenia mozaiki: {e}")
        else:
            layers_to_add = all_rasters

        if len(p) > 12:
            p[12].values = layers_to_add

        if layers_to_add:
            try:
                active_map = arcpy.mp.ArcGISProject("CURRENT").activeMap
                if active_map:
                    for layer in layers_to_add:
                        active_map.addDataFromPath(layer)
                    arcpy.AddMessage("Dodano wyniki do aktywnej mapy.")
            except Exception as e:
                arcpy.AddWarning(f"Błąd dodawania wyników do mapy: {e}")

# ==============================================================================
# NARZĘDZIE 5: SKOROWIDZ EGIB (WFS - Z AUTOMATYCZNYM KAFELKOWANIEM BBOX)
# ==============================================================================
class PobierzSkorowidzEGIB(object):
    """
    Pobiera geometrie działek i/lub budynków z Usługi Zbiorczej WFS EGiB GUGiK
    dla wskazanego obszaru za pomocą zapytań WFS 2.0.0 z automatycznym kafelkowaniem obszaru.
    Raportuje postęp pobierania w paczkach po 100 obiektów.
    """

    WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza"

    def __init__(self):
        self.label = "Pobierz obrys działek/budynków (WFS)"
        self.description = "Pobiera geometrie działek lub budynków ze zbiorczej usługi WFS EGiB dla podanego obszaru z podziałem na mniejsze kafelki."
        self.canRunInBackground = False

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
        """Dzieli zasięg (extent) na siatkę mniejszych BBOX-ów w metrach[cite: 4]."""
        bboxes = []
        x = ext.XMin
        while x < ext.XMax:
            next_x = min(x + tile_size, ext.XMax)
            y = ext.YMin
            while y < ext.YMax:
                next_y = min(y + tile_size, ext.YMax)
                bboxes.append((x, y, next_x, next_y))
                y = next_y
            x = next_x
        return bboxes

    def _get_wfs_features(self, bbox_2180, type_name):
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
            arcpy.AddWarning(f"Błąd zapytania WFS (GET): {e}")
            return []

        if not data or b"ExceptionReport" in data:
            return []

        try:
            root = ET.fromstring(data)
        except Exception:
            return []

        features_data = []

        for feat in root.iter():
            local = feat.tag.split("}")[-1]
            if local in ("member", "featureMember"):
                children = list(feat)
                if not children: continue
                child_elem = children[0]

                attrs = {}
                wkt_geom = None

                pos_lists = child_elem.findall(".//{*}posList")
                if pos_lists:
                    rings = []
                    for pl in pos_lists:
                        if not pl.text: continue
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
                    if not list(child) and child.text and child.text.strip():
                        attrs[tag] = child.text.strip()

                if wkt_geom:
                    features_data.append((wkt_geom, attrs))

        return features_data

    def execute(self, p, messages):
        in_features = p[0].value
        typ_obj = p[1].valueAsText
        type_name = "ms:dzialki" if "Działki" in typ_obj else "ms:budynki"

        out_ws = p[2].valueAsText
        raw_out_name = p[3].valueAsText

        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', raw_out_name)
        if safe_name and safe_name[0].isdigit():
            safe_name = "L_" + safe_name

        out_name = arcpy.ValidateTableName(safe_name, out_ws)
        out_fc = os.path.join(out_ws, out_name)
        sr_2180 = arcpy.SpatialReference(2180)

        processed_ids = set()

        if not arcpy.Exists(out_fc):
            arcpy.CreateFeatureclass_management(out_ws, out_name, "POLYGON", spatial_reference=sr_2180)
            arcpy.management.AddField(out_fc, "ID_OBIEKTU", "TEXT", field_length=100)
            arcpy.management.AddField(out_fc, "INFO", "TEXT", field_length=255)
            arcpy.AddMessage(f"Utworzono nową warstwę: {out_name}")
        else:
            try:
                with arcpy.da.SearchCursor(out_fc, ["ID_OBIEKTU"]) as sc:
                    for r in sc:
                        if r[0]: processed_ids.add(r[0])
                arcpy.AddMessage(f"Warstwa '{out_name}' istnieje. Dopisywanie obiektów (wczytano {len(processed_ids)} istniejących).")
            except Exception as e:
                arcpy.AddError(f"Błąd odczytu istniejącej warstwy: {e}")
                return

        src_sr = arcpy.Describe(in_features).spatialReference
        initial_count = len(processed_ids)
        current_added = 0

        arcpy.AddMessage(f"Pobieranie obiektów z WFS EGiB ({type_name}). Rozpoczynam przetwarzanie...")

        insert_fields = ["SHAPE@", "ID_OBIEKTU", "INFO"]
        with arcpy.da.InsertCursor(out_fc, insert_fields) as cursor:
            with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as search_cur:
                for row in search_cur:
                    input_geom = row[0]
                    if not input_geom: continue

                    if src_sr and src_sr.factoryCode != 2180:
                        input_geom = input_geom.projectAs(sr_2180)

                    ext = input_geom.extent
                    # Dzielimy obszar na kafelki 500x500 metrow[cite: 4]
                    sub_bboxes = self._split_bbox(ext, tile_size=500.0)

                    for bbox in sub_bboxes:
                        # Odrzucamy kafelki poza poligonem wejściowym[cite: 4]
                        tile_poly = arcpy.Polygon(arcpy.Array([
                            arcpy.Point(bbox[0], bbox[1]),
                            arcpy.Point(bbox[0], bbox[3]),
                            arcpy.Point(bbox[2], bbox[3]),
                            arcpy.Point(bbox[2], bbox[1])
                        ]), sr_2180)

                        if input_geom.disjoint(tile_poly):
                            continue

                        try:
                            features = self._get_wfs_features(bbox, type_name)
                            for wkt, attrs in features:
                                try:
                                    poly_geom = arcpy.FromWKT(wkt, sr_2180)

                                    if poly_geom and not poly_geom.disjoint(input_geom):
                                        obj_id = attrs.get("ID_DZIALKI", attrs.get("ID_BUDYNKU", attrs.get("gml_id", "Nieznany")))

                                        if obj_id in processed_ids and obj_id != "Nieznany":
                                            continue

                                        info = str(attrs)[:250]
                                        cursor.insertRow((poly_geom, obj_id, info))
                                        processed_ids.add(obj_id)

                                        # Zliczanie pobranych i zapisanych obiektów z powiadomieniem co 100
                                        current_added += 1
                                        if current_added % 100 == 0:
                                            arcpy.AddMessage(f"Pobrano i zapisano {current_added} obiektów ({typ_obj.lower()})...")

                                except Exception as ge:
                                    arcpy.AddWarning(f"Błąd geometrii obiektu: {ge}")
                        except Exception as e:
                            arcpy.AddWarning(f"Błąd zapytania WFS: {e}")

        added_count = len(processed_ids) - initial_count
        arcpy.AddMessage(f"Zakończono! Łącznie pobrano i dopisano obiektów: {added_count}")

        if added_count == 0:
            arcpy.AddWarning("W przypadku braku obiektów, sprawdź stan usługi dla wybranego powiatu na stronie EZiUDP.")

        if len(p) > 5:
            p[5].value = out_fc

        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_map = aprx.activeMap
            active_view = aprx.activeView

            if active_map:
                layers = active_map.listLayers(out_name)
                if not layers:
                    active_map.addDataFromPath(out_fc)
                    arcpy.AddMessage("Dodano warstwę do aktywnej mapy.")

                if active_view:
                    active_view.refresh()
        except Exception:
            pass