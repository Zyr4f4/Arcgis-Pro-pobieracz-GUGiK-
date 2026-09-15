## <b>Toolbox odpowiedzialny za pobieranie danych udostępnianych przez GUGiK w Arcgis Pro.</b>

#### <b>TL/DR:</b>
W środku znajdziesz narzędzia pozwalające na pobranie:
- Obrysów udostępnianych w ramach usługi ULDK (działek/obrębów/gmin/powiatów/województw)
- Obrysów działek/budynków poprzez usługę WFS
- Ortofotomapy
- NMT/NMPT
- Chmur punktów
- Aktualnych paczek BDOT10k
- Archiwalnych paczek BDOT10k
- Punktów adresowych (KINA)
- Osi ulic (KINA)

Pełny opis narzędzi tutaj: [Zawartość przybornika](https://github.com/Zyr4f4/Arcgis-Pro-pobieracz-GUGiK-#zawarto%C5%9B%C4%87-przybornika)
--------------------------------------------------------
### <b> Instalacja </b>

Pobierz kod ze strony:

<img width="410" height="392" alt="image" src="https://github.com/user-attachments/assets/9be618ba-4ecc-42c9-9e98-1ee64ea6af36" />

Wypakuj pobrane archiwum. Aby dodać toolboxa do projektu należy w panelu katalog kliknąć PPM na Tolboxes i wybrać opcję Add Toolbox. 

<img width="723" height="467" alt="image" src="https://github.com/user-attachments/assets/7c0c1eb7-211c-4d40-83bd-2c0eaf2ab434" />

Następnie PPM na samym przyborniku -> Dodaj do nowych projektów. Dzięki temu nie będzie potrzeby dodawać go za każdym razem ponownie.

<img width="404" height="501" alt="image" src="https://github.com/user-attachments/assets/5d2a8b58-4ff6-4218-9f61-23b9c84e9639" />

W przypadku problemów z działaniem wtyczki należy przejść do panelu opcje -> geoprzetwarzanie i odznaczyć opcję Analizuj narzędzia skryptów i modeli z uwagi na zgodność z aplikacją ArcGIS Pro.

<img width="879" height="805" alt="image" src="https://github.com/user-attachments/assets/b3132193-2d7b-4b36-a3cf-f6fd420f9171" />

--------------------------------------------------------
## <b>ZAWARTOSĆ PRZYBORNIKA</b>

### <b>1. Pobierz obrys działek/obrębów/gmin/powiatów/województw (ULDK)</b>
  
Dostępne są trzy metody wyboru obiektów:
- Kliknięcie na mapie
- Wybór wg numeru TERYT
- Kaskadowy wybór z listy (Województwo -> Powiat -> Gmina -> Obręb)

<img width="765" height="521" alt="image" src="https://github.com/user-attachments/assets/5f9cf0ab-33e9-4d03-ba8e-bcc66b07c60f" />

Do prawidłowego działania listy kaskadowej niezbędne jest umieszczenie w tym samym folderze pliku .json ze słownikiem. W razie potrzeby jego aktualizacji, można to zrobić za pomocą dołączonego skryptu.

### <b>2. Pobierz obrys działek/ budynków (WFS)</b>
   Alternatywa dla ULDK. Pozwala na pobieranie danych również za pomocą poligonów/linii.
<img width="768" height="287" alt="image" src="https://github.com/user-attachments/assets/d46f000e-ec2b-44fd-85ac-42dc9cd67996" />

### <b>3. Pobierz ortofotomapę</b>

Pozwala na pobieranie ortofotomapy wg następujących kryteriów:
- standardowa/true ortho
- aktualność (najnowsza/ wybór roku wykonania)
- RGB/CIR/czarno biała
- wielkość piksela
- stopień wypełnienia arkusza

  Zaznaczenie wszystkich lat skorowidzów pozwala na pobranie wszystkich materiałów dla wybranego obszaru.
  Po pobraniu można utworzyć mozaikę z pobranych materiałów i obliczyć jej statystyki.
  
<img width="694" height="569" alt="image" src="https://github.com/user-attachments/assets/4fad2edc-ce62-40cd-a818-8c4b4e5d5644" />


### <b>4. Pobierz NMT/NMPT</b>

Pozwala na pobieranie danych wg następujących kryteriów:
- NMT/NMPT
- wybrany układ wysokościowy
- aktualność
- format danych
- rozdzielczość
- stopień wypełnienia arkusza
  
Opcjonalnie możemy skonwertować dane do .tiff i dodać do okna mapy.

<img width="780" height="519" alt="image" src="https://github.com/user-attachments/assets/0bbca1a3-6e4e-47a5-99bd-1b1a04b7c91a" />

### <b>5. Pobierz chmury punktów LIDAR</b>

Dane pobierane są po wyborze układu wysokościowego. Aby dodać dane do okna mapy konieczne będzie utworzenie LAS Datasetu (Arcgis nie obsługuje plików .laz)

<img width="769" height="387" alt="image" src="https://github.com/user-attachments/assets/9f301447-955a-4a9f-b8fe-5aaa75adfbad" />

### <b>6. Pobierz paczkę danych BDOT10k</b>

Pobiera aktualną paczkę danych BDOT10k dla wybranego powiatu w .shp/.gml. Przy pobieraniu .shp istnieje możliwość automatycznego dodania warstw do geobazy i okna mapy. 

<img width="762" height="397" alt="image" src="https://github.com/user-attachments/assets/cf6b4517-cb82-4483-b025-d1fe3e787cfe" />

### <b>7. Pobierz archiwalną paczkę BDOT10k</b>

Pobiera archiwalną paczkę danych BDOT10k dla wybranego powiatu w .shp/.gml. Przy pobieraniu .shp istnieje możliwość automatycznego dodania warstw do geobazy i okna mapy. 

<img width="713" height="337" alt="image" src="https://github.com/user-attachments/assets/3d033313-097c-4931-b252-6c18ee5a84b1" />

### <b>8. Pobierz osie ulic</b>

Pobiera osie ulic udostępniane poprzez Krajową Integrację Numeracji Adresowej. Do wyboru pobieranie za pomocą istniejącej warstwy\narzędzie odręcznego lub TERYTu.

<img width="715" height="350" alt="image" src="https://github.com/user-attachments/assets/907e42c2-b10c-413b-8916-b37aad861d0a" />

### <b>9. Pobierz punkty adresowe</b>

Pobiera punkty adresowe udostępniane poprzez Krajową Integrację Numeracji Adresowej. Do wyboru pobieranie za pomocą istniejącej warstwy\narzędzie odręcznego lub TERYTu.

<img width="715" height="359" alt="image" src="https://github.com/user-attachments/assets/30bf9b90-7f61-4d98-b0ca-8f4976e02559" />

###### Nota końcowa:

###### Projekt jest hobbystyczny - autor nie jest pracownikiem ESRI/GUGiK. Używasz narzędzia na własną odpowiedzialność.
###### Lubię placki
