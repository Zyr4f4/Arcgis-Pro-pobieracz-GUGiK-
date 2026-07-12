# -*- coding: utf-8 -*-
import urllib.request
import json
import time
import os


def fetch_uldk_dict(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as response:
                text = response.read().decode('utf-8').strip()

            lines = text.split('\n')
            if lines[0].strip() == "0":
                result = {}
                for line in lines[1:]:
                    parts = line.strip().split('|')
                    if len(parts) >= 2:
                        nazwa = parts[0].strip()
                        teryt = parts[1].strip()
                        result[teryt] = nazwa
                return result
            return {}
        except Exception:
            time.sleep(1)
    return {}


def build_teryt_database():
    print("Rozpoczynam budowanie bazy TERYT. Może to potrwać kilka minut...\n")
    database = {"wojewodztwa": {}, "powiaty": {}, "gminy": {}, "obreby": {}}

    url_woj = "https://uldk.gugik.gov.pl/service.php?obiekt=wojewodztwo&wynik=wojewodztwo,teryt"
    wojewodztwa = fetch_uldk_dict(url_woj)
    database["wojewodztwa"] = wojewodztwa

    for woj_teryt, woj_nazwa in wojewodztwa.items():
        url_pow = f"https://uldk.gugik.gov.pl/service.php?obiekt=powiat&wynik=powiat,teryt&teryt={woj_teryt}"
        powiaty = fetch_uldk_dict(url_pow)
        database["powiaty"][woj_teryt] = powiaty

        for pow_teryt, pow_nazwa in powiaty.items():
            print(f"Pobieranie danych dla: powiat {pow_nazwa}...")
            url_gmi = f"https://uldk.gugik.gov.pl/service.php?obiekt=gmina&wynik=gmina,teryt&teryt={pow_teryt}"
            gminy = fetch_uldk_dict(url_gmi)
            database["gminy"][pow_teryt] = gminy

            for gmi_teryt in gminy.keys():
                url_obr = f"https://uldk.gugik.gov.pl/service.php?obiekt=obreb&wynik=nazwa,teryt&teryt={gmi_teryt}"
                obreby = fetch_uldk_dict(url_obr)
                database["obreby"][gmi_teryt] = obreby

    out_file = "slownik_teryt.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(database, f, ensure_ascii=False, indent=2)
    print("\nGOTOWE! Pomyślnie zapisano plik.")


if __name__ == "__main__":
    build_teryt_database()