"""Slice packed plates with the CuraEngine inside the extracted Cura AppImage.

CuraEngine's CLI only reads default_value, not Cura's `value` formulas, so this
resolves the whole definition stack (fdmprinter -> creality_base ->
creality_ender3v3se, extruder, 0.4 variant, global 'standard' quality, user
overrides) by evaluating the formulas itself, then passes every setting with -s.

usage: python3 slice_plates.py <work dir from pack_plates.py> <out dir> [plate names...]
"""
import json, math, os, re, subprocess, sys, configparser, time

APP = os.path.expanduser("~/Applications/cura-extract/squashfs-root")
RES = APP + "/share/cura/resources"
PARTS = sys.argv[1]
OUT = sys.argv[2]
ONLY = sys.argv[3:]

def flatten(tree, out):
    for k, v in tree.items():
        out[k] = {p: v[p] for p in ("default_value", "value", "type") if p in v}
        if "children" in v:
            flatten(v["children"], out)

def load_def(name, d="definitions"):
    return json.load(open(f"{RES}/{d}/{name}.def.json"))

base = {}
flatten(load_def("fdmprinter")["settings"], base)
flatten(load_def("fdmextruder", "definitions")["settings"], base)

def apply_overrides(layer):
    for k, v in layer.items():
        base.setdefault(k, {}).update(v)

for n in ("creality_base", "creality_ender3v3se"):
    apply_overrides(load_def(n).get("overrides", {}))
apply_overrides(load_def("creality_base_extruder_0", "extruders").get("overrides", {}))

def cfg_layer(path):
    cp = configparser.ConfigParser(interpolation=None); cp.read(path)
    lay = {}
    for k, v in cp["values"].items():
        lay[k] = {"value": v[1:]} if v.startswith("=") else {"default_value": v}
    return lay

STACK = [cfg_layer(f"{RES}/variants/creality/creality_ender3v3se_0.4.inst.cfg"),
         cfg_layer(f"{RES}/quality/creality/base/base_global_standard.inst.cfg")]

MATERIAL = {
    "PLA": dict(material_print_temperature=205, material_bed_temperature=60, cool_fan_speed=100),
    "PLA+": dict(material_print_temperature=210, material_bed_temperature=60, cool_fan_speed=100),
    "PETG": dict(material_print_temperature=235, material_bed_temperature=75, cool_fan_speed=50,
                 cool_fan_speed_max=60, retraction_amount=1.0),
}
USER = dict(layer_height=0.2, layer_height_0=0.2, infill_sparse_density=20, adhesion_type="skirt",
            skirt_gap=3, skirt_line_count=2, support_enable=False)

def cast(v, typ):
    if not isinstance(v, str):
        return v
    if typ == "bool": return v.strip().lower() in ("true", "yes", "1", "on")
    if typ in ("int",):
        try: return int(float(v))
        except ValueError: return v
    if typ == "float":
        try: return float(v)
        except ValueError: return v
    if typ in ("[int]", "polygon", "polygons"):
        try: return eval(v)
        except Exception: return v
    return v

def resolver(user):
    layers = [base] + STACK
    cache = {}
    def raw(k):
        if k in user: return ("lit", user[k])
        for lay in reversed(layers):
            e = lay.get(k)
            if e is None: continue
            if "value" in e: return ("expr", e["value"])
            if "default_value" in e: return ("lit", e["default_value"])
        return None
    def get(k):
        if k in cache: return cache[k]
        r = raw(k)
        if r is None: raise KeyError(k)
        kind, v = r
        typ = base.get(k, {}).get("type")
        if kind == "expr" and isinstance(v, str):
            val = eval(v, env, Lookup())
        else:
            val = v
        val = cast(val, typ)
        cache[k] = val
        return val
    class Lookup(dict):
        def __missing__(self, k):
            return get(k)
    env = {"__builtins__": __builtins__, "math": math, "max": max, "min": min, "sum": sum, "any": any,
           "all": all, "round": round, "int": int, "float": float, "len": len, "map": map, "sqrt": math.sqrt,
           "log": math.log, "extruderValue": lambda e, k: get(k), "extruderValues": lambda k: [get(k)],
           "resolveOrValue": lambda k: get(k), "defaultExtruderPosition": lambda: "0",
           "anyExtruderWithMaterial": lambda k: "0", "anyExtruderNrWithOrDefault": lambda k: "0",
           "valueFromContainer": lambda *a: None, "valueFromExtruderContainer": lambda *a: None}
    return get

def fmt(v):
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, float): return repr(round(v, 6))
    return str(v)

def all_settings(user):
    get = resolver(user)
    out, bad = {}, []
    for k, e in base.items():
        if e.get("type") == "category": continue
        try: out[k] = get(k)
        except Exception as ex: bad.append((k, repr(ex)[:80]))
    return out, bad

def gcode_template(s, settings):
    return re.sub(r"\{(\w+)\}", lambda m: fmt(settings.get(m.group(1), m.group(0))), s)

LD = APP + "/runtime/compat/lib64/ld-linux-x86-64.so.2"
LP = ":".join([APP, APP + "/runtime/compat", APP + "/runtime/compat/lib/x86_64-linux-gnu",
               APP + "/runtime/compat/usr/lib/x86_64-linux-gnu", APP + "/usr/lib/x86_64-linux-gnu",
               APP + "/lib/x86_64-linux-gnu", APP + "/usr/lib"])

def slice_plate(pl):
    user = dict(USER, **MATERIAL[pl["material"]])
    user["adhesion_type"] = pl["adhesion"]
    if pl["supports"]:
        user.update(support_enable=True, support_type="everywhere", support_angle=45)
    g, bad = all_settings(user)
    if bad: print("unresolved:", bad[:10], file=sys.stderr)
    for key in ("machine_start_gcode", "machine_end_gcode"):
        g[key] = gcode_template(g[key], g)
    cmd = [LD, "--library-path", LP, APP + "/CuraEngine", "slice", "-j", f"{RES}/definitions/fdmprinter.def.json"]
    for k, v in g.items(): cmd += ["-s", f"{k}={fmt(v)}"]
    cmd += ["-e0"]
    for k, v in g.items(): cmd += ["-s", f"{k}={fmt(v)}"]
    for p in pl["parts"]:
        cmd += ["-l", p["stl"], "-s", f"mesh_position_x={-g['machine_width'] / 2}",
                "-s", f"mesh_position_y={-g['machine_depth'] / 2}"]
        if p["overrides"]:
            gm, _ = all_settings(dict(user, **p["overrides"]))
            for k, v in gm.items():
                if fmt(v) != fmt(g[k]) and not k.startswith("machine_"):
                    cmd += ["-s", f"{k}={fmt(v)}"]
    out = os.path.join(OUT, pl["name"] + ".gcode")
    cmd += ["-o", out]
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(OUT) or ".")
    log = os.path.join(PARTS, pl["name"] + ".log"); open(log, "w").write(r.stdout + r.stderr)
    m = re.search(r"Gcode header after slicing: (;FLAVOR.*?;TARGET_MACHINE[^\n]*)", r.stdout + r.stderr, re.S)
    if r.returncode == 0 and m:
        body = open(out).read()
        i = body.index(";TARGET_MACHINE"); i = body.index("\n", i)
        open(out, "w").write(m.group(1) + body[i:])
    print(pl["name"], "rc", r.returncode, f"{time.time() - t:.0f}s", flush=True)
    return r.returncode

man = json.load(open(os.path.join(PARTS, "plates.json")))
rc = 0
for pl in man:
    if ONLY and pl["name"] not in ONLY: continue
    rc |= slice_plate(pl)
sys.exit(rc)
