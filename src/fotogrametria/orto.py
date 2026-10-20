# -*- coding: utf-8 -*-
import arcpy
import os
import re
import csv
import time
import random
import shutil
import datetime
from src.utils import WFSClient, FileDownloader, ToolCancelled


def _cluster_into_rect_chunks(items, max_chunk_size):
    """
    Dzieli listę krotek (cx, cy, raster_path) na zwarte grupy
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
    SCENE_RULES = {
        "RGB": ("RGB", r"rgb"),
        "CIR (podczerwień)": ("CIR", r"cir|\bir\b|podczerwie"),
        "Czarno-biała": ("BM", r"czarno|panchromat|szaro|\bbw\b|b/w|cz-b|cz/b|cz_b"),
    }

    LARGE_TILE_COUNT_WARN = 100
    LARGE_SIZE_WARN_BYTES = 5 * 1024 ** 3
    ESTIMATE_AFTER_FILES = 3
    NON_POLYGON_BUFFER_M = 5.0
    COVERAGE_TOL_FRACTION = 0.005

    RASTER_EXT = (".tif", ".tiff", ".jp2", ".ecw")
    AUX_EXT = (".tfw", ".jgw", ".j2w", ".wld", ".prj", ".xml", ".aux", ".ovr", ".tab", ".eww")

    def __init__(self):
        self.wfs_client = WFSClient()
        self.downloader = FileDownloader()

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
        param_lata.filter.list = [str(rok) for rok in range(datetime.datetime.now().year, 1995, -1)]

        param_scena = arcpy.Parameter(
            displayName="Typ barwny sceny",
            name="typ_barwny",
            datatype="GPString",
            parameterType="Optional",
            direction="Input"
        )
        param_scena.filter.list = ["Dowolna"] + list(self.SCENE_RULES.keys())
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
        param_mosaic_name.value = "Mozaika_ortofotomapa_GUGiK"

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
        p[3].enabled = not p[2].value
        p[9].enabled = p[8].value
        p[10].enabled = p[8].value
        p[11].enabled = p[8].value
        p[12].enabled = p[8].value

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
                val_lower = str(val).lower()
                for _label, (code, pattern) in self.SCENE_RULES.items():
                    if re.search(pattern, val_lower):
                        return code
        return "UNKNOWN"

    def _extract_year(self, attrs):
        for k, v in attrs.items():
            if k in ("rok", "rok_nalotu", "rok_zdjecia", "data_zdjecia", "rok_wydania") or "rok" in k:
                match = re.search(r"(19|20)\d{2}", str(v))
                if match:
                    return match.group(0)
        return attrs.get("rok_skorowidza", "")

    @staticmethod
    def _year_key(yr):
        match = re.search(r"(19|20)\d{2}", str(yr)) if yr is not None else None
        return int(match.group(0)) if match else 0

    @staticmethod
    def _tile_url(attrs):
        url_key = next((k for k in attrs if "url_do_pobrania" in k), None)
        return attrs.get(url_key) if url_key and attrs.get(url_key) else None

    def _filter_by_scene(self, features, scena):
        if not scena or scena == "Dowolna":
            return features
        pattern = self.SCENE_RULES.get(scena, (None, None))[1]
        if not pattern:
            return features

        matched = []
        for attrs in features:
            for key in self.SCENE_CANDIDATE_KEYS:
                val = attrs.get(key)
                if val and re.search(pattern, str(val).lower()):
                    matched.append(attrs)
                    break
        return matched

    @staticmethod
    def _area(geom):
        try:
            return float(geom.area) if geom is not None else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _union_all(geoms):
        geoms = list(geoms)
        while len(geoms) > 1:
            nxt = []
            for i in range(0, len(geoms), 2):
                nxt.append(geoms[i].union(geoms[i + 1]) if i + 1 < len(geoms) else geoms[i])
            geoms = nxt
        return geoms[0]

    def _get_tile_geometry(self, attrs, sr_2180):
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

    def _find_tiles(self, base_url, bbox_2180, aoi_geom, sr_2180, tylko_aktualna, wybrane_lata,
                    scena, piksel, pelny_arkusz):
        editions = self.wfs_client.get_all_typenames(base_url)
        if not tylko_aktualna and wybrane_lata:
            editions = [(y, tn) for y, tn in editions if str(y) in wybrane_lata]

        editions = sorted(editions, key=lambda e: self._year_key(e[0]), reverse=True)
        stats = {"candidates": 0, "outside": 0}
        failed_editions = []
        all_found = []
        selected = []
        seen_edition = False
        remaining = aoi_geom
        aoi_area = self._area(aoi_geom)
        min_area = max(1.0, self.COVERAGE_TOL_FRACTION * aoi_area)
        current_bbox = bbox_2180

        for yr, typename in editions:
            if self.downloader.is_cancelled():
                raise ToolCancelled()

            try:
                features = self.wfs_client.get_features(base_url, typename, current_bbox)
            except ToolCancelled:
                raise
            except Exception as e:
                failed_editions.append(str(typename))
                arcpy.AddWarning(f"Ostrzeżenie: Błąd pobierania warstwy {typename}: {e}")
                continue

            if not features:
                continue

            for f in features:
                if yr: f['rok_skorowidza'] = str(yr)

            features = [f for f in features if self._tile_url(f)]
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
                            if match and abs(float(match.group(1).replace(',', '.')) - piksel) < 1e-4:
                                filtered_by_pix.append(f)
                        except ValueError:
                            pass
                features = filtered_by_pix

            if not features:
                continue

            stats["candidates"] += len(features)
            located, no_geom = [], []
            for f in features:
                g = self._get_tile_geometry(f, sr_2180) if aoi_geom is not None else None
                if g is None:
                    no_geom.append(f)
                    continue
                try:
                    if g.disjoint(aoi_geom):
                        stats["outside"] += 1
                        continue
                except Exception:
                    pass
                located.append((f, g))

            if not located and not no_geom:
                continue

            if not tylko_aktualna:
                all_found.extend([a for a, _ in located] + no_geom)
                continue

            if not located:
                if not seen_edition:
                    selected = list(no_geom)
                    break
                continue

            if not seen_edition and no_geom:
                selected.extend(no_geom)
            seen_edition = True

            picked = []
            for attrs_f, g in located:
                try:
                    new_area = self._area(g.intersect(remaining, 4))
                except Exception:
                    new_area = min_area
                if new_area >= min_area:
                    picked.append((attrs_f, g))

            if not picked:
                continue

            selected.extend(a for a, _ in picked)
            try:
                remaining = remaining.difference(self._union_all([g for _, g in picked]))
            except Exception:
                pass

            rem_area = self._area(remaining)
            pct = 100.0 * (1.0 - rem_area / aoi_area) if aoi_area > 0 else 100.0
            arcpy.AddMessage(f"  Edycja {yr or typename}: {len(picked)} kafli, pokrycie: {min(100.0, max(0.0, pct)):.0f}%")

            if rem_area <= min_area:
                break

            try:
                rext = remaining.extent
                current_bbox = (rext.XMin, rext.YMin, rext.XMax, rext.YMax)
            except Exception:
                pass

        if failed_editions:
            arcpy.AddWarning("UWAGA: Nie udało się odpytać edycji: " + ", ".join(failed_editions))

        result = selected if tylko_aktualna else all_found
        if not result:
            raise RuntimeError("Nie znaleziono kafli spełniających podane kryteria.")
        return result

    def _unique_mosaic_base(self, out_gdb, base, n_parts):
        suffixes = [f"_part{i}" for i in range(1, n_parts + 1)] if n_parts > 1 else [""]
        def names_for(candidate):
            return [arcpy.ValidateTableName(f"{candidate}{s}", out_gdb) for s in suffixes]

        n = 1
        while n <= 999:
            candidate = base if n == 1 else f"{base}_{n}"
            if not any(arcpy.Exists(os.path.join(out_gdb, nm)) for nm in names_for(candidate)):
                return candidate
            n += 1
        return f"{base}_{datetime.datetime.now().strftime('%H%M%S')}"

    def execute(self, p, messages):
        in_features = p[0].value
        rodzaj = p[1].valueAsText
        tylko_aktualna = p[2].value
        wybrane_lata = [rok.strip("'\"") for rok in (p[3].valueAsText or "").split(';')] if p[3].valueAsText else []
        scena = p[4].valueAsText
        piksel = float(p[5].value) if p[5].value is not None else None
        out_folder = p[6].valueAsText
        add_to_map = p[7].value if p[7].value is not None else True
        create_mosaic = p[8].value or False
        mosaic_name = p[9].valueAsText or f"Mozaika_{datetime.datetime.now().strftime('%H%M%S')}"
        max_tiles = int(p[10].value) if p[10].value is not None else 0
        out_gdb = p[11].valueAsText
        calc_stats = p[12].value or False
        overwrite = p[13].value or False
        pelny_arkusz = p[14].value or False

        self.downloader.reset_stats()

        if not out_gdb or not arcpy.Exists(out_gdb):
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                out_gdb = aprx.defaultGeodatabase
            except Exception:
                out_gdb = arcpy.env.workspace

        if not in_features or not out_folder:
            return arcpy.AddError("Brak wymaganych parametrów wejściowych.")

        base_url = self.WFS_ENDPOINTS.get(rodzaj)
        sr_2180 = arcpy.SpatialReference(2180)

        user_geometries = []
        with arcpy.da.SearchCursor(in_features, ["SHAPE@"]) as cur:
            for row in cur:
                g = row[0]
                if g:
                    user_geometries.append(g.projectAs(sr_2180) if g.spatialReference.factoryCode != 2180 else g)

        combined_geom = user_geometries[0]
        for next_geom in user_geometries[1:]:
            combined_geom = combined_geom.union(next_geom)

        gtype = combined_geom.type.lower()
        if gtype in ("polygon", "multipolygon"):
            aoi_geom = combined_geom
            ext = combined_geom.extent
            bbox = (ext.XMin, ext.YMin, ext.XMax, ext.YMax)
        else:
            aoi_geom = combined_geom.buffer(self.NON_POLYGON_BUFFER_M)
            ext = aoi_geom.extent
            bbox = (ext.XMin, ext.YMin, ext.XMax, ext.YMax)

        arcpy.AddMessage("Faza 1/2: Przeszukiwanie wybranych skorowidzów WFS GUGiK...")
        try:
            features = self._find_tiles(base_url, bbox, aoi_geom, sr_2180, tylko_aktualna, wybrane_lata,
                                        scena, piksel, pelny_arkusz)
        except ToolCancelled:
            return arcpy.AddWarning("Przerwano przez użytkownika.")
        except Exception as e:
            return arcpy.AddError(f"Błąd wyszukiwania kafli: {e}")

        tiles_to_download = {}
        for attrs in features:
            url = self._tile_url(attrs)
            if url and url not in tiles_to_download:
                tiles_to_download[url] = attrs

        total = len(tiles_to_download)
        if total == 0:
            return arcpy.AddWarning("Nie znaleziono kafli wewnątrz wskazanego obrysu.")

        arcpy.AddMessage(f"Wyselekcjonowano {total} kafli. Faza 2/2: Pobieranie i rozpakowywanie...")
        all_downloaded_rasters = set()
        all_metadata = []
        failed, pending = [], []
        consecutive_fails = 0
        size_estimated = False

        run_start = time.time()
        try:
            arcpy.SetProgressor("step", "Pobieranie ortofotomapy...", 0, total, 1)
        except Exception:
            pass

        items = list(tiles_to_download.items())
        for idx, (url, attrs) in enumerate(items, 1):
            if self.downloader.is_cancelled():
                for u, a in items[idx - 1:]:
                    pending.append(u)
                    a.update({'status': "nie_pobrano", 'blad': "Przerwano"})
                    all_metadata.append(a)
                arcpy.AddWarning("Przerwano przez użytkownika.")
                break

            arcpy.AddMessage(f"[{idx}/{total}] (dotąd: {self.downloader.fmt_size(self.downloader.stat_bytes)})")
            try:
                arcpy.SetProgressorPosition(idx - 1)
            except Exception:
                pass

            files_before = self.downloader.stat_files
            try:
                scene_type = self._parse_feature_scene(attrs)
                year_str = self._extract_year(attrs)
                prefix = "_".join([x for x in [year_str, scene_type] if x])

                local_path = self.downloader.download(url, out_folder, prefix=prefix, overwrite=overwrite, check_type="raster")
                rasters = self.downloader.extract_files(local_path, out_folder, prefix=prefix, target_exts=self.RASTER_EXT, aux_exts=self.AUX_EXT, overwrite=overwrite)
                all_downloaded_rasters.update(rasters)

                attrs.update({
                    'lokalna_sciezka': local_path,
                    'rastry': ", ".join(rasters),
                    'rozmiar_bajty': os.path.getsize(local_path) if os.path.exists(local_path) else "",
                    'status': "ok",
                    'blad': ""
                })
                all_metadata.append(attrs)
                consecutive_fails = 0

                if not size_estimated and self.downloader.stat_files >= self.ESTIMATE_AFTER_FILES and total - idx > 0:
                    size_estimated = True
                    avg = self.downloader.stat_bytes / self.downloader.stat_files
                    est = avg * (total - self.downloader.stat_skipped)
                    try:
                        free = shutil.disk_usage(out_folder).free
                    except OSError:
                        free = None
                    msg = f"Szacunkowy łączny rozmiar: ok. {self.downloader.fmt_size(est)} (wolne na dysku: {self.downloader.fmt_size(free) if free else '?'})"
                    if free and est > 0.9 * free:
                        arcpy.AddWarning(msg + " - UWAGA: Może zabraknąć miejsca na dysku!")
                    else:
                        arcpy.AddMessage(msg)

                if idx < total and self.downloader.stat_files > files_before:
                    self.downloader.interruptible_sleep(self.downloader.pause_between + random.uniform(0, 0.4))

            except ToolCancelled:
                arcpy.AddWarning("Przerwano przez użytkownika.")
                break
            except Exception as e:
                consecutive_fails += 1
                failed.append((url, str(e)))
                attrs.update({'status': "blad", 'blad': str(e)})
                all_metadata.append(attrs)
                arcpy.AddWarning(f"Błąd pobierania {url}: {e}")

                if consecutive_fails >= self.downloader.max_consecutive_fails and idx < total:
                    arcpy.AddWarning(f"{consecutive_fails} nieudane pobrania z rzędu (rate limit). Przerywam pobieranie.")
                    for u, a in items[idx:]:
                        pending.append(u)
                        a.update({'status': "nie_pobrano", 'blad': "Bezpiecznik rate limit"})
                        all_metadata.append(a)
                    break

                if idx < total:
                    self.downloader.interruptible_sleep(self.downloader.cooldown_fail)

        all_downloaded_rasters = sorted(all_downloaded_rasters)
        ok_count = total - len(failed) - len(pending)

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if all_metadata:
            csv_path = os.path.join(out_folder, f"metadane_pobierania_{timestamp}.csv")
            try:
                fieldnames = sorted(set(k for md in all_metadata for k in md.keys()))
                with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
                    writer.writeheader()
                    writer.writerows(all_metadata)
                arcpy.AddMessage(f"Zapisano metadane: {csv_path}")
            except Exception as e:
                arcpy.AddWarning(f"Błąd zapisu CSV: {e}")

        if failed or pending:
            retry_path = os.path.join(out_folder, f"do_pobrania_ortofotomapa_{timestamp}.txt")
            with open(retry_path, "w", encoding="utf-8") as f:
                for u in [x[0] for x in failed] + pending:
                    f.write(u + "\n")
            arcpy.AddMessage(f"Zapisano listę nieudanych adresów: {retry_path}")

        arcpy.AddMessage(f"Pobrano {ok_count}/{total} kafli w {time.time() - run_start:.0f} s.")

        layers_to_add = all_downloaded_rasters
        if create_mosaic and all_downloaded_rasters:
            if failed or pending:
                arcpy.AddWarning("Pominięto tworzenie mozaiki z powodu niekompletnych kafli.")
            elif not out_gdb or not arcpy.Exists(out_gdb):
                arcpy.AddError(f"Brak poprawnej geobazy docelowej dla mozaiki ({out_gdb}).")
            else:
                spatial_rasters = []
                for r_path in all_downloaded_rasters:
                    try:
                        desc = arcpy.Describe(r_path)
                        cx = (desc.extent.XMin + desc.extent.XMax) / 2.0
                        cy = (desc.extent.YMin + desc.extent.YMax) / 2.0
                        spatial_rasters.append((cx, cy, r_path))
                    except Exception:
                        spatial_rasters.append((0.0, 0.0, r_path))

                chunks = _cluster_into_rect_chunks(spatial_rasters, max_tiles) if (max_tiles and max_tiles > 0) else [[r[2] for r in spatial_rasters]]
                base_name = self._unique_mosaic_base(out_gdb, mosaic_name, len(chunks))

                created_mosaics = []
                for idx, chunk_rasters in enumerate(chunks, 1):
                    suffix = f"_part{idx}" if len(chunks) > 1 else ""
                    md_name = arcpy.ValidateTableName(f"{base_name}{suffix}", out_gdb)
                    md_path = os.path.join(out_gdb, md_name)

                    arcpy.AddMessage(f"Tworzenie mozaiki ({idx}/{len(chunks)}): {md_name} ({len(chunk_rasters)} rastrów)...")
                    try:
                        arcpy.management.CreateMosaicDataset(out_gdb, md_name, sr_2180)
                        arcpy.management.AddRastersToMosaicDataset(in_mosaic_dataset=md_path, raster_type="Raster Dataset", input_path=chunk_rasters)
                        if calc_stats:
                            arcpy.management.CalculateStatistics(md_path)
                        created_mosaics.append(md_path)
                    except Exception as e:
                        arcpy.AddError(f"Błąd tworzenia mozaiki {md_name}: {e}")

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