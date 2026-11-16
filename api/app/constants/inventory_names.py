"""The shop's own names for its consumables, tools and raw materials.

From the client's list (November 2026). The reference loader, the dev seed
and scripts/set_inventory_names.py all read these. ``aliases`` are older or
other spellings of the same thing (case-insensitive): a record found under
one is renamed to the shop's name so its history stays with it.
"""

# Consumables (the ``tools`` table): counted stock, handed out and bought.
CONSUMABLES = [
    {"name": "Cutting Disk", "code": "INV-CUTTING-DISK", "unit": "pcs", "shop_term": None,
     "aliases": ["cutting disc", "cut-off disc", "cutoff disc"]},
    {"name": "Grinding Stone", "code": "INV-GRINDING-STONE", "unit": "pcs", "shop_term": None,
     "aliases": ["grinding disc", "grinding disk", "grinding wheel"]},
    {"name": "Flap Disk", "code": "INV-FLAP-DISK", "unit": "pcs", "shop_term": None,
     "aliases": ["flap disc"]},
    # Cutting oil is ordered from a supplier by the gallon.
    {"name": "Fluids", "code": "INV-FLUIDS", "unit": "gallon", "shop_term": None,
     "aliases": ["cutting oil", "cutting fluid", "soluble coolant", "coolant", "fluid"],
     "ordered_from_supplier": True,
     # Different fluids under one name: the old name stays as the spec.
     "old_name_as_spec": True},
    {"name": "Sandpaper", "code": "INV-SANDPAPER", "unit": "pack", "shop_term": "Liha",
     "aliases": ["liha", "sand paper"]},
    {"name": "Drill Bits", "code": "INV-DRILL-BITS", "unit": "pcs", "shop_term": None,
     "aliases": ["drill bit", "hss drill bit", "drill"]},
    {"name": "Endmills", "code": "INV-ENDMILLS", "unit": "pcs", "shop_term": None,
     "aliases": ["end mill", "end mills", "endmill", "hss end mill", "carbide end mill"]},
    {"name": "Handtaps", "code": "INV-HANDTAPS", "unit": "pcs", "shop_term": None,
     "aliases": ["hand tap", "hand taps", "handtap", "machine tap", "tap"]},
    {"name": "Bandsaw Blade", "code": "INV-BANDSAW-BLADE", "unit": "pcs", "shop_term": None,
     "aliases": ["bandsaw blade", "band saw blade", "bandsaw blades"]},
]

# Tools (``tool_types``): individually tracked, borrowed and returned by QR.
ONE_EACH = "One unit per tool, each with its own QR."
TOOLS = [
    {"name": "Caliper", "code": "TL-CALIPER", "aliases": ["vernier caliper", "vernier", "digital caliper", "calipers"],
     "description": ONE_EACH},
    {"name": "Micrometer", "code": "TL-MICROMETER", "aliases": ["micrometers"], "description": ONE_EACH},
    {"name": "Hammers", "code": "TL-HAMMERS", "aliases": ["hammer"], "description": ONE_EACH},
    # Collets are small and kept in sets, so a set is one unit with one QR.
    {"name": "Collets", "code": "TL-COLLETS", "aliases": ["collet"],
     "description": "One unit per collet set, with one QR on the set's case. Put the set in the notes, e.g. ER32, 18 pcs."},
    {"name": "Clamps", "code": "TL-CLAMPS", "aliases": ["clamp", "c-clamp", "c clamp"], "description": ONE_EACH},
]

# Raw materials (``material_catalog``): what a PO line is for; its size goes
# on the line. SS, GI and BI pipe are different materials (stainless,
# galvanized iron, black iron); 4140, 4340 and 1045 are different steels.
MATERIALS = [
    {"name": "Stainless Plates", "shop_term": None, "default_unit": "sheet", "category": "Stainless steel",
     "aliases": ["stainless steel", "stainless plate", "ss plate", "ss plates"]},
    {"name": "MS Plates", "shop_term": None, "default_unit": "sheet", "category": "Steel",
     "aliases": ["ms", "ms plate", "mild steel", "mild steel (black iron)"]},
    {"name": "Shafting", "shop_term": None, "default_unit": "pcs", "category": "Steel",
     "aliases": ["shaft", "shafts", "shaft steel"]},
    {"name": "Bronze Materials", "shop_term": None, "default_unit": "pcs", "category": "Non-ferrous",
     "aliases": ["bronze", "bronze material"]},
    {"name": "Engineering Plastic", "shop_term": None, "default_unit": "pcs", "category": "Plastic",
     "aliases": ["engineering plastics", "plastic", "nylon", "acetal", "uhmw", "teflon", "bakelite"]},
    {"name": "SS Pipe", "shop_term": "Stainless pipe", "default_unit": "pcs", "category": "Stainless steel",
     "aliases": ["stainless pipe", "stainless steel pipe"]},
    {"name": "GI Pipe", "shop_term": "Galvanized iron pipe", "default_unit": "pcs", "category": "Steel",
     "aliases": ["galvanized iron pipe", "galvanized pipe"]},
    {"name": "BI Pipe", "shop_term": "Black iron pipe", "default_unit": "pcs", "category": "Steel",
     "aliases": ["black iron pipe"]},
    {"name": "Hydraulic Cylinder", "shop_term": None, "default_unit": "pcs", "category": "Other",
     "aliases": ["hydraulic cylinders"]},
    {"name": "4140", "shop_term": "41-40", "default_unit": "pcs", "category": "Steel",
     "aliases": ["aisi 4140", "aisi 4140 alloy steel", "4140 steel"]},
    {"name": "4340", "shop_term": "43-40", "default_unit": "pcs", "category": "Steel",
     "aliases": ["aisi 4340", "aisi 4340 alloy steel", "4340 steel"]},
    {"name": "1045", "shop_term": "10-45", "default_unit": "pcs", "category": "Steel",
     "aliases": ["aisi 1045", "1045 steel"]},
]


def norm(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def match(specs: list[dict], name: str | None) -> dict | None:
    """The spec whose name or alias is this name, if any."""
    key = norm(name)
    for spec in specs:
        if key == norm(spec["name"]) or key in {norm(a) for a in spec.get("aliases", [])}:
            return spec
    return None
