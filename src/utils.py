# -*- coding: utf-8 -*-
import os
import re
import time
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

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