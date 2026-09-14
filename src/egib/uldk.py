# -*- coding: utf-8 -*-
import arcpy
import os
import re
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


class PobierzDzialkeULDKImpl(object):
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
        param_typ.value = "Powiat"

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
        if not p or not p[0].value:
            return

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

            woj_list = sorted([f"{v} ({k})" for k, v in db.get("wojewodztwa", {}).items()])
            if p[4].filter.list != woj_list:
                p[4].filter.list = woj_list

            woj_id = extract_id(p[4].valueAsText)

            pow_list = []
            if woj_id and woj_id in db.get("powiaty", {}):
                pow_list = sorted([f"{v} ({k})" for k, v in db["powiaty"][woj_id].items()])

            if p[5].filter.list != pow_list:
                p[5].filter.list = pow_list
                if p[5].valueAsText and p[5].valueAsText not in pow_list:
                    p[5].value = None

            pow_id = extract_id(p[5].valueAsText)

            gmi_list = []
            if pow_id and pow_id in db.get("gminy", {}):
                gmi_list = sorted([f"{v} ({k})" for k, v in db["gminy"][pow_id].items()])

            if p[6].filter.list != gmi_list:
                p[6].filter.list = gmi_list
                if p[6].valueAsText and p[6].valueAsText not in gmi_list:
                    p[6].value = None

            gmi_id = extract_id(p[6].valueAsText)

            obr_list = []
            if gmi_id and gmi_id in db.get("obreby", {}):
                obr_list = sorted([f"{v} ({k})" for k, v in db["obreby"][gmi_id].items()])

            if p[7].filter.list != obr_list:
                p[7].filter.list = obr_list
                if p[7].valueAsText and p[7].valueAsText not in obr_list:
                    p[7].value = None

    def execute(self, p, messages):
        arcpy.AddMessage("--- Uruchomiono pobieranie ULDK ---")
        method = p[0].valueAsText
        typ_obiektu = p[1].valueAsText
        point_fs = p[2].value
        parcel_id = p[3].valueAsText

        woj_id = extract_id(p[4].valueAsText)
        pow_id = extract_id(p[5].valueAsText)
        gmi_id = extract_id(p[6].valueAsText)
        obr_id = extract_id(p[7].valueAsText)
        dz_nr_val = p[8].valueAsText

        out_ws = p[9].valueAsText
        raw_out_name = p[10].valueAsText

        # Zachowanie polskich znaków diakrytycznych
        safe_name = re.sub(r'[^\w]', '_', raw_out_name)
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
        errors = []

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
                        if row[0]:
                            processed_ids.add(str(row[0]).strip())
                arcpy.AddMessage(f"Wczytano {len(processed_ids)} wcześniej pobranych obiektów z warstwy.")
            except Exception as e:
                arcpy.AddError(f"Warstwa istnieje, ale ma nieprawidłową strukturę: {e}")
                return

        initial_count = len(processed_ids)
        insert_fields = ["SHAPE@", "ID_TERYT", "TYP_OBIEKTU", "WOJEWODZTWO", "POWIAT", "GMINA", "OBREB", "NUMER_DZIALKI", "NAZWA"]

        # Przygotowanie kolejki zapytań do obliczenia łącznej liczby kroków progressora
        queries = []
        if method == "Kliknięcie na mapie":
            if not point_fs:
                return arcpy.AddError("Proszę wskazać punkt na mapie.")
            srid = arcpy.Describe(point_fs).spatialReference.factoryCode or 2180
            with arcpy.da.SearchCursor(point_fs, ["SHAPE@XY"]) as s_cur:
                for row in s_cur:
                    if row[0] and None not in row[0]:
                        xy = f"{row[0][0]},{row[0][1]}" if str(srid) == "2180" else f"{row[0][0]},{row[0][1]},{srid}"
                        url = f"{url_base}?request=Get{api_endpoint}ByXY&xy={xy}&result={result_param}"
                        queries.append((url, f"Punkt: {xy}"))

        elif method == "Identyfikator TERYT":
            if not parcel_id:
                return arcpy.AddError("Proszę podać identyfikator.")
            for p_id in [i.strip() for i in parcel_id.split(',') if i.strip()]:
                url = f"{url_base}?request=Get{api_endpoint}ById&id={p_id}&result={result_param}"
                queries.append((url, p_id))

        elif method == "Kaskadowy wybór z listy (Woj -> Pow -> Gmi -> Obr)":
            db = load_teryt_db()
            if not db:
                return arcpy.AddError("Nie udało się załadować bazy slownik_teryt.json!")

            if typ_obiektu == "Działka":
                if not obr_id or not dz_nr_val:
                    return arcpy.AddError("Do pobrania działek niezbędne jest wybranie obrębu oraz numeru działki.")
                for nr in dz_nr_val.split(','):
                    clean_nr = nr.strip()
                    if clean_nr:
                        url = f"{url_base}?request=GetParcelById&id={obr_id}.{clean_nr}&result={result_param}"
                        queries.append((url, f"{obr_id}.{clean_nr}"))
            else:
                to_fetch = []
                if typ_obiektu == "Województwo":
                    if woj_id:
                        to_fetch.append((woj_id, p[4].valueAsText))
                    else:
                        for k, v in db.get("wojewodztwa", {}).items():
                            to_fetch.append((k, v))
                elif typ_obiektu == "Powiat":
                    if pow_id:
                        to_fetch.append((pow_id, p[5].valueAsText))
                    elif woj_id:
                        for k, v in db.get("powiaty", {}).get(woj_id, {}).items():
                            to_fetch.append((k, v))
                    else:
                        for w_id, pow_dict in db.get("powiaty", {}).items():
                            for k, v in pow_dict.items():
                                to_fetch.append((k, v))
                elif typ_obiektu == "Gmina":
                    if gmi_id:
                        to_fetch.append((gmi_id, p[6].valueAsText))
                    elif pow_id:
                        for k, v in db.get("gminy", {}).get(pow_id, {}).items():
                            to_fetch.append((k, v))
                    elif woj_id:
                        for cur_pow in db.get("powiaty", {}).get(woj_id, {}).keys():
                            for k, v in db.get("gminy", {}).get(cur_pow, {}).items():
                                to_fetch.append((k, v))
                    else:
                        for cur_pow, gmi_dict in db.get("gminy", {}).items():
                            for k, v in gmi_dict.items():
                                to_fetch.append((k, v))
                elif typ_obiektu == "Obręb":
                    if obr_id:
                        to_fetch.append((obr_id, p[7].valueAsText))
                    elif gmi_id:
                        for k, v in db.get("obreby", {}).get(gmi_id, {}).items():
                            to_fetch.append((k, v))
                    elif pow_id:
                        for cur_gmi in db.get("gminy", {}).get(pow_id, {}).keys():
                            for k, v in db.get("obreby", {}).get(cur_gmi, {}).items():
                                to_fetch.append((k, v))
                    else:
                        return arcpy.AddError("Aby pobrać obręby, wskaż co najmniej powiat lub gminę.")

                for unit_id, unit_name in to_fetch:
                    clean_id = str(unit_id).strip()
                    url = f"{url_base}?request=Get{api_endpoint}ById&id={clean_id}&result={result_param}"
                    queries.append((url, f"{unit_name} ({clean_id})"))

        total_queries = len(queries)
        if total_queries == 0:
            return arcpy.AddWarning("Brak obiektów spełniających podane kryteria do odpytania.")

        # Total steps = liczba zapytań + 1 krok na finalizację widoku mapy
        total_steps = total_queries + 1
        current_step = 0
        arcpy.SetProgressor("step", f"Pobieranie danych ULDK (0/{total_queries})...", 0, total_steps, 1)

        with arcpy.da.InsertCursor(out_fc, insert_fields) as cursor:
            for idx, (url, item_label) in enumerate(queries, start=1):
                arcpy.SetProgressorLabel(f"Pobieranie [{idx}/{total_queries}]: {item_label}")
                full_extent = self.fetch_and_insert(url, cursor, processed_ids, full_extent, sr_2180, typ_obiektu, errors)
                current_step += 1
                arcpy.SetProgressorPosition(current_step)

        arcpy.AddMessage(f"Dodano {len(processed_ids) - initial_count} nowych obiektów.")

        if len(p) > 12:
            p[12].value = out_fc

        arcpy.SetProgressorLabel("Aktualizacja widoku mapy...")
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_view = aprx.activeView
            active_map = aprx.activeMap

            if active_map:
                layers = active_map.listLayers(out_name)
                if not layers:
                    active_map.addDataFromPath(out_fc)

                if full_extent and active_view:
                    bx = (full_extent.XMax - full_extent.XMin) * 0.1 or 10
                    by = (full_extent.YMax - full_extent.YMin) * 0.1 or 10
                    full_extent.XMin -= bx
                    full_extent.YMin -= by
                    full_extent.XMax += bx
                    full_extent.YMax += by
                    active_view.camera.setExtent(full_extent)
        except Exception as e:
            errors.append(f"Aktualizacja widoku mapy: {e}")

        arcpy.SetProgressorPosition(total_steps)
        arcpy.ResetProgressor()

        if errors:
            arcpy.AddWarning(f"--- Raport błędów i ostrzeżeń ({len(errors)}) ---")
            for err in errors:
                arcpy.AddWarning(f"  • {err}")

        arcpy.AddMessage("Zakończono pomyślnie!")

    def fetch_and_insert(self, req_url, cursor, processed_ids, extent, sr_2180, typ_obiektu, errors):
        try:
            resp_text = HttpClient.fetch_text(req_url, timeout=30)
        except Exception as e:
            errors.append(f"Błąd połączenia ({req_url}): {e}")
            return extent

        lines = [line.strip() for line in resp_text.split('\n') if line.strip()]
        if not lines:
            return extent

        status = lines[0].strip()
        if status != "0":
            err_details = " ".join(lines[1:]) if len(lines) > 1 else ""
            errors.append(f"ULDK ({status}): {err_details} | URL: {req_url}")
            return extent

        if len(lines) < 2:
            return extent

        for data_line in lines[1:]:
            parts = data_line.strip().split('|')
            if len(parts) < 3:
                continue

            d_id = parts[0].strip()
            if d_id in processed_ids:
                continue

            try:
                geom_wkt = parts[-1].split(';')[-1].strip()
                geom = arcpy.FromWKT(geom_wkt, sr_2180)
                if not geom or geom.area == 0:
                    continue

                woj = pow_name = gmi = obr = num = nazwa = None
                if typ_obiektu == "Działka" and len(parts) >= 7:
                    woj, pow_name, gmi, obr, num = parts[1:6]
                elif typ_obiektu == "Obręb" and len(parts) >= 6:
                    woj, pow_name, gmi, nazwa = parts[1:5]
                elif typ_obiektu == "Gmina" and len(parts) >= 5:
                    woj, pow_name, nazwa = parts[1:4]
                elif typ_obiektu == "Powiat" and len(parts) >= 4:
                    woj, nazwa = parts[1:3]
                elif typ_obiektu == "Województwo" and len(parts) >= 3:
                    nazwa = parts[1]

                cursor.insertRow((geom, d_id, typ_obiektu, woj, pow_name, gmi, obr, num, nazwa))
                processed_ids.add(d_id)

                if extent is None:
                    extent = geom.extent
                else:
                    extent.XMin = min(extent.XMin, geom.extent.XMin)
                    extent.YMin = min(extent.YMin, geom.extent.YMin)
                    extent.XMax = max(extent.XMax, geom.extent.XMax)
                    extent.YMax = max(extent.YMax, geom.extent.YMax)
            except Exception as e:
                errors.append(f"Błąd zapisu obiektu {d_id}: {e}")
        return extent