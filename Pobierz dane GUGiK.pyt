# -*- coding: utf-8 -*-
import os
import sys

base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)


class Toolbox(object):
    def __init__(self):
        self.label = "Pobierz dane GUGiK"
        self.alias = "GUGiK_tools"
        self.tools = [
            # EGiB
            PobierzDzialkeULDK,
            PobierzSkorowidzEGIB,
            # KINA
            PobierzPunktyAdresoweKINA,
            PobierzUliceKINA,
            # Dane fotogrametryczne
            PobierzOrtofotomape,
            PobierzChmuryPunktow,
            PobierzNMTNMPT,
            # Paczki danych
            PobierzBDOT10k,
            PobierzArchiwalnyBDOT10k
        ]


# ==============================================================================
# FOLDER: EGiB
# ==============================================================================
class PobierzDzialkeULDK(object):
    def __init__(self):
        self.label = "Pobierz obrys działek/obrębów/gmin/powiatów/ województw (ULDK)"
        self.description = "Pobiera poligony działek, obrębów, gmin, powiatów lub województw za pomocą Usługi Lokalizacji Działek Katastralnych."
        self.category = "EGiB"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.egib.uldk import PobierzDzialkeULDKImpl
            self._impl = PobierzDzialkeULDKImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)


class PobierzSkorowidzEGIB(object):
    def __init__(self):
        self.label = "Pobierz obrys działek/budynków (WFS)"
        self.description = "Pobiera geometrie działek lub budynków ze zbiorczej usługi WFS EGiB dla podanego obszaru z podziałem na mniejsze kafelki."
        self.category = "EGiB"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.egib.wfs_egib import PobierzSkorowidzEGIBImpl
            self._impl = PobierzSkorowidzEGIBImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)


# ==============================================================================
# FOLDER: Krajowa Integracja Numeracji Adresowej (KINA)
# ==============================================================================
class PobierzPunktyAdresoweKINA(object):
    def __init__(self):
        self.label = "Pobierz punkty adresowe"
        self.description = "Pobiera punkty numeracji adresowej z usługi WFS Krajowej Integracji Numeracji Adresowej z automatycznym kafelkowaniem."
        self.category = "Krajowa Integracja Numeracji Adresowej"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.kina.punkty import PobierzPunktyAdresoweKINAImpl
            self._impl = PobierzPunktyAdresoweKINAImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)


class PobierzUliceKINA(object):
    def __init__(self):
        self.label = "Pobierz osie ulic"
        self.description = "Pobiera geometrie osi ulic i placów z usługi WFS Krajowej Integracji Numeracji Adresowej z automatycznym kafelkowaniem."
        self.category = "Krajowa Integracja Numeracji Adresowej"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.kina.ulice import PobierzUliceKINAImpl
            self._impl = PobierzUliceKINAImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)


# ==============================================================================
# FOLDER: Dane fotogrametryczne
# ==============================================================================
class PobierzOrtofotomape(object):
    def __init__(self):
        self.label = "Pobierz ortofotomapę"
        self.description = "Narysuj obszar lub wybierz warstwę, aby pobrać rastry z WFS dla wskazanych lat."
        self.category = "Dane fotogrametryczne"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.fotogrametria.orto import PobierzOrtofotomapeImpl
            self._impl = PobierzOrtofotomapeImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)


class PobierzChmuryPunktow(object):
    def __init__(self):
        self.label = "Pobierz chmury punktów LIDAR"
        self.description = "Narysuj obszar lub wybierz warstwę, aby pobrać chmury punktów z WFS dla wybranych układów wysokościowych."
        self.category = "Dane fotogrametryczne"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.fotogrametria.lidar import PobierzChmuryPunktowImpl
            self._impl = PobierzChmuryPunktowImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)


class PobierzNMTNMPT(object):
    def __init__(self):
        self.label = "Pobierz NMT / NMPT"
        self.description = "Pobiera pliki NMT/NMPT z WFS GUGiK dla wybranego obszaru, nadaje układ 2180 i opcjonalnie tworzy mozaikę."
        self.category = "Dane fotogrametryczne"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.fotogrametria.nmt import PobierzNMTNMPTImpl
            self._impl = PobierzNMTNMPTImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)


# ==============================================================================
# FOLDER: Paczki danych
# ==============================================================================
class PobierzBDOT10k(object):
    def __init__(self):
        self.label = "Pobierz paczkę BDOT10k"
        self.description = "Pobiera paczki (SHP/GML) BDOT10k dla powiatów bezpośrednio z zasobów GUGiK."
        self.category = "Paczki danych"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.paczki.bdot import PobierzBDOT10kImpl
            self._impl = PobierzBDOT10kImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)


class PobierzArchiwalnyBDOT10k(object):
    def __init__(self):
        self.label = "Pobierz archiwalną paczkę BDOT10k"
        self.description = "Pobiera archiwalne paczki danych BDOT10k (SHP/GML) dla powiatów według stanu na koniec wskazanego roku z zasobów GUGiK."
        self.category = "Paczki danych"
        self.canRunInBackground = False
        self._impl = None

    def _get_impl(self):
        if self._impl is None:
            from src.paczki.bdot_archiwum import PobierzArchiwalnyBDOT10kImpl
            self._impl = PobierzArchiwalnyBDOT10kImpl()
        return self._impl

    def getParameterInfo(self):
        return self._get_impl().getParameterInfo()

    def updateParameters(self, parameters):
        return self._get_impl().updateParameters(parameters)

    def execute(self, parameters, messages):
        return self._get_impl().execute(parameters, messages)