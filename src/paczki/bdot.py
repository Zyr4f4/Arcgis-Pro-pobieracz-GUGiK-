# -*- coding: utf-8 -*-
import arcpy
import os
import re
import zipfile
from src.utils import HttpClient, load_teryt_db, load_bdot_db


class PobierzBDOT10kImpl(object):
    URL_SHP = "https://opendata.geoportal.gov.pl/bdot10k/SHP/{woj_code}/{teryt}_SHP.zip"
    URL_GML = "https://opendata.geoportal.gov.pl/bdot10k/{woj_code}/{teryt}_GML.zip"
    DOWNLOAD_TIMEOUT = 90

    def getParameterInfo(self):
        param_format = arcpy.Parameter(
            displayName="Format paczki",
            name="format_paczki",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_format.filter.list = ["SHP", "GML"]
        param_format.value = "SHP"

        db = load_teryt_db()
        woj_list = []
        if db and isinstance(db, dict) and "wojewodztwa" in db:
            woj_list = sorted([f"{v} ({k})" for k, v in db.get("wojewodztwa", {}).items()])

        param_woj = arcpy.Parameter(
            displayName="Województwo",
            name="wojewodztwo",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_woj.filter.list = woj_list

        param_pow = arcpy.Parameter(
            displayName="Powiat",
            name="powiat",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_pow.filter.list = []

        param_out_dir = arcpy.Parameter(
            displayName="Folder docelowy na pobrane paczki ZIP",
            name="out_dir",
            datatype="DEFolder",
            parameterType="Required",
            direction="Input"
        )

        param_process_all = arcpy.Parameter(
            displayName="Wypakuj ZIP, dodaj warstwy do geobazy i okna aktywnej mapy (tylko SHP)",
            name="process_all",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_process_all.value = True

        param_out_ws = arcpy.Parameter(
            displayName="Geobaza docelowa dla importu (tylko SHP)",
            name="out_ws",
            datatype="DEWorkspace",
            parameterType="Optional",
            direction="Input"
        )
        try:
            if arcpy.env.workspace:
                param_out_ws.value = arcpy.env.workspace
        except Exception:
            pass

        param_overwrite = arcpy.Parameter(
            displayName="Pobierz ponownie / nadpisz w geobazie, jeśli istnieje",
            name="overwrite",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_overwrite.value = False

        param_derived = arcpy.Parameter(
            displayName="Ścieżka do pobranych danych (Ukryta)",
            name="out_path",
            datatype="DEFolder",
            parameterType="Derived",
            direction="Output"
        )

        return [
            param_format, param_woj, param_pow,
            param_out_dir, param_process_all, param_out_ws,
            param_overwrite, param_derived
        ]

    def updateParameters(self, p):
        is_shp = (p[0].valueAsText == "SHP")

        # Dla GML wyłączamy checkbox wypakowania i geobazę
        if not is_shp:
            p[4].value = False
            p[4].enabled = False
            p[5].enabled = False
        else:
            p[4].enabled = True
            p[5].enabled = bool(p[4].value)

        db = load_teryt_db()
        if db:
            woj_val = p[1].valueAsText
            woj_id = woj_val.split('(')[-1].replace(')', '').strip() if woj_val and '(' in woj_val else None

            pow_list = []
            if woj_id and woj_id in db.get("powiaty", {}):
                pow_list = sorted([f"{v} ({k})" for k, v in db["powiaty"][woj_id].items()])

            if p[2].filter.list != pow_list:
                p[2].filter.list = pow_list
                if p[2].valueAsText and p[2].valueAsText not in pow_list:
                    p[2].value = None

    def _sanitize_and_rename_shp(self, folder_path, teryt):
        processed_shp = []
        for root_d, _, files in os.walk(folder_path):
            file_bases = {}
            for f in files:
                dot_idx = f.rfind('.')
                if dot_idx != -1:
                    base = f[:dot_idx]
                    ext = f[dot_idx:]
                    file_bases.setdefault(base, []).append(ext)

            for old_base, extensions in file_bases.items():
                if ".shp" not in [e.lower() for e in extensions]:
                    continue

                if "__" in old_base:
                    class_code = old_base.split("__")[-1].upper()
                else:
                    class_code = old_base.replace(".", "_").upper()

                new_base = f"BDOT_{teryt}_{class_code}"

                for ext in extensions:
                    old_file = os.path.join(root_d, f"{old_base}{ext}")
                    new_file = os.path.join(root_d, f"{new_base}{ext}")
                    if old_file != new_file:
                        if os.path.exists(new_file):
                            os.remove(new_file)
                        os.rename(old_file, new_file)

                renamed_shp = os.path.join(root_d, f"{new_base}.shp")
                if os.path.exists(renamed_shp):
                    processed_shp.append((renamed_shp, class_code))

        return processed_shp

    def execute(self, p, messages):
        overwrite = bool(p[6].value)
        original_overwrite = arcpy.env.overwriteOutput
        arcpy.env.overwriteOutput = overwrite
        try:
            self._execute_impl(p, messages, overwrite)
        finally:
            arcpy.env.overwriteOutput = original_overwrite

    def _execute_impl(self, p, messages, overwrite):
        fmt = (p[0].valueAsText or "SHP").upper()
        pow_val = p[2].valueAsText
        out_dir = p[3].valueAsText
        process_all = bool(p[4].value) and (fmt == "SHP")
        out_ws = p[5].valueAsText

        if not pow_val:
            return arcpy.AddError("Wybierz powiat z listy.")

        teryt = pow_val.split('(')[-1].replace(')', '').strip()[:4]
        pow_nazwa = pow_val.split('(')[0].strip()
        woj_code = teryt[:2]

        bdot_raw = load_bdot_db()
        if isinstance(bdot_raw, dict) and "slownik_klas" in bdot_raw:
            bdot_dict = bdot_raw.get("slownik_klas", {})
            layer_order = bdot_raw.get("kolejnosc_warstw", [])
        else:
            bdot_dict = bdot_raw if isinstance(bdot_raw, dict) else {}
            layer_order = []

        os.makedirs(out_dir, exist_ok=True)

        clean_pow_nazwa = re.sub(r'[\\/*?:"<>| ]', '_', pow_nazwa)
        file_name = f"{teryt}_{clean_pow_nazwa}_{fmt}.zip"
        zip_path = os.path.join(out_dir, file_name)

        arcpy.SetProgressor("step", f"Pobieranie paczki BDOT10k ({fmt})...", 0, 1, 1)

        if os.path.exists(zip_path) and not overwrite and zipfile.is_zipfile(zip_path):
            messages.addMessage(f"Plik istnieje, pomijam pobieranie: {file_name}")
            arcpy.SetProgressorPosition(1)
        else:
            if fmt == "GML":
                url = self.URL_GML.format(woj_code=woj_code, teryt=teryt)
            else:
                url = self.URL_SHP.format(woj_code=woj_code, teryt=teryt)

            messages.addMessage(f"Pobieranie z: {url}")
            download_ok = False
            try:
                HttpClient.download_stream(url, zip_path, messages=messages, timeout=self.DOWNLOAD_TIMEOUT)
                if zipfile.is_zipfile(zip_path):
                    download_ok = True
            except Exception as e:
                messages.addWarningMessage(f"Błąd pobierania: {e}")

            if not download_ok or not zipfile.is_zipfile(zip_path):
                if os.path.exists(zip_path):
                    try:
                        os.remove(zip_path)
                    except Exception:
                        pass
                return arcpy.AddError(f"Nie udało się pobrać paczki dla powiatu {pow_nazwa} ({teryt}) spod adresu: {url}")

            arcpy.SetProgressorPosition(1)

        # GML kończy działanie po pobraniu archiwum
        if fmt == "GML":
            if len(p) > 7:
                p[7].value = out_dir
            arcpy.ResetProgressor()
            messages.addMessage(f"Paczka GML została pobrana: {zip_path}")
            messages.addMessage("Zakończono pomyślnie!")
            return

        # Jeśli dla SHP odznaczono wypakowanie i import
        if not process_all:
            if len(p) > 7:
                p[7].value = out_dir
            arcpy.ResetProgressor()
            messages.addMessage("Zakończono pomyślnie!")
            return

        # Wypakowanie i import SHP do GDB
        if zip_path.lower().endswith(".zip") and zipfile.is_zipfile(zip_path):
            folder_name = f"{teryt}_{clean_pow_nazwa}_BDOT10k_{fmt}"
            target_extract_path = os.path.join(out_dir, folder_name)
            messages.addMessage(f"Wypakowywanie do: {target_extract_path}...")
            os.makedirs(target_extract_path, exist_ok=True)

            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(target_extract_path)

            if out_ws and arcpy.Exists(out_ws):
                renamed_shp_list = self._sanitize_and_rename_shp(target_extract_path, teryt)

                if layer_order:
                    def get_order(item):
                        code = item[1]
                        return layer_order.index(code) if code in layer_order else 9999
                    renamed_shp_list.sort(key=get_order)

                valid_shp_list = [item for item in renamed_shp_list if os.path.getsize(item[0]) > 104]
                total_valid = len(valid_shp_list)

                total_pipeline_steps = 2 + (total_valid * 2)
                current_step = 2

                arcpy.SetProgressor("step", f"Przetwarzanie BDOT10k (0/{total_valid})...", 0, total_pipeline_steps, 1)
                arcpy.SetProgressorPosition(current_step)

                messages.addMessage(f"Importowanie {total_valid} plików SHP do geobazy: {out_ws}...")
                imported_layers = []

                for idx, (shp_full, class_code) in enumerate(valid_shp_list, 1):
                    opis_klasy = bdot_dict.get(class_code, "Obiekt topograficzny")
                    layer_display_name = f"[{class_code}] {opis_klasy}"

                    fc_name = arcpy.ValidateTableName(f"BDOT_{teryt}_{class_code}", out_ws)
                    out_fc_path = os.path.join(out_ws, fc_name)

                    arcpy.SetProgressorLabel(f"Import do GDB [{idx}/{total_valid}]: {fc_name}")

                    if not arcpy.Exists(out_fc_path) or overwrite:
                        try:
                            count = int(arcpy.management.GetCount(shp_full)[0])
                            if count > 0:
                                arcpy.conversion.ExportFeatures(
                                    in_features=shp_full,
                                    out_features=out_fc_path
                                )
                                imported_layers.append((out_fc_path, layer_display_name))
                                messages.addMessage(f"  [{idx}/{total_valid}] Zaimportowano: {fc_name} ({count} obiektów)")
                        except Exception as imp_err:
                            messages.addWarningMessage(f"  [{idx}/{total_valid}] Pominięto warstwę {os.path.basename(shp_full)}: {imp_err}")
                    else:
                        imported_layers.append((out_fc_path, layer_display_name))
                        messages.addMessage(f"  [{idx}/{total_valid}] Istnieje w bazie: {fc_name}")

                    current_step += 1
                    arcpy.SetProgressorPosition(current_step)

                messages.addMessage(f"Zaimportowano łącznie {len(imported_layers)} niepustych warstw do geobazy.")

                if imported_layers:
                    try:
                        aprx = arcpy.mp.ArcGISProject("CURRENT")
                        active_map = aprx.activeMap
                        if active_map:
                            group_name = f"BDOT10k - {pow_nazwa} ({teryt})"
                            group_layer = active_map.createGroupLayer(group_name)
                            group_layer.visible = False

                            for l_idx, (fc_path, disp_name) in enumerate(imported_layers, 1):
                                arcpy.SetProgressorLabel(f"Dodawanie do mapy [{l_idx}/{len(imported_layers)}]: {disp_name}")
                                added_lyr = active_map.addDataFromPath(fc_path)
                                added_lyr.name = disp_name
                                added_lyr.visible = False
                                active_map.addLayerToGroup(group_layer, added_lyr, "TOP")
                                active_map.removeLayer(added_lyr)

                                current_step += 1
                                arcpy.SetProgressorPosition(current_step)

                            for lyr in group_layer.listLayers():
                                lyr.visible = True
                            group_layer.visible = True

                            messages.addMessage(f"Utworzono grupę '{group_name}' i włączono widoczność wszystkich warstw.")
                    except Exception as map_err:
                        messages.addWarningMessage(f"Nie udało się dodać warstw do widoku mapy: {map_err}")

                arcpy.SetProgressorPosition(total_pipeline_steps)

        if len(p) > 7:
            p[7].value = out_dir

        arcpy.ResetProgressor()
        messages.addMessage("Zakończono pomyślnie!")