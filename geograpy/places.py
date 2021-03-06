import os
import csv
import re
import pycountry
import sqlite3
from unidecode import unidecode
from .utils import remove_non_ascii, fuzzy_match
from collections import Counter

"""
Takes a list of place names and works place designation (country, region, etc) 
and relationships between places (city is inside region is inside country, etc)
"""


class PlaceContext(object):
    def __init__(self, place_names, db_file=None):
        db_file = db_file or os.path.dirname(
            os.path.realpath(__file__)) + "/data/locs.db"
        self.conn = sqlite3.connect(db_file, timeout=10, check_same_thread=False)
        self.conn.text_factory = lambda x: str(x, 'utf-8', 'ignore')
        self.places = place_names

    def db_has_data(self):
        cur = self.conn.cursor()

        cur.execute("SELECT Count(*) FROM sqlite_master WHERE name='cities';")
        data = cur.fetchone()[0]

        if data > 0:
            cur.execute("SELECT Count(*) FROM cities")
            data = cur.fetchone()[0]
            return data > 0

        return False

    def correct_country_mispelling(self, s):
        cur_dir = os.path.dirname(os.path.realpath(__file__))
        with open(cur_dir + "/data/ISO3166ErrorDictionary.csv", "rt") as info:
            reader = csv.reader(info)
            for row in reader:
                if s in remove_non_ascii(row[0]):
                    return row[2]

        return s

    def get_location(self, l):
        cur = self.conn.cursor()
        new_data = {}
        rows = []
        geos = {
            "country": ['country_iso_code_key="%s"', 'secondary_iso_code_key="%s"', 'country_name_key="%s"'],
            "country_region": ['subdivision_1_iso_code_key="%s"', 'subdivision_1_name_key="%s"',
                               'subdivision_2_iso_code like "%s"', 'subdivision_2_name  like "%s"'],
            "state": ['subdivision_1_iso_code_key="%s"', 'subdivision_1_name_key="%s"',
                      'subdivision_2_iso_code like "%s"', 'subdivision_2_name  like "%s"'],
            "city": ['city_name_key like "%s"', 'city_name_key like "%s"', 'city_name_v2_key like "%s"',
                     'city_name_v2_key like "%s"']
        }
        try:
            query = self.get_query(l.copy(), geos)
            cur.execute(query)
            rows = cur.fetchall()

            if len(rows) == 0:
                geos["city"] = ['city_name_key like "%s%%"', 'city_name_v2_key like "%%%s"']
                query = self.get_query(l.copy(), geos)
                cur.execute(query)
                rows = cur.fetchall()

        except sqlite3.OperationalError:
            print("database locked")

        if len(rows) > 1:
            return None

        for row in rows:
            new_data['country_code'] = row[0]
            new_data['country_name'] = row[1]
            new_data['region_code'] = row[2]
            new_data['region_name'] = row[3]

            try:
                new_data['city'] = row[4]
            except IndexError:
                pass
            break

        if len(rows) == 1:
            return new_data
        else:
            return None

    def get_query(self, l, geos):
        where = ''
        number_of_filters = 0
        columns = [
            'lower(country_iso_code) as country_iso_code',
            'lower(country_name) as country_name',
            'lower(subdivision_1_iso_code) as region_code',
            'lower(subdivision_1_name) as region_name'
        ]

        if 'city' in l:
            columns.append('lower(city_name) as city')
        elif 'city' not in l and 'city_district' in l:
            geos['city_district'] = ['city_name_key like "%s%%"', 'city_name_key like "%%%s"',
                                     'city_name_v2_key like "%%%s"', 'city_name_v2_key like "%s%%"']
            columns.append('lower(city_name) as city')
        elif 'city' not in l and 'city_district' not in l and 'suburb' in l:
            geos['suburb'] = ['city_name_key like "%s%%"', 'city_name_key like "%%%s"', 'city_name_v2_key like "%%%s"',
                              'city_name_v2_key like "%s%%"']
            columns.append('lower(city_name) as city')

        for key, values in geos.items():
            if key in l:
                number_of_filters += 1
                geo_value = re.sub("[- .,']+", "", l[key])
                where += ' and ('
                str_or = ''
                for value in values:
                    value = value.strip()
                    where += str_or + (value % unidecode(geo_value))
                    str_or = ' OR '
                l.pop(key, None)
                where += ' )'

        select_columns = ', '.join(columns)

        return "SELECT DISTINCT " + select_columns + " FROM cities WHERE 1" + where

    def is_a_country(self, s):
        s = self.correct_country_mispelling(s)
        try:
            pycountry.countries.get(name=s)
            return True
        except KeyError:
            return False

    def places_by_name(self, place_name, column_name):
        cur = self.conn.cursor()
        cur.execute('SELECT * FROM cities WHERE ' + column_name + ' like "' + place_name + '"')
        rows = cur.fetchall()

        if len(rows) > 0:
            return rows

        return None

    def cities_for_name(self, city_name):
        return self.places_by_name(city_name, 'city_name')

    def regions_for_name(self, region_name):
        return self.places_by_name(region_name, 'subdivision_1_name')

    def get_region_names(self, country_name):
        country_name = self.correct_country_mispelling(country_name)
        try:
            obj = pycountry.countries.get(name=country_name)
            regions = pycountry.subdivisions.get(country_code=obj.alpha2)
        except:
            regions = []

        return [r.name for r in regions]

    def set_countries(self):
        countries = [
            self.correct_country_mispelling(place) for place in self.places
            if self.is_a_country(place)
        ]

        self.country_mentions = Counter(countries).most_common()
        self.countries = list(set(countries))

    def set_regions(self):
        regions = []
        self.country_regions = {}
        region_names = {}

        if not self.countries:
            self.set_countries()

        def region_match(place_name, region_name):
            return fuzzy_match(
                remove_non_ascii(place_name), remove_non_ascii(region_name))

        def is_region(place_name, region_names):
            return filter(lambda rn: region_match(place_name, rn),
                          region_names)

        for country in self.countries:
            region_names = self.get_region_names(country)
            matched_regions = [
                p for p in self.places if is_region(p, region_names)
            ]

            regions += matched_regions
            self.country_regions[country] = list(set(matched_regions))

        self.region_mentions = Counter(regions).most_common()
        self.regions = list(set(regions))

    def set_cities(self):
        self.cities = []
        self.country_cities = {}
        self.address_strings = []

        if not self.countries:
            self.set_countries()

        if not self.regions:
            self.set_regions()

        cur = self.conn.cursor()
        cur.execute("SELECT * FROM cities WHERE city_name IN (" + ",".join(
            "?" * len(self.places)) + ")", self.places)
        rows = cur.fetchall()

        for row in rows:
            country = None

            try:
                country = pycountry.countries.get(alpha_2=row[3])
                country_name = country.name
            except KeyError:
                country_name = row[4]

            city_name = row[7]
            region_name = row[6]

            if city_name not in self.cities:
                self.cities.append(city_name)

            if country_name not in self.countries:
                self.countries.append(country_name)
                self.country_mentions.append((country_name, 1))

            if country and country_name not in self.country_cities:
                # TODO: determine what to do if country _is_ None here - is _name safe?
                self.country_cities[country.name] = []

            if city_name not in self.country_cities[country_name]:
                self.country_cities[country_name].append(city_name)

                if country_name in self.country_regions and region_name in self.country_regions[country_name]:
                    self.address_strings.append(city_name + ", " + region_name
                                                + ", " + country_name)

        all_cities = [p for p in self.places if p in self.cities]
        self.city_mentions = Counter(all_cities).most_common()

    def set_other(self):
        if not self.cities:
            self.set_cities()

        def unused(place_name):
            places = [self.countries, self.cities, self.regions]
            return all(
                self.correct_country_mispelling(place_name) not in l
                for l in places)

        self.other = [p for p in self.places if unused(p)]
