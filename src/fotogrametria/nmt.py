# -*- coding: utf-8 -*-
import arcpy
import os
import re
import csv
import zipfile
import datetime
import urllib.parse
from src.utils import HttpClient, WFSClient


class PobierzNMTNMPTImpl(object):
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

    def _get_tile_geometry(self, attrs, sr_2180):
        """Tworzy obiekt geometrii arkusza z atrybutów współrzędnych skorowidza lub posList."""
        try:
            x_min = float(attrs.get('minx') or attrs.get('x_min') or attrs.get('xmin'))
            y_min = float(attrs.get('miny') or attrs.get('y_min') or attrs.get('ymin'))
            x_max = float(attrs.get('maxx') or attrs.get('x_max') or attrs.get('xmax'))
            y_max = float(attrs.get('maxy') or attrs.get('y_max') or attrs.get('ymax'))
            ext = arcpy.Extent(x_min, y_min, x_max, y_max)
            ext.spatialReference = sr_2180
            return ext.polygon
        except Exception:
            pass

        for k, v in attrs.items():
            if 'poslist' in k or 'coordinates' in k:
                try:
                    nums = [float(x) for x in re.findall(r"[-+]?\d*\.\d+|\d+", v)]
                    if len(nums) >= 8:
                        ys = nums[0::2]
                        xs = nums[1::2]
                        ext = arcpy.Extent(min(xs), min(ys), max(xs), max(ys))
                        ext.spatialReference = sr_2180
                        return ext.polygon
                except Exception:
                    pass
        return None

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

        # Scalenie geometrii wejściowych
        user_geometries = []
        with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as cur:
            for row in cur:
                g = row[0]
                if not g:
                    continue
                if g.spatialReference and g.spatialReference.factoryCode != 2180:
                    g = g.projectAs(sr_2180)
                user_geometries.append(g)

        if not user_geometries:
            return arcpy.AddError("Brak geometrii w wybranej warstwie wejściowej.")

        combined_geom = user_geometries[0]
        for next_geom in user_geometries[1:]:
            combined_geom = combined_geom.union(next_geom)

        ext = combined_geom.extent
        bbox = (ext.XMin - 5.0, ext.YMin - 5.0, ext.XMax + 5.0, ext.YMax + 5.0) if combined_geom.type.lower() == "point" else (ext.XMin, ext.YMin, ext.XMax, ext.YMax)

        tiles_to_download = {}
        arcpy.AddMessage(f"Wyszukiwanie arkuszy {key_typ}...")

        try:
            features = self._find_tiles(base_url, bbox, tylko_aktualna, wybrane_lata, format_danych, rozdzielczosc, pelny_arkusz, messages)
        except Exception as e:
            return arcpy.AddError(f"Błąd wyszukiwania kafli: {e}")

        for attrs in features:
            url_key = next((k for k in attrs if "url_do_pobrania" in k), None)
            if not url_key or not attrs[url_key]:
                continue

            # Dokładne odrzucanie kafli spoza poligonu
            if combined_geom.type.lower() in ("polygon", "multipolygon"):
                t_geom = self._get_tile_geometry(attrs, sr_2180)
                if t_geom:
                    try:
                        if t_geom.disjoint(combined_geom):
                            continue
                    except Exception:
                        pass

            download_url = attrs[url_key]
            if download_url not in tiles_to_download:
                tiles_to_download[download_url] = attrs

        if not tiles_to_download:
            return arcpy.AddWarning(f"Nie znaleziono plików {key_typ} wewnątrz wskazanego obrysu.")

        arcpy.AddMessage(f"Znaleziono {len(tiles_to_download)} unikalnych plików {key_typ} (odrzucono kafle spoza obrysu). Rozpoczynanie pobierania...")
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