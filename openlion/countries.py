"""The countries OpenLion can put on the card, and how each one is mapped.

The card's map database (navigation/db_aunz) has two countries, each with its own
search catalog: Australia (catalog 0) and New Zealand (catalog 1). Their rows in
auz.ctr (name, ISO codes, driving side, speed unit) and their regions in auz.stt
are kept from the factory card; OpenLion fills either or both from OSM.

OSM models the two differently:
- Australia has suburb / locality boundaries (admin_level=9) and postcode areas.
- New Zealand has regions (4) and district / city councils (6) only; suburbs, towns
  and localities are place=* points (a few suburbs are areas). Streets go to the
  nearest place, and suburbs are grouped under their town or city like the factory
  does ("TE RAPA, HAMILTON").
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Country:
    code: str                 # ISO 3166 alpha-2, also the CLI / app key
    catalog: int              # countryID / catalog in the factory tables
    name: str                 # as on the card ("AUSTRALIA")
    title: str                # for people ("Australia")
    geofabrik: str            # download.geofabrik.de path without -latest.osm.pbf
    phone_cc: str             # international dialling code
    boundary_levels: tuple    # admin_levels read as areas: state, county (+ locality)
    localities: str           # "boundaries" (admin_level=9 areas) or "places" (place=* points)
    postcodes: bool = False   # boundary=postal_code areas exist
    # (city, state, CBD suburb): cities the factory models as one municipality
    municipalities: tuple = ()
    # suburbs (place=suburb / quarter) are listed under their nearest town or city
    suburbs_in_towns: bool = False
    # OSM state name (normalised) -> factory state name, where they differ
    state_alias: dict = field(default_factory=dict)


AU = Country(
    code="AU", catalog=0, name="AUSTRALIA", title="Australia",
    geofabrik="australia-oceania/australia", phone_cc="61",
    boundary_levels=("4", "6", "9"), localities="boundaries", postcodes=True,
    municipalities=(("CANBERRA", "AUSTRALIAN CAPITAL TERRITORY", "CITY"),),
)

NZ = Country(
    code="NZ", catalog=1, name="NEW ZEALAND", title="New Zealand",
    geofabrik="australia-oceania/new-zealand", phone_cc="64",
    boundary_levels=("4", "6"), localities="places", suburbs_in_towns=True,
    state_alias={"MANAWATU-WHANGANUI": "MANAWATU WANGANUI"},
)

ALL = (AU, NZ)
BY_CODE = {c.code: c for c in ALL}
BY_CATALOG = {c.catalog: c for c in ALL}

# place=* values used as localities where a country has no locality boundaries, and the
# weight each gets when streets are shared out by distance (a town's streets reach
# further out than a hamlet's)
PLACE_WEIGHT = {"city": 3.0, "town": 2.0, "suburb": 1.3, "quarter": 1.0, "village": 1.3,
                "hamlet": 1.0, "locality": 1.0}
SUBURB_KINDS = ("suburb", "quarter")
TOWN_KINDS = ("city", "town")
# a suburb further than this from any town or city of its council stays on its own
SUBURB_TOWN_MAX = 40000            # deg * 1e5, about 40 km


def parse_input(arg):
    """'NZ=path.osm.pbf' -> (NZ, path); a bare path is Australia (1.0 behaviour)."""
    head, sep, tail = arg.partition("=")
    if sep and head.upper() in BY_CODE:
        return BY_CODE[head.upper()], tail
    return AU, arg
