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


class PobierzChmuryPunktowImpl(object):
    WFS_ENDPOINTS = {
        "PL-KRON86-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarKRON86/WFS/Skorowidze",
        "PL-EVRF2007-NH": "https://mapy.geoportal.gov.pl/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze",
    }

    VERTICAL_WKID = {
        "PL-EVRF2007-NH": 9651,
        "PL-KRON86-NH": 9650,
    }
    HORIZONTAL_WKID = 2180

    LARGE_TILE_COUNT_WARN = 50
    LARGE_SIZE_WARN_BYTES = 5 * 1024 ** 3
    ESTIMATE_AFTER_FILES = 3
    NON_POLYGON_BUFFER_M = 5.0
    COVERAGE_TOL_FRACTION = 0.005

    POINT_EXT = (".las", ".laz")
    AUX_EXT = (".lax", ".prj", ".xml")

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
        param_lata.filter.list = [str(rok) for rok in range(datetime.datetime.now().year, 2010, -1)]

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
        p[3].enabled = not p[2].value
        p[6].enabled = p[5].value

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

    @staticmethod
    def _tile_vertical_system(attrs):
        for k, v in attrs.items():
            kl = str(k).lower()
            if "wysok" in kl and "uklad" in kl and v:
                vl = str(v).lower()
                if "evrf" in vl: return "PL-EVRF2007-NH"
                if "kron" in vl: return "PL-KRON86-NH"
        return None

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

    def _find_tiles(self, base_url, bbox_2180, aoi_geom, sr_2180, tylko_aktualna, wybrane_lata, pelny_arkusz):
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
            arcpy.AddMessage(f"  Edycja {yr or typename}: {len(picked)} arkuszy, pokrycie: {min(100.0, max(0.0, pct)):.0f}%")

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
            raise RuntimeError("Nie znaleziono arkuszy spełniających kryteria.")
        return result

    def _build_las_sr(self, uklad, sr_2180):
        vcs = self.VERTICAL_WKID.get(uklad)
        if not vcs:
            return sr_2180
        try:
            sr = arcpy.SpatialReference(self.HORIZONTAL_WKID, vcs)
            if getattr(sr, "VCS", None) is not None:
                return sr
        except Exception:
            pass
        return sr_2180

    @staticmethod
    def _unique_file_path(folder, filename):
        stem, ext = os.path.splitext(filename)
        path = os.path.join(folder, filename)
        n = 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(folder, f"{stem}_{n}{ext}")
        return path

    def execute(self, p, messages):
        in_features = p[0].value
        uklad = p[1].valueAsText
        tylko_aktualna = p[2].value
        wybrane_lata = [rok.strip("'\"") for rok in (p[3].valueAsText or "").split(';')] if p[3].valueAsText else []
        out_folder = p[4].valueAsText
        create_lasd = p[5].value or False
        lasd_name = p[6].valueAsText or "Mozaika_LIDAR.lasd"
        if not lasd_name.lower().endswith('.lasd'): lasd_name += '.lasd'
        overwrite = p[7].value or False
        pelny_arkusz = p[8].value or False

        self.downloader.reset_stats()

        if not in_features or not out_folder:
            return arcpy.AddError("Brak wymaganych parametrów wejściowych.")

        base_url = self.WFS_ENDPOINTS.get(uklad)
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

        arcpy.AddMessage(f"Wyszukiwanie arkuszy chmur punktów ({uklad})...")
        try:
            features = self._find_tiles(base_url, bbox, aoi_geom, sr_2180, tylko_aktualna, wybrane_lata, pelny_arkusz)
        except ToolCancelled:
            return arcpy.AddWarning("Przerwano przez użytkownika.")
        except Exception as e:
            return arcpy.AddError(f"Błąd wyszukiwania: {e}")

        tiles_to_download = {}
        for attrs in features:
            url = self._tile_url(attrs)
            if url and url not in tiles_to_download:
                tiles_to_download[url] = attrs

        total = len(tiles_to_download)
        if total == 0:
            return arcpy.AddWarning("Nie znaleziono chmur punktów w wybranym obszarze.")

        arcpy.AddMessage(f"Wyselekcjonowano {total} arkuszy. Rozpoczynam pobieranie...")
        all_point_clouds = set()
        all_metadata = []
        failed, pending = [], []
        consecutive_fails = 0
        size_estimated = False

        run_start = time.time()
        try:
            arcpy.SetProgressor("step", "Pobieranie chmur punktów...", 0, total, 1)
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
                year_str = self._extract_year(attrs)
                local_path = self.downloader.download(url, out_folder, prefix=year_str, overwrite=overwrite, check_type="lidar")
                files = self.downloader.extract_files(local_path, out_folder, prefix=year_str, target_exts=self.POINT_EXT, aux_exts=self.AUX_EXT, overwrite=overwrite)
                all_point_clouds.update(files)

                attrs.update({
                    'lokalna_sciezka': local_path,
                    'pliki_las': ", ".join(files),
                    'uklad_wysokosciowy': uklad,
                    'rozmiar_bajty': os.path.getsize(local_path) if os.path.exists(local_path) else "",
                    'status': "ok",
                    'blad': ""
                })
                all_metadata.append(attrs)
                consecutive_fails = 0

                # Szacowanie łącznego rozmiaru
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
                attrs.update({'status': "blad", 'blad': str(e), 'uklad_wysokosciowy': uklad})
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

        all_point_clouds = sorted(all_point_clouds)
        ok_count = total - len(failed) - len(pending)

        # Raport CSV i URL-e do ponowienia
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if all_metadata:
            csv_path = os.path.join(out_folder, f"metadane_lidar_{timestamp}.csv")
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
            retry_path = os.path.join(out_folder, f"do_pobrania_lidar_{timestamp}.txt")
            with open(retry_path, "w", encoding="utf-8") as f:
                for u in [x[0] for x in failed] + pending:
                    f.write(u + "\n")
            arcpy.AddMessage(f"Zapisano listę nieudanych adresów: {retry_path}")

        arcpy.AddMessage(f"Pobrano {ok_count}/{total} arkuszy w {time.time() - run_start:.0f} s.")

        layers_to_add = []
        if create_lasd and all_point_clouds:
            if failed or pending:
                arcpy.AddWarning("Pominięto tworzenie LAS Dataset z powodu brakujących arkuszy.")
            else:
                lasd_path = self._unique_file_path(out_folder, lasd_name)
                las_sr = self._build_las_sr(uklad, sr_2180)
                try:
                    arcpy.management.CreateLasDataset(input=all_point_clouds, out_las_dataset=lasd_path, spatial_reference=las_sr)
                    layers_to_add = [lasd_path]
                except Exception as e:
                    arcpy.AddError(f"Błąd tworzenia LAS Dataset: {e}")

        if len(p) > 9:
            p[9].values = layers_to_add

        if create_lasd and layers_to_add:
            try:
                active_map = arcpy.mp.ArcGISProject("CURRENT").activeMap
                if active_map:
                    for l in layers_to_add:
                        active_map.addDataFromPath(l)
            except Exception:
                pass