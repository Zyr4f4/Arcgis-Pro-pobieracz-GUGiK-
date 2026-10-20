# -*- coding: utf-8 -*-
import arcpy
import os
import re
import json
import urllib.parse
from src.utils import HttpClient


class GeokodowanieAdresuUUGImpl(object):
    """Narzędzie do wyszukiwania i lokalizacji adresów za pomocą Uniwersalnej Usługi Geokodowania (UUG) GUGiK.

    Obsługuje zarówno adresy ze strukturą ulic (np. "Wrocław, Mielecka 3"),
    jak i miejscowości bez wydzielonych ulic (np. "Królewskie Brzeziny 13").

    Endpoint usługi: https://services.gugik.gov.pl/uug?request=GetAddress&address={address}
    """

    UUG_BASE_URL = "https://services.gugik.gov.pl/uug"

    FIELDS_DEFINITION = [
        ["ADRES_WEJSCIOWY", "TEXT", "Adres wejściowy", 200],
        ["MIEJSCOWOSC", "TEXT", "Miejscowość", 100],
        ["CZESC_MIASTA", "TEXT", "Część miasta", 100],
        ["ULICA", "TEXT", "Ulica", 150],
        ["NUMER", "TEXT", "Numer porządkowy", 20],
        ["KOD_POCZTOWY", "TEXT", "Kod pocztowy", 10],
        ["WOJEWODZTWO", "TEXT", "Województwo", 60],
        ["POWIAT", "TEXT", "Powiat", 60],
        ["GMINA", "TEXT", "Gmina", 60],
        ["TERYT", "TEXT", "Kod TERYT gminy", 10],
        ["SIMC", "TEXT", "Kod SIMC miejscowości", 10],
        ["ULIC", "TEXT", "Kod ULIC ulicy", 10],
        ["DOKLADNOSC", "DOUBLE", "Dokładność dopasowania", None],
        ["DOKLADNOSC_MIASTO", "DOUBLE", "Dokładność – miejscowość", None],
        ["DOKLADNOSC_ULICA", "DOUBLE", "Dokładność – ulica", None],
        ["X_2180", "DOUBLE", "Współrzędna X (PL-1992)", None],
        ["Y_2180", "DOUBLE", "Współrzędna Y (PL-1992)", None],
    ]

    def getParameterInfo(self):
        param_table = arcpy.Parameter(
            displayName="Warstwa lub tabela z adresami",
            name="input_table",
            datatype=["DETable", "GPTableView", "GPFeatureLayer", "DEFeatureClass"],
            parameterType="Required",
            direction="Input"
        )

        param_field_city = arcpy.Parameter(
            displayName="Kolumna z miejscowością",
            name="city_field",
            datatype="Field",
            parameterType="Required",
            direction="Input"
        )
        param_field_city.parameterDependencies = [param_table.name]

        param_field_street = arcpy.Parameter(
            displayName="Kolumna z ulicą (opcjonalnie)",
            name="street_field",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_field_street.parameterDependencies = [param_table.name]

        param_field_number = arcpy.Parameter(
            displayName="Kolumna z numerem porządkowym (opcjonalnie)",
            name="number_field",
            datatype="Field",
            parameterType="Optional",
            direction="Input"
        )
        param_field_number.parameterDependencies = [param_table.name]

        param_exact = arcpy.Parameter(
            displayName="Wymagaj dokładnego numeru porządkowego",
            name="exact_number",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        param_exact.value = True

        param_out_ws = arcpy.Parameter(
            displayName="Geobaza docelowa",
            name="out_ws",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            if aprx.defaultGeodatabase:
                param_out_ws.value = aprx.defaultGeodatabase
        except Exception:
            if arcpy.env.workspace:
                param_out_ws.value = arcpy.env.workspace

        param_out_name = arcpy.Parameter(
            displayName="Nazwa warstwy wynikowej",
            name="out_name",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )
        param_out_name.value = "Geokodowane_Adresy_UUG"

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
            param_table,        # 0
            param_field_city,   # 1 – miejscowość
            param_field_street, # 2 – ulica
            param_field_number, # 3 – numer
            param_exact,        # 4
            param_out_ws,       # 5
            param_out_name,     # 6
            param_author,       # 7
            param_derived       # 8
        ]

    def updateParameters(self, p):
        if not p:
            return

        # Ukryj pole informacyjne autora
        if len(p) > 7:
            p[7].enabled = False

        if not p[5].value:
            try:
                aprx = arcpy.mp.ArcGISProject("CURRENT")
                if aprx.defaultGeodatabase:
                    p[5].value = aprx.defaultGeodatabase
            except Exception:
                if arcpy.env.workspace:
                    p[5].value = arcpy.env.workspace

    @staticmethod
    def _clean_val(val):
        if val is None:
            return ""
        s = str(val).strip()
        if s.lower() in ("<null>", "none", "null"):
            return ""
        return s

    @staticmethod
    def _safe_float(val):
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_jednostka(raw_jednostka):
        """Rozbija jednostkę administracyjną (np. '{Polska,dolnośląskie,Wrocław,Wrocław}') na (wojewodztwo, powiat, gmina)."""
        if not raw_jednostka:
            return None, None, None
        clean = str(raw_jednostka).strip().strip("{}")
        parts = [p.strip() for p in clean.split(",") if p.strip()]
        if not parts:
            return None, None, None
        if parts[0].lower() in ("polska", "poland"):
            parts = parts[1:]
        woj = parts[0] if len(parts) > 0 else None
        powiat = parts[1] if len(parts) > 1 else None
        gmina = parts[2] if len(parts) > 2 else None
        return woj, powiat, gmina

    def _build_address_string(self, city, street, number):
        """Buduje pełną frazę adresu z osobnych pól, obsługując brak ulicy.

        Przykłady:
          - miejscowość + ulica + numer  → "Wrocław, Mielecka 3"
          - miejscowość + numer (brak ulicy) → "Królewskie Brzeziny 13"
          - miejscowość + ulica (brak numeru) → "Wrocław, Mielecka"
          - sama miejscowość                 → "Wrocław"
        """
        city = self._clean_val(city)
        street = self._clean_val(street)
        number = self._clean_val(number)

        if not city:
            return None

        if street:
            street_with_num = f"{street} {number}" if number else street
            return f"{city}, {street_with_num}"
        elif number:
            return f"{city} {number}"
        else:
            return city

    def _query_uug(self, address, exact_number=1):
        """Wysyła zapytanie do usługi UUG (GetAddress) i zwraca listę dopasowań."""
        params = {
            "request": "GetAddress",
            "address": address,
        }
        if exact_number is not None:
            params["exact_number"] = str(int(exact_number))

        url = f"{self.UUG_BASE_URL}?{urllib.parse.urlencode(params)}"
        resp_text = HttpClient.fetch_text(url, timeout=30)

        try:
            data = json.loads(resp_text)
        except json.JSONDecodeError:
            return []

        results = []
        raw_results = data.get("results", {})
        if isinstance(raw_results, dict):
            for key in sorted(raw_results.keys(), key=lambda k: int(k) if k.isdigit() else k):
                item = raw_results[key]
                if isinstance(item, dict):
                    results.append(item)
        return results

    def execute(self, p, messages):
        input_table = p[0].value
        city_field = p[1].valueAsText
        street_field = p[2].valueAsText
        number_field = p[3].valueAsText
        exact_number = bool(p[4].value) if p[4].value is not None else True

        out_ws = p[5].valueAsText
        raw_out_name = p[6].valueAsText

        safe_name = re.sub(r'[^\w]', '_', raw_out_name)
        if safe_name and safe_name[0].isdigit():
            safe_name = "L_" + safe_name
        out_name = arcpy.ValidateTableName(safe_name, out_ws)
        out_fc = os.path.join(out_ws, out_name)

        sr_2180 = arcpy.SpatialReference(2180)
        errors = []

        # --- Weryfikacja danych wejściowych i odczyt adresów ---
        if not input_table or not city_field:
            arcpy.AddError("Wskaż tabelę wejściową oraz kolumnę zawierającą nazwę miejscowości.")
            return

        read_fields = [city_field]
        street_idx = None
        number_idx = None

        if street_field:
            street_idx = len(read_fields)
            read_fields.append(street_field)
        if number_field:
            number_idx = len(read_fields)
            read_fields.append(number_field)

        addresses = []
        with arcpy.da.SearchCursor(input_table, read_fields) as cur:
            for row in cur:
                city_val = str(row[0]).strip() if row[0] else ""
                street_val = str(row[street_idx]).strip() if street_idx is not None and row[street_idx] else ""
                number_val = str(row[number_idx]).strip() if number_idx is not None and row[number_idx] else ""

                addr = self._build_address_string(city_val, street_val, number_val)
                if addr:
                    addresses.append(addr)

        if not addresses:
            arcpy.AddError("Wskazana tabela nie zawiera żadnych poprawnych adresów do przetworzenia.")
            return

        total = len(addresses)
        arcpy.AddMessage(f"Wczytano adresów do geokodowania: {total}.")

        # --- Utworzenie warstwy wynikowej ---
        if not arcpy.Exists(out_fc):
            arcpy.CreateFeatureclass_management(out_ws, out_name, "POINT", spatial_reference=sr_2180)
            arcpy.management.AddFields(out_fc, self.FIELDS_DEFINITION)

        field_names = [f[0] for f in self.FIELDS_DEFINITION]
        insert_fields = ["SHAPE@"] + field_names

        arcpy.SetProgressor("step", "Lokalizowanie adresów w usłudze UUG GUGiK...", 0, total, 1)

        inserted_count = 0
        skipped_count = 0

        with arcpy.da.InsertCursor(out_fc, insert_fields) as cursor:
            for idx, addr in enumerate(addresses, start=1):
                if HttpClient.is_cancelled():
                    arcpy.AddWarning("Przetwarzanie zostało przerwane przez użytkownika.")
                    break

                arcpy.SetProgressorLabel(f"Lokalizowanie adresu [{idx}/{total}]: {addr[:60]}...")

                try:
                    results = self._query_uug(addr, exact_number=int(exact_number))
                except Exception as e:
                    errors.append(f"Błąd zapytania do usługi dla adresu '{addr}': {e}")
                    skipped_count += 1
                    arcpy.SetProgressorPosition(idx)
                    continue

                if not results:
                    arcpy.AddWarning(f"  Nie odnaleziono adresu w bazie: {addr}")
                    skipped_count += 1
                    arcpy.SetProgressorPosition(idx)
                    continue

                # Pobranie najlepiej dopasowanego wyniku (pierwszy element)
                item = results[0]

                x_str = item.get("x")
                y_str = item.get("y")
                if not x_str or not y_str:
                    arcpy.AddWarning(f"  Brak współrzędnych w odpowiedzi usługi dla adresu: {addr}")
                    skipped_count += 1
                    arcpy.SetProgressorPosition(idx)
                    continue

                try:
                    x = float(x_str)
                    y = float(y_str)
                except (ValueError, TypeError):
                    arcpy.AddWarning(f"  Niepoprawny format współrzędnych geograficznych dla adresu: {addr}")
                    skipped_count += 1
                    arcpy.SetProgressorPosition(idx)
                    continue

                point = arcpy.PointGeometry(arcpy.Point(x, y), sr_2180)
                woj, pow_val, gmi = self._parse_jednostka(item.get("jednostka"))

                row_vals = [
                    point,
                    addr[:200],                                      # ADRES_WEJSCIOWY
                    (item.get("city") or "")[:100] or None,          # MIEJSCOWOSC
                    (item.get("citypart") or "")[:100] or None,      # CZESC_MIASTA
                    (item.get("street") or "")[:150] or None,        # ULICA
                    (item.get("number") or "")[:20] or None,         # NUMER
                    (item.get("code") or "")[:10] or None,           # KOD_POCZTOWY
                    woj[:60] if woj else None,                        # WOJEWODZTWO
                    pow_val[:60] if pow_val else None,                # POWIAT
                    gmi[:60] if gmi else None,                        # GMINA
                    (item.get("teryt") or "")[:10] or None,          # TERYT
                    (item.get("simc") or "")[:10] or None,           # SIMC
                    (item.get("ulic") or "")[:10] or None,           # ULIC
                    self._safe_float(item.get("accuracy")),          # DOKLADNOSC
                    self._safe_float(item.get("city_accuracy")),     # DOKLADNOSC_MIASTO
                    self._safe_float(item.get("street_accuracy")),   # DOKLADNOSC_ULICA
                    x,                                               # X_2180
                    y,                                               # Y_2180
                ]

                try:
                    cursor.insertRow(row_vals)
                    inserted_count += 1
                except Exception as ins_err:
                    errors.append(f"Nie udało się zapisać punktu dla adresu '{addr}': {ins_err}")

                arcpy.SetProgressorPosition(idx)

        arcpy.AddMessage(f"Pomyślnie zlokalizowano i zapisano punktów: {inserted_count}.")
        if skipped_count:
            arcpy.AddWarning(f"Nie odnaleziono lokalizacji dla {skipped_count} adresów.")

        if len(p) > 8:
            p[8].value = out_fc

        # Dodanie warstwy do aktywnego widoku mapy
        arcpy.SetProgressorLabel("Dodawanie utworzonej warstwy do aktywnej mapy...")
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            active_map = aprx.activeMap
            if active_map:
                layers = active_map.listLayers(out_name)
                if not layers:
                    active_map.addDataFromPath(out_fc)
        except Exception as e:
            errors.append(f"Nie udało się dodać warstwy do widoku mapy: {e}")

        arcpy.ResetProgressor()

        if errors:
            arcpy.AddWarning(f"--- Podsumowanie ostrzeżeń i błędów ({len(errors)}) ---")
            for err in errors:
                arcpy.AddWarning(f"  • {err}")

        arcpy.AddMessage("Przetwarzanie zakończone pomyślnie!")
