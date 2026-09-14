# -*- coding: utf-8 -*-
import arcpy
import os
import re
import csv
import zipfile
import datetime
import urllib.parse
from src.utils import HttpClient, WFSClient


class PobierzChmuryPunktowImpl(object):
    WFS_ENDPOINTS = {
        "PL-KRON86-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarKRON86/WFS/Skorowidze",
        "PL-EVRF2007-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze",
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
        arcpy.AddMessage("Wyszukiwanie arkuszy chmur punktów...")

        try:
            features = self._find_tiles(base_url, bbox, tylko_aktualna, wybrane_lata, pelny_arkusz, messages)
        except Exception as e:
            return arcpy.AddError(f"Błąd wyszukiwania: {e}")

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
            return arcpy.AddWarning("Nie znaleziono chmur punktów wewnątrz wskazanego obrysu.")

        arcpy.AddMessage(f"Zakończono wyszukiwanie. Wyselekcjonowano {len(tiles_to_download)} arkuszy (odrzucono kafle spoza obrysu). Rozpoczynam pobieranie...")
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