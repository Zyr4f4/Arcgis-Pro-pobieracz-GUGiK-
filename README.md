## <b>Toolbox odpowiedzialny za pobieranie danych udostępnianych przez GUGiK w Arcgis Pro.</b>

#### <b>TL/DR:</b>
W środku znajdziesz narzędzia pozwalające na pobranie:
- Obrysów udostępnianych w ramach usługi ULDK (działek/obrębów/gmin/powiatów/województw)
- Obrysów działek/budynków poprzez usługę WFS
- Ortofotomapy
- NMT/NMPT
- Chmur punktów

--------------------------------------------------------
### <b> Instalacja </b>
Aby dodać toolboxa do projektu należy w panelu katalog kliknąć PPM na Tolboxes i wybrać opcję Add Toolbox. Następnie PPM na samym przyborniku -> Dodaj do nowych projektów. Dzięki temu nie będzie potrzeby dodawać go za każdym razem ponownie.

<img width="767" height="432" alt="image" src="https://github.com/user-attachments/assets/289af5cf-0ee6-4a67-bbbe-36e65665e790" />

--------------------------------------------------------
## <b>ZAWARTOSĆ PRZYBORNIKA</b>

### <b>1. Pobierz geometrię z ULDK</b>
  
Dostępne są trzy metody wyboru obiektów:
- Kliknięcie na mapie
- Wybór wg numeru TERYT
- Kaskadowy wybór z listy (Województwo -> Powiat -> Gmina -> Obręb)

<img width="763" height="521" alt="image" src="https://github.com/user-attachments/assets/28042358-d8f6-4a4e-b3dd-63a012e20c66" />

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

  Dodatkowo po pobraniu można utworzyć mozaikę z pobranych materiałów i obliczyć jej statystyki.
  
<img width="774" height="836" alt="image" src="https://github.com/user-attachments/assets/332556f5-7b96-4293-9e6d-ccba8cd45750" />

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

###### Nota końcowa:

###### Projekt jest hobbystyczny - autor nie jest pracownikiem ESRI/GUGiK. Używasz narzędzia na własną odpowiedzialność.
###### Lubię placki
