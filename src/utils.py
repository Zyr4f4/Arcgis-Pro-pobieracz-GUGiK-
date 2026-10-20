# -*- coding: utf-8 -*-
import os
import re
import time
import json
import random
import shutil
import hashlib
import zipfile
import http.client
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

try:
    import arcpy
except ImportError:
    arcpy = None


class DownloadError(Exception):
    """Błąd pobierania lub rozpakowania pliku."""
    pass


class ToolCancelled(Exception):
    """Przerwanie operacji przez użytkownika."""
    pass


TERYT_DB = None


def get_base_dir():
    try:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        import inspect
        return os.path.dirname(os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe()))))


def load_teryt_db():
    global TERYT_DB
    if TERYT_DB is None:
        try:
            base_dir = get_base_dir()
            db_path = os.path.join(base_dir, "slownik_teryt.json")
            if os.path.exists(db_path):
                with open(db_path, 'r', encoding='utf-8') as f:
                    TERYT_DB = json.load(f)
            else:
                TERYT_DB = False
        except Exception:
            TERYT_DB = False
    return TERYT_DB


BDOT_DB = None


def load_bdot_db():
    global BDOT_DB
    if BDOT_DB is None:
        try:
            base_dir = get_base_dir()
            db_path = os.path.join(base_dir, "slownik_bdot.json")
            if os.path.exists(db_path):
                with open(db_path, 'r', encoding='utf-8') as f:
                    BDOT_DB = json.load(f)
            else:
                BDOT_DB = {}
        except Exception:
            BDOT_DB = {}
    return BDOT_DB


class HttpClient:
    DEFAULT_TIMEOUT = 60
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ArcGISPro-GUGiKTools/1.0"
    }

    @staticmethod
    def is_cancelled():
        if arcpy is not None:
            try:
                return bool(arcpy.env.isCancelled)
            except Exception:
                pass
        return False

    @classmethod
    def fetch_bytes(cls, url, headers=None, timeout=None, retries=4, delay=3.0):
        req_headers = cls.DEFAULT_HEADERS.copy()
        if headers:
            req_headers.update(headers)
        to = timeout or cls.DEFAULT_TIMEOUT

        req = urllib.request.Request(url, headers=req_headers)
        last_exc = None
        for attempt in range(1, retries + 1):
            if cls.is_cancelled():
                raise ToolCancelled("Operacja anulowana przez użytkownika.")
            try:
                with urllib.request.urlopen(req, timeout=to) as resp:
                    return resp.read()
            except Exception as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(delay * (2 ** (attempt - 1)) * random.uniform(0.8, 1.2))
        raise last_exc

    @classmethod
    def fetch_text(cls, url, headers=None, timeout=None, retries=4, delay=3.0, encoding='utf-8'):
        data = cls.fetch_bytes(url, headers=headers, timeout=timeout, retries=retries, delay=delay)
        return data.decode(encoding, errors='ignore').strip()

    @classmethod
    def download_stream(cls, url, out_path, **kwargs):
        """Metoda zgodności wstecznej - deleguje do FileDownloader."""
        downloader = FileDownloader()
        return downloader.download(url, os.path.dirname(out_path), overwrite=True)


class FileDownloader:
    """Odporny downloader plików z GUGiK (pauzy, backoff, wznawianie Range, walidacja, rozpakowywanie)."""

    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ArcGISPro-GUGiK-Downloader"
    TIMEOUT_S = 60
    MAX_RETRIES = 4
    BACKOFF_BASE_S = 4.0
    BACKOFF_MAX_S = 90.0
    RETRY_AFTER_MAX_S = 120.0
    CHUNK = 256 * 1024
    PROGRESS_INTERVAL_S = 5.0

    def __init__(self, pause_between=0.8, cooldown_fail=15.0, max_consecutive_fails=3):
        self.pause_between = pause_between
        self.cooldown_fail = cooldown_fail
        self.max_consecutive_fails = max_consecutive_fails
        self.reset_stats()

    def reset_stats(self):
        self.stat_files = 0
        self.stat_bytes = 0
        self.stat_seconds = 0.0
        self.stat_skipped = 0
        self.stat_skipped_bytes = 0

    @staticmethod
    def is_cancelled():
        return HttpClient.is_cancelled()

    def interruptible_sleep(self, seconds):
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self.is_cancelled():
                return
            time.sleep(min(0.5, end - time.time()))

    @staticmethod
    def fmt_size(n):
        n = float(n)
        if n >= 1024 ** 3: return f"{n / (1024 ** 3):.2f} GB"
        if n >= 1024 ** 2: return f"{n / (1024 ** 2):.1f} MB"
        if n >= 1024: return f"{n / 1024:.0f} KB"
        return f"{int(n)} B"

    def _backoff(self, attempt, retry_after=None):
        wait = min(self.BACKOFF_MAX_S, self.BACKOFF_BASE_S * (2 ** (attempt - 1))) * random.uniform(0.75, 1.25)
        if retry_after:
            wait = max(wait, min(float(retry_after), self.RETRY_AFTER_MAX_S))
        return wait

    @staticmethod
    def _parse_retry_after(value):
        try:
            return float(value) if value is not None and str(value).strip().isdigit() else None
        except Exception:
            return None

    def _fetch_once(self, url, part_path, label=""):
        resume_from = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"

        req = urllib.request.Request(url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=self.TIMEOUT_S)
        except urllib.error.HTTPError as e:
            if e.code == 416 and resume_from:
                os.remove(part_path)
                raise DownloadError("Nie można wznowić pobierania, ponawiam od zera.")
            raise

        with resp:
            status = getattr(resp, "status", None) or resp.getcode()
            length = resp.headers.get("Content-Length")
            length = int(length) if length and str(length).isdigit() else None

            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                if os.path.exists(part_path):
                    os.remove(part_path)
                raise DownloadError("Serwer zwrócił stronę HTML zamiast pliku.")

            if resume_from and status == 206:
                mode = "ab"
                expected = (resume_from + length) if length is not None else None
            else:
                mode = "wb"
                expected = length

            done = resume_from if mode == "ab" else 0
            if arcpy:
                info = f"  Rozmiar pliku: {self.fmt_size(expected)}" if expected else "  Rozmiar pliku: nieznany"
                if done and expected:
                    info += f" (wznowienie od {self.fmt_size(done)})"
                arcpy.AddMessage(info)

            session = 0
            t0 = time.time()
            last_label = t0
            last_log = t0
            with open(part_path, mode) as f:
                while True:
                    chunk = resp.read(self.CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    session += len(chunk)
                    now = time.time()
                    if now - last_label >= 1.0:
                        last_label = now
                        if self.is_cancelled():
                            raise ToolCancelled("Przerwano przez użytkownika.")
                        speed = session / max(now - t0, 1e-6)
                        txt = (
                            f"{label}: {self.fmt_size(done)} / {self.fmt_size(expected)} ({done * 100.0 / expected:.0f}%), {self.fmt_size(speed)}/s"
                            if expected else f"{label}: {self.fmt_size(done)}, {self.fmt_size(speed)}/s")
                        if arcpy:
                            try:
                                arcpy.SetProgressorLabel(txt)
                            except Exception:
                                pass
                        if now - last_log >= self.PROGRESS_INTERVAL_S:
                            last_log = now
                            if arcpy:
                                arcpy.AddMessage("  " + txt)

        size = os.path.getsize(part_path)
        if expected is not None and size != expected:
            raise DownloadError(f"Niepełny plik: {size} z {expected} B")

    def _http_download(self, url, dest_path, label=""):
        part = dest_path + ".part"
        last_err = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            if self.is_cancelled():
                raise ToolCancelled("Przerwano przez użytkownika.")
            retry_after = None
            try:
                self._fetch_once(url, part, label)
                os.replace(part, dest_path)
                return
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code in (400, 404, 410):
                    raise DownloadError(f"HTTP {e.code} - plik niedostępny pod tym adresem")
                retry_after = self._parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
            except (OSError, http.client.HTTPException, DownloadError) as e:
                last_err = str(e) or e.__class__.__name__

            if attempt < self.MAX_RETRIES:
                wait = self._backoff(attempt, retry_after)
                if arcpy:
                    arcpy.AddWarning(
                        f"  próba {attempt}/{self.MAX_RETRIES} nieudana ({last_err}); ponowię za {wait:.0f} s")
                self.interruptible_sleep(wait)

        raise DownloadError(f"{last_err} (po {self.MAX_RETRIES} próbach)")

    def looks_valid(self, path, check_type=None):
        """Weryfikacja integralności pliku."""
        try:
            if os.path.getsize(path) == 0:
                return False
            if zipfile.is_zipfile(path):
                return True
            with open(path, "rb") as fh:
                head = fh.read(16)
        except OSError:
            return False

        if head.lstrip()[:1] == b"<":
            return False

        low = path.lower()
        if check_type == "lidar" or low.endswith((".las", ".laz")):
            return head[:4] == b"LASF"
        if check_type == "raster" or low.endswith((".tif", ".tiff")):
            return head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
        if low.endswith(".jp2"):
            return head[:12] == b"\x00\x00\x00\x0cjP  \r\n\x87\n" or head[:4] == b"\xff\x4f\xff\x51"
        return True

    def download(self, url, out_folder, prefix="", target_filename=None, overwrite=False, check_type=None):
        os.makedirs(out_folder, exist_ok=True)
        if target_filename:
            fname = f"{prefix}_{target_filename}" if prefix else target_filename
        else:
            parsed_name = os.path.basename(urllib.parse.urlparse(url).path)
            if not parsed_name:
                parsed_name = "plik_" + hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
            fname = f"{prefix}_{parsed_name}" if prefix else parsed_name

        out_path = os.path.join(out_folder, fname)
        part_path = out_path + ".part"

        if overwrite and os.path.exists(part_path):
            os.remove(part_path)

        if os.path.exists(out_path) and not overwrite and not self.looks_valid(out_path, check_type):
            if arcpy:
                arcpy.AddWarning(f"  Istniejący plik {fname} jest uszkodzony - pobieram ponownie.")
            os.remove(out_path)

        if overwrite or not os.path.exists(out_path):
            if arcpy:
                arcpy.AddMessage(f"  Rozpoczynanie pobierania: {fname}...")
            t_start = time.time()
            self._http_download(url, out_path, fname)
            if not self.looks_valid(out_path, check_type):
                if os.path.exists(out_path):
                    os.remove(out_path)
                raise DownloadError("Pobrany plik jest uszkodzony lub serwer zwrócił stronę błędu.")

            size = os.path.getsize(out_path)
            elapsed = time.time() - t_start
            self.stat_files += 1
            self.stat_bytes += size
            self.stat_seconds += elapsed
            speed = size / elapsed if elapsed > 0 else 0
            if arcpy:
                arcpy.AddMessage(
                    f"  Pobrano: {fname} ({self.fmt_size(size)} w {elapsed:.1f} s, {self.fmt_size(speed)}/s)")
        else:
            size = os.path.getsize(out_path)
            self.stat_skipped += 1
            self.stat_skipped_bytes += size
            if arcpy:
                arcpy.AddMessage(f"  Pominięto pobieranie (plik już istnieje): {fname} ({self.fmt_size(size)})")

        return out_path

    @staticmethod
    def _is_inside(folder_abs, path_abs):
        try:
            common = os.path.commonpath([folder_abs, path_abs])
        except ValueError:
            return False
        return os.path.normcase(common) == os.path.normcase(folder_abs)

    def extract_files(self, archive_path, out_folder, prefix="", target_exts=(), aux_exts=(), overwrite=False):
        """Strumieniowe rozpakowywanie z archiwum ZIP lub zwrócenie pliku, jeśli już jest docelowy."""
        out_folder_abs = os.path.abspath(out_folder)

        if zipfile.is_zipfile(archive_path):
            extracted = []
            with zipfile.ZipFile(archive_path) as z:
                names = [n for n in z.namelist() if not n.endswith("/")]
                targets = [n for n in names if n.lower().endswith(target_exts)]
                if not targets:
                    raise DownloadError(f"W archiwum brak plików o rozszerzeniach {target_exts}.")

                def _extract_one(name):
                    base = os.path.basename(name)
                    if not base:
                        return None
                    out_name = f"{prefix}_{base}" if prefix else base
                    target_path = os.path.abspath(os.path.join(out_folder, out_name))
                    if not self._is_inside(out_folder_abs, target_path):
                        return None
                    if overwrite or not os.path.exists(target_path):
                        tmp = target_path + ".part"
                        with z.open(name) as src, open(tmp, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                        os.replace(tmp, target_path)
                    return target_path

                for name in targets:
                    p = _extract_one(name)
                    if p:
                        extracted.append(p)

                for name in names:
                    if name.lower().endswith(aux_exts) and name not in targets:
                        _extract_one(name)

            if not extracted:
                raise DownloadError("Po rozpakowaniu archiwum nie uzyskano żadnego pliku.")
            return extracted

        if archive_path.lower().endswith(target_exts):
            return [archive_path]

        raise DownloadError(f"Nieznany format pliku: {os.path.basename(archive_path)}")


class WFSClient:
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
            for child in feat.iter():
                local = child.tag.split("}")[-1].lower()
                if child.text and child.text.strip():
                    attrs[local] = child.text.strip()
            if attrs:
                results.append(attrs)
        return results