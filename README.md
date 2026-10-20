## <b>Toolbox odpowiedzialny za pobieranie danych udostępnianych przez GUGiK w ArcGIS Pro.</b>

#### <b>TL/DR:</b>
Przybornik zawiera zestaw narzędzi podzielonych na 4 kategorie, pozwalających na bezpośrednie pobieranie i przetwarzanie danych udostępnianych przez PZGiK / GUGiK:

- **EGiB**:
  - Obrysów udostępnianych w ramach usługi **ULDK** (działek/obrębów/gmin/powiatów/województw)
  - Obrysów działek i budynków poprzez usługę **WFS EGiB**
- **Adresy**:
  - Punktów adresowych (**KINA**)
  - Osi ulic (**KINA**)
  - **Geokodowania adresów (UUG)** – lokalizacji adresów z tabeli za pomocą Uniwersalnej Usługi Geokodowania
- **Dane fotogrametryczne**:
  - **Ortofotomapy** (standardowa oraz true-ortho, RGB/CIR/czarno-biała, z opcją tworzenia mozaiki)
  - **NMT / NMPT** (układy EVRF2007 i KRON86, konwersja do TIFF, mozaika)
  - **Chmur punktów LIDAR** (LAS/LAZ)
- **Paczki danych**:
  - Aktualnych paczek **BDOT10k** (SHP/GML z automatycznym importem do geobazy)
  - Archiwalnych paczek **BDOT10k** (stany na koniec roku od 2014 r.)
  - Danych z **Rejestru Cen Nieruchomości (RCN)** – transakcji działek, lokali i budynków poprzez WFS

#### Pełny opis poszczególnych narzędzi niżej
--------------------------------------------------------
### <b>Instalacja</b>

Pobierz kod ze strony:

<img width="410" height="392" alt="image" src="https://github.com/user-attachments/assets/9be618ba-4ecc-42c9-9e98-1ee64ea6af36" />

Wypakuj pobrane archiwum. Aby dodać toolboxa do projektu należy w panelu katalog kliknąć PPM na Toolboxes i wybrać opcję Add Toolbox. 

<img width="723" height="467" alt="image" src="https://github.com/user-attachments/assets/7c0c1eb7-211c-4d40-83bd-2c0eaf2ab434" />

Następnie PPM na samym przyborniku -> Dodaj do nowych projektów. Dzięki temu nie będzie potrzeby dodawać go za każdym razem ponownie.

<img width="404" height="501" alt="image" src="https://github.com/user-attachments/assets/5d2a8b58-4ff6-4218-9f61-23b9c84e9639" />

W przypadku problemów z działaniem wtyczki należy przejść do panelu opcje -> geoprzetwarzanie i odznaczyć opcję Analizuj narzędzia skryptów i modeli z uwagi na zgodność z aplikacją ArcGIS Pro.

<img width="879" height="805" alt="image" src="https://github.com/user-attachments/assets/b3132193-2d7b-4b36-a3cf-f6fd420f9171" />

> [!NOTE]
> **Wymagania i pliki pomocnicze:**
> Do prawidłowego działania list rozwijanych (kaskadowy wybór JST) wymagane jest umieszczenie w tym samym folderze co przybornik plików słowników: `slownik_teryt.json` oraz `slownik_bdot.json`. W razie konieczności aktualizacji bazy TERYT można skorzystać z dołączonego skryptu `pobieracz_slownika_JST.py`.

--------------------------------------------------------
## <b>ZAWARTOŚĆ PRZYBORNIKA</b>

### <b>Kategoria: EGiB</b>

### <b>1. Pobierz obrys działek/obrębów/gmin/powiatów/województw (ULDK)</b>
  
Dostępne są trzy metody wyboru obiektów:
- Kliknięcie na mapie (wskazanie punktu ołówkiem)
- Wybór wg numeru TERYT (możliwość podania wielu identyfikatorów po przecinku)
- Kaskadowy wybór z listy (Województwo -> Powiat -> Gmina -> Obręb)

<img width="765" height="521" alt="image" src="https://github.com/user-attachments/assets/5f9cf0ab-33e9-4d03-ba8e-bcc66b07c60f" />

Do prawidłowego działania listy kaskadowej niezbędne jest umieszczenie w tym samym folderze pliku `slownik_teryt.json`. W razie potrzeby jego aktualizacji, można to zrobić za pomocą dołączonego skryptu.

### <b>2. Pobierz obrys działek/budynków (WFS)</b>
Alternatywa dla ULDK oparta o zbiorczą usługę WFS EGiB. Pozwala na pobieranie danych za pomocą poligonów lub linii (rysowanie odręczne lub wskazanie warstwy). 

<img width="768" height="287" alt="image" src="https://github.com/user-attachments/assets/d46f000e-ec2b-44fd-85ac-42dc9cd67996" />

--------------------------------------------------------
### <b>Kategoria: Adresy</b>

### <b>3. Pobierz punkty adresowe (KINA)</b>

Pobiera punkty adresowe udostępniane poprzez Krajową Integrację Numeracji Adresowej (WFS). Do wyboru pobieranie za pomocą istniejącej warstwy / narzędzia odręcznego lub kodu TERYT (powiat / gmina).

<img width="715" height="359" alt="image" src="https://github.com/user-attachments/assets/30bf9b90-7f61-4d98-b0ca-8f4976e02559" />

### <b>4. Pobierz osie ulic (KINA)</b>

Pobiera osie ulic i placów udostępniane poprzez Krajową Integrację Numeracji Adresowej (WFS). Do wyboru pobieranie za pomocą istniejącej warstwy / narzędzia odręcznego lub kodu TERYT.

<img width="715" height="350" alt="image" src="https://github.com/user-attachments/assets/907e42c2-b10c-413b-8916-b37aad861d0a" />

### <b>5. Geokodowanie adresów (UUG)</b>

Narzędzie umożliwia masowe geokodowanie tabelarycznych danych adresowych (tabela, Excel, CSV, warstwa) za pomocą oficjalnej **Uniwersalnej Usługi Geokodowania (UUG)** GUGiK.
- Wymaga wskazania kolumny z miejscowością oraz opcjonalnie kolumn z ulicą i numerem porządkowym (obsługuje zarówno strukturę uliczną, jak i miejscowości bez wydzielonych ulic).
- Opcja wymogu odnalezienia ścisłego numeru porządkowego.
- Tworzy warstwę punktową w układzie współrzędnych PL-1992.

- <img width="494" height="410" alt="image" src="https://github.com/user-attachments/assets/3a623086-c4b5-4055-8c01-4965ea5c4d33" />

--------------------------------------------------------
### <b>Kategoria: Dane fotogrametryczne</b>

### <b>6. Pobierz ortofotomapę</b>

Pozwala na pobieranie ortofotomapy wg następujących kryteriów:
- standardowa / true ortho
- aktualność (najnowsza / wybór roku wykonania)
- RGB / CIR / czarno-biała
- wielkość piksela
- stopień wypełnienia arkusza

Zaznaczenie wszystkich lat skorowidzów pozwala na pobranie wszystkich materiałów dla wybranego obszaru. Po pobraniu można automatycznie utworzyć mozaikę z pobranych materiałów i obliczyć jej statystyki.
  
<img width="694" height="569" alt="image" src="https://github.com/user-attachments/assets/4fad2edc-ce62-40cd-a818-8c4b4e5d5644" />

### <b>7. Pobierz chmury punktów LIDAR</b>

Pobiera chmury punktów z lotniczego skanowania laserowego (LAS/LAZ). Dane pobierane są po wyborze układu wysokościowego (**PL-EVRF2007-NH** lub **PL-KRON86-NH**). Możliwość pobrania wyłącznie najnowszych danych lub wszystkich dostępnych materiałów. Posiada moduł szacowania wielkości pobieranych danych.

<img width="769" height="387" alt="image" src="https://github.com/user-attachments/assets/9f301447-955a-4a9f-b8fe-5aaa75adfbad" />

### <b>8. Pobierz NMT / NMPT</b>

Pozwala na pobieranie danych wysokościowych wg następujących kryteriów:
- NMT / NMPT
- wybrany układ wysokościowy (PL-EVRF2007-NH lub PL-KRON86-NH)
- aktualność
- format danych
- rozdzielczość
- stopień wypełnienia arkusza
  
Opcjonalnie możemy automatycznie skonwertować pobrane dane do formatu .tif (EPSG:2180), zbudować mozaikę i dodać ją do okna mapy.

<img width="780" height="519" alt="image" src="https://github.com/user-attachments/assets/0bbca1a3-6e4e-47a5-99bd-1b1a04b7c91a" />

--------------------------------------------------------
### <b>Kategoria: Paczki danych</b>

### <b>9. Pobierz paczkę danych BDOT10k</b>

Pobiera aktualną paczkę danych BDOT10k dla wybranego powiatu w formacie .shp lub .gml bezpośrednio z zasobów otwartych GUGiK. Przy pobieraniu .shp istnieje możliwość automatycznego wypakowania, zaimportowania klas obiektów do bazy danych i dodania warstw do okna mapy. 

<img width="762" height="397" alt="image" src="https://github.com/user-attachments/assets/cf6b4517-cb82-4483-b025-d1fe3e787cfe" />

### <b>10. Pobierz archiwalną paczkę BDOT10k</b>

Pobiera archiwalną paczkę danych BDOT10k dla wybranego powiatu według stanu na koniec wskazanego roku (od 2014 r.) w formacie .shp lub .gml. Przy pobieraniu .shp istnieje możliwość automatycznego dodania warstw do geobazy i okna mapy. 

<img width="713" height="337" alt="image" src="https://github.com/user-attachments/assets/3d033313-097c-4931-b252-6c18ee5a84b1" />

### <b>11. Pobierz dane z Rejestru Cen Nieruchomości (RCN)</b>

Pobiera dane o transakcjach na rynku nieruchomości ze zbiorczej usługi WFS Rejestru Cen Nieruchomości GUGiK.
- **Dostępne warstwy:** Działki (poligony), Lokale (punkty), Budynki (poligony).
- **Określenie obszaru:** Kaskadowy wybór z bazy TERYT (Województwo -> Powiat -> opcjonalnie Gmina) LUB wskazanie obszaru na mapie (narzędzie odręczne / warstwa).
- **Filtrowanie dat:** Opcjonalny filtr zakresu dat transakcji (od - do) za pomocą kalendarza.
- **Zapis:** Bezpośredni zapis do geobazy z podziałem na atrybuty transakcyjne (cena, data, powierzchnia, udziały).

<img width="495" height="566" alt="image" src="https://github.com/user-attachments/assets/2eadc881-2ac3-42b8-b0be-502ea2f7112f" />

--------------------------------------------------------
###### Nota końcowa:

###### Projekt jest hobbystyczny - autor nie jest pracownikiem ESRI/GUGiK. Używasz narzędzia na własną odpowiedzialność.
###### Lubię placki
