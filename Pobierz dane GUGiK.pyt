# -*- coding: utf-8 -*-
import arcpy
import urllib.request
import os
import json
import re

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

class Toolbox(object):
    def __init__(self):
        self.label = "Pobierz dane GUGiK"
        self.alias = "GUGiK_tools"
        self.tools = [PobierzDzialkeULDK]

class PobierzDzialkeULDK(object):
    def __init__(self):
        self.label = "Pobierz geometrię z ULDK"
        self.description = "Pobiera poligony działek, obrębów, gmin, powiatów lub województw."
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
        param_author.value = "Autor: Mateusz Lubański m.lubanski94@gmail.com"

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
        
        # Wymagaj numeru działki tylko jeśli wybrano typ "Działka" i kaskadę
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

            woj_list = sorted([f"{v} ({k})" for k, v in db.get("wojewodztwa", {}).items()])
            if p[4].filter.list != woj_list:
                p[4].filter.list = woj_list
            if not p[4].valueAsText and woj_list:
                p[4].value = woj_list[0]

            woj_val = p[4].valueAsText
            woj_id = woj_val.split('(')[-1].replace(')','') if woj_val else None

            pow_list = []
            if woj_id and woj_id in db.get("powiaty", {}):
                pow_list = sorted([f"{v} ({k})" for k, v in db["powiaty"][woj_id].items()])
                
            if p[5].filter.list != pow_list and pow_list:
                p[5].filter.list = pow_list
                p[5].value = pow_list[0]
            elif not p[5].valueAsText and pow_list:
                p[5].value = pow_list[0]

            pow_val = p[5].valueAsText
            pow_id = pow_val.split('(')[-1].replace(')','') if pow_val else None

            gmi_list = []
            if pow_id and pow_id in db.get("gminy", {}):
                gmi_list = sorted([f"{v} ({k})" for k, v in db["gminy"][pow_id].items()])
                
            if p[6].filter.list != gmi_list and gmi_list:
                p[6].filter.list = gmi_list
                p[6].value = gmi_list[0]
            elif not p[6].valueAsText and gmi_list:
                p[6].value = gmi_list[0]

            gmi_val = p[6].valueAsText
            gmi_id = gmi_val.split('(')[-1].replace(')','') if gmi_val else None

            obr_list = []
            if gmi_id and gmi_id in db.get("obreby", {}):
                obr_list = sorted([f"{v} ({k})" for k, v in db["obreby"][gmi_id].items()])
                
            if p[7].filter.list != obr_list and obr_list:
                p[7].filter.list = obr_list
                p[7].value = obr_list[0]
            elif not p[7].valueAsText and obr_list:
                p[7].value = obr_list[0]

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
        
        # Mapowanie typu na parametry zapytania
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
                    # Ekstrakcja TERYT dla jednostek nadrzędnych
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
            req = urllib.request.Request(req_url)
            with urllib.request.urlopen(req) as response:
                resp_text = response.read().decode('utf-8').strip()
        except Exception as e:
            arcpy.AddWarning(f"Błąd sieci: {e}")
            return extent

        lines = resp_text.split('\n')
        if lines[0].strip() != "0" or len(lines) < 2:
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
                
                # Dynamiczne mapowanie atrybutów w zależności od zapytania
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