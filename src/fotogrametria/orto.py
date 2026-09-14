# -*- coding: utf-8 -*-
import arcpy
import os
import re
import csv
import zipfile
import datetime
import urllib.parse
from src.utils import HttpClient, WFSClient


def _cluster_into_rect_chunks(items, max_chunk_size):
    """
    Dzieli listę krotek (cx, cy, raster_path) na zwarte, przestrzenne grupy
    o wielkości <= max_chunk_size, dzieląc rekurencyjnie wzdłuż dłuższego boku.
    """
    if len(items) <= max_chunk_size or max_chunk_size <= 0:
        return [sorted([it[2] for it in items])]

    xs = [it[0] for it in items]
    ys = [it[1] for it in items]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)

    if dx >= dy:
        items_sorted = sorted(items, key=lambda it: (it[0], it[1]))
    else:
        items_sorted = sorted(items, key=lambda it: (it[1], it[0]))

    mid = len(items_sorted) // 2
    left = items_sorted[:mid]
    right = items_sorted[mid:]

    return _cluster_into_rect_chunks(left, max_chunk_size) + _cluster_into_rect_chunks(right, max_chunk_size)


class PobierzOrtofotomapeImpl(object):
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
            displayName="Nazwa bazowa dla tworzonej mozaiki",
            name="mosaic_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_mosaic_name.value = "Mozaika_ortofotmapa_GUGiK"

        param_max_tiles = arcpy.Parameter(
            displayName="Maksymalna liczba rastrów na jedną mozaikę (0 = bez podziału)",
            name="max_rasters_per_mosaic",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input"
        )
        param_max_tiles.value = 100

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
            displayName="Wynikowe rastry/mozaiki (Ukryta)",
            name="out_rasters",
            datatype="DERasterDataset",
            parameterType="Derived",
            direction="Output",
            multiValue=True
        )

        return [
            param_geom, param_rodzaj, param_tylko_aktualna, param_lata,
            param_scena, param_piksel, param_out_folder, param_add_to_map,
            param_mosaic, param_mosaic_name, param_max_tiles, param_out_gdb,
            param_stats, param_overwrite, param_pelny, param_author, param_derived
        ]

    def updateParameters(self, p):
        tylko_aktualna = p[2].value
        p[3].enabled = not tylko_aktualna

        tworz_mozaike = p[8].value
        p[9].enabled = tworz_mozaike
        p[10].enabled = tworz_mozaike
        p[11].enabled = tworz_mozaike
        p[12].enabled = tworz_mozaike

        # Automatyczne podpowiadanie geobazy aktywnego projektu
        if not p[11].value:
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                if aprx.defaultGeodatabase:
                    p[11].value = aprx.defaultGeodatabase
            except Exception:
                if arcpy.env.workspace:
                    p[11].value = arcpy.env.workspace

        if len(p) > 15:
            p[15].enabled = False

    def _parse_feature_scene(self, attrs):
        for key in self.SCENE_CANDIDATE_KEYS:
            val = attrs.get(key)
            if val:
                val_lower = val.lower()
                if "rgb" in val_lower: return "RGB"
                if any(kw in val_lower for kw in ["cir", "ir", "podczerwie"]): return "CIR"
                if any(kw in val_lower for kw in ["czarno", "panchromat", "szaro", "bw", "b/w", "cz-b", "cz/b", "cz_b"]): return "BM"
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

    def _get_tile_geometry(self, attrs, sr_2180):
        """Tworzy obiekt geometrii arkusza z atrybutów współrzędnych skorowidza lub godła."""
        # Wariant 1: Bezpośrednie atrybuty granic arkusza w skorowidzu
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

        # Wariant 2: Parsowanie z poslist/coordinates jeśli istnieją
        for k, v in attrs.items():
            if 'poslist' in k or 'coordinates' in k:
                try:
                    nums = [float(x) for x in re.findall(r"[-+]?\d*\.\d+|\d+", v)]
                    if len(nums) >= 8:
                        # Serwer GUGiK zwraca współrzędne posList w układzie 2180
                        # w kolejności Northing,Easting (Y,X) - zamiana jest
                        # bezwarunkowa (tak samo jak przy budowaniu BBOX w WFSClient),
                        # bo zakresy obu osi w Polsce się nakładają i heurystyka
                        # warunkowa myliła się, gubiąc wszystkie kafle.
                        ys = nums[0::2]
                        xs = nums[1::2]
                        ext = arcpy.Extent(min(xs), min(ys), max(xs), max(ys))
                        ext.spatialReference = sr_2180
                        return ext.polygon
                except Exception:
                    pass
        return None

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
        max_tiles = int(p[10].value) if p[10].value is not None else 0
        out_gdb = p[11].valueAsText
        calc_stats = p[12].value if p[12].value is not None else False
        overwrite = p[13].value if p[13].value is not None else False
        pelny_arkusz = p[14].value if p[14].value is not None else False

        # Zabezpieczenie ścieżki geobazy
        if not out_gdb or not arcpy.Exists(out_gdb):
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                out_gdb = aprx.defaultGeodatabase
            except Exception:
                out_gdb = arcpy.env.workspace

        if not in_features:
            return arcpy.AddError("Proszę wskazać obszar na mapie lub warstwę.")

        base_url = self.WFS_ENDPOINTS.get(rodzaj)
        if not base_url:
            return arcpy.AddError(f"Nieznany rodzaj skorowidza: {rodzaj}")

        sr_2180 = arcpy.SpatialReference(2180)

        # Scalenie wszystkich zaznaczonych geometrii wejściowych w jedną geometrię bazową
        user_geometries = []
        with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as cur:
            for row in cur:
                g = row[0]
                if not g: continue
                if g.spatialReference and g.spatialReference.factoryCode != 2180:
                    g = g.projectAs(sr_2180)
                user_geometries.append(g)

        if not user_geometries:
            return arcpy.AddError("Brak geometrii w wybranej warstwie wejściowej.")

        # Zespolony poligon (union) do precyzyjnego przecinania
        combined_geom = user_geometries[0]
        for next_geom in user_geometries[1:]:
            combined_geom = combined_geom.union(next_geom)

        ext = combined_geom.extent
        bbox = (ext.XMin - 5.0, ext.YMin - 5.0, ext.XMax + 5.0, ext.YMax + 5.0) if combined_geom.type.lower() == "point" else (ext.XMin, ext.YMin, ext.XMax, ext.YMax)

        tiles_to_download = {}
        arcpy.AddMessage("Faza 1/2: Przeszukiwanie wybranych skorowidzów WFS GUGiK...")

        try:
            features = self._find_tiles(base_url, bbox, tylko_aktualna, wybrane_lata, scena, piksel, pelny_arkusz, messages)
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
            return arcpy.AddWarning("Nie znaleziono żadnych rastrów spełniających kryteria wewnątrz wskazanego obrysu.")

        arcpy.AddMessage(f"Zakończono wyszukiwanie. Wyselekcjonowano {len(tiles_to_download)} kafli (odrzucono kafelki spoza obrysu).")
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

        all_downloaded_rasters = sorted(list(all_downloaded_rasters))

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
                arcpy.AddError(f"Brak poprawnej geobazy docelowej dla mozaiki ({out_gdb}).")
            else:
                # Wyznaczenie środków każdego pobranego rastra do klastrowania
                spatial_rasters = []
                for r_path in all_downloaded_rasters:
                    try:
                        desc = arcpy.Describe(r_path)
                        cx = (desc.extent.XMin + desc.extent.XMax) / 2.0
                        cy = (desc.extent.YMin + desc.extent.YMax) / 2.0
                        spatial_rasters.append((cx, cy, r_path))
                    except Exception:
                        spatial_rasters.append((0.0, 0.0, r_path))

                # Podział na zwarte klastry o kształcie zbliżonym do kwadratu/prostokąta
                chunks = _cluster_into_rect_chunks(spatial_rasters, max_tiles) if (max_tiles and max_tiles > 0) else [[r[2] for r in spatial_rasters]]

                created_mosaics = []
                for idx, chunk_rasters in enumerate(chunks, 1):
                    suffix = f"_part{idx}" if len(chunks) > 1 else ""
                    raw_target_name = f"{mosaic_name}{suffix}"
                    md_name = arcpy.ValidateTableName(raw_target_name, out_gdb)
                    md_path = os.path.join(out_gdb, md_name)

                    arcpy.AddMessage(f"Tworzenie mozaiki ({idx}/{len(chunks)}): {md_name} ({len(chunk_rasters)} rastrów w klastrze)...")
                    try:
                        arcpy.management.CreateMosaicDataset(out_gdb, md_name, sr_2180)
                        arcpy.management.AddRastersToMosaicDataset(
                            in_mosaic_dataset=md_path,
                            raster_type="Raster Dataset",
                            input_path=chunk_rasters
                        )
                        if calc_stats:
                            arcpy.management.CalculateStatistics(md_path)
                        created_mosaics.append(md_path)
                    except Exception as e:
                        arcpy.AddError(f"Błąd podczas tworzenia mozaiki {md_name}: {e}")

                if created_mosaics:
                    layers_to_add = created_mosaics

        if len(p) > 16:
            p[16].values = layers_to_add

        if add_to_map and layers_to_add:
            try:
                active_map = arcpy.mp.ArcGISProject("CURRENT").activeMap
                if active_map:
                    for layer_path in layers_to_add:
                        active_map.addDataFromPath(layer_path)
                    arcpy.AddMessage("Dodano wyniki do aktywnej mapy.")
            except Exception as e:
                arcpy.AddWarning(f"Nie udało się dodać warstw do mapy: {e}")