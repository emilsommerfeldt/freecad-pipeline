"""Build a part from spec.json with FreeCAD, verify it, and export FCStd/STL.

Run inside the container with:
    freecadcmd /models/build_part.py

Contract:
- Reads spec.json located next to this script.
- Builds the part with the Sketcher API using explicit named dimensional
  constraints (no baked coordinates), in a PartDesign Body + Pad.
- Verifies Shape.isValid(), closed shells, bounding box, volume, every named
  constraint value, and STL watertightness against the spec.
- Writes /models/<name>.FCStd, /models/<name>.stl (binary),
  /models/<name>.verification.json, and prints the same verification table
  to stdout as JSON. Any mismatch or failed check exits non-zero.
"""

import ctypes
import hashlib
import json
import os
import re
import sys
import traceback
import zipfile

import FreeCAD as App

# Headless FreeCAD caveat: PartDesign objects can load as generic GeoFeature
# types unless the Mod/ paths are on sys.path (see the FreeCAD wiki page
# "Headless FreeCAD").
_HOME = App.getHomePath()
for _mod_root in (os.path.join(_HOME, "Mod"), os.path.join(_HOME, "Ext")):
    if os.path.isdir(_mod_root):
        for _entry in sorted(os.listdir(_mod_root)):
            _full = os.path.join(_mod_root, _entry)
            if os.path.isdir(_full) and _full not in sys.path:
                sys.path.append(_full)

import Mesh  # noqa: E402
import MeshPart  # noqa: E402
import Part  # noqa: E402
import Sketcher  # noqa: E402

TOL = 1e-6  # mm (and mm^3) verification tolerance
FIXED_DATE = "1980-01-01T00:00:00Z"


def script_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return "/models"


def load_spec():
    path = os.path.join(script_dir(), "spec.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def build_sketch(doc, body, spec):
    """Construct the L profile with explicit, named dimensional constraints."""
    c = spec["constraints"]
    pts = [
        (0.0, 0.0),
        (c["foot_length"], 0.0),
        (c["foot_length"], c["foot_thickness"]),
        (c["wall_thickness"], c["foot_thickness"]),
        (c["wall_thickness"], c["wall_height"]),
        (0.0, c["wall_height"]),
    ]

    sketch = doc.addObject("Sketcher::SketchObject", "Profile")
    body.addObject(sketch)

    for i in range(6):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 6]
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(x1, y1, 0), App.Vector(x2, y2, 0)
            ),
            False,
        )

    # Close the wire and anchor the datum (bottom-left corner) to the origin.
    for i in range(6):
        sketch.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 6, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))

    # Orientation.
    for i in (0, 2, 4):
        sketch.addConstraint(Sketcher.Constraint("Horizontal", i))
    for i in (1, 3, 5):
        sketch.addConstraint(Sketcher.Constraint("Vertical", i))

    # Dimensional constraints, each annotated exactly once.
    dims = [
        ("foot_length", Sketcher.Constraint("DistanceX", 0, 1, 0, 2, c["foot_length"])),
        ("foot_thickness", Sketcher.Constraint("DistanceY", 1, 1, 1, 2, c["foot_thickness"])),
        ("wall_height", Sketcher.Constraint("DistanceY", -1, 1, 5, 1, c["wall_height"])),
        ("wall_thickness", Sketcher.Constraint("DistanceX", -1, 1, 3, 2, c["wall_thickness"])),
    ]
    for name, con in dims:
        idx = sketch.addConstraint(con)
        sketch.renameConstraint(idx, name)

    return sketch


def build_part(spec):
    doc = App.newDocument(spec["name"])
    # Fixed document UUID keeps the FCStd (and its StringHasher names)
    # reproducible across runs.
    doc.Uid = "123e4567-e89b-12d3-a456-426614174000"
    body = doc.addObject("PartDesign::Body", "Body")
    sketch = build_sketch(doc, body, spec)

    pad = doc.addObject("PartDesign::Pad", "Pad")
    pad.Profile = sketch
    pad.Length = spec["thickness"]
    body.addObject(pad)
    doc.recompute()
    return doc, body, sketch, pad


def check(label, expected, actual, ok, unit=""):
    return {
        "check": label,
        "expected": expected,
        "actual": round(actual, 9) if isinstance(actual, float) else actual,
        "pass": bool(ok),
        "unit": unit,
    }


def verify(doc, body, sketch, pad, spec):
    rows = []
    shape = body.Shape

    rows.append(check("shape_is_valid", True, shape.isValid(), shape.isValid()))

    n_solids = len(shape.Solids)
    rows.append(check("solid_count", 1, n_solids, n_solids == 1))

    closed = bool(shape.Shells) and all(sh.isClosed() for sh in shape.Shells)
    rows.append(check("shape_is_closed", True, closed, closed))

    bb = shape.BoundBox
    exp_bb = spec["expected"]["bounding_box"]
    for axis, key in (("x", "XLength"), ("y", "YLength"), ("z", "ZLength")):
        actual = getattr(bb, key)
        rows.append(check(
            "bounding_box_" + axis, exp_bb[axis], actual,
            abs(actual - exp_bb[axis]) <= TOL, "mm",
        ))
    rows.append(check("bbox_min_x", 0.0, bb.XMin, abs(bb.XMin) <= TOL, "mm"))
    rows.append(check("bbox_min_y", 0.0, bb.YMin, abs(bb.YMin) <= TOL, "mm"))
    rows.append(check("bbox_min_z", 0.0, bb.ZMin, abs(bb.ZMin) <= TOL, "mm"))

    exp_vol = spec["expected"]["volume"]
    vol = shape.Volume
    rows.append(check("volume", exp_vol, vol, abs(vol - exp_vol) <= 1e-4, "mm3"))

    fully = bool(getattr(sketch, "FullyConstrained", False))
    rows.append(check("sketch_fully_constrained", True, fully, fully))

    for con in sketch.Constraints:
        if con.Name and con.Name in spec["constraints"]:
            expected = float(spec["constraints"][con.Name])
            rows.append(check(
                "constraint_" + con.Name, expected, con.Value,
                abs(con.Value - expected) <= 1e-9, "mm",
            ))

    return rows


def export_stl(shape, path):
    mesh = MeshPart.meshFromShape(
        Shape=shape, LinearDeflection=0.05, AngularDeflection=0.1, Relative=False
    )
    mesh.write(path)
    with open(path, "rb") as fh:
        header = fh.read(80)
    is_binary = not header.lstrip().startswith(b"solid") or b"facet normal" not in header
    return mesh, is_binary


def check_watertight(path):
    """Re-read the written STL and confirm every edge is shared by 2 facets."""
    m = Mesh.Mesh()
    m.read(path)
    edge_use = {}
    for facet in m.Facets:
        idx = facet.PointIndices
        for a, b in ((idx[0], idx[1]), (idx[1], idx[2]), (idx[2], idx[0])):
            edge = (a, b) if a < b else (b, a)
            edge_use[edge] = edge_use.get(edge, 0) + 1
    open_edges = sum(1 for n in edge_use.values() if n != 2)
    non_manifold = m.hasNonManifolds() if hasattr(m, "hasNonManifolds") else False
    self_inter = m.hasSelfIntersections() if hasattr(m, "hasSelfIntersections") else False
    return open_edges == 0 and not non_manifold and not self_inter, {
        "facets": m.CountFacets,
        "open_edges": open_edges,
        "non_manifold": bool(non_manifold),
        "self_intersecting": bool(self_inter),
    }


def normalize_fcstd(path):
    """Strip timestamps, random document/object IDs, and zip metadata for
    bit-for-bit reproducibility of the FCStd from the same spec.

    FreeCAD documents serialize with (a) creation/modification timestamps,
    (b) object IDs that start at a random per-process base and (c) hex
    forms of those IDs (H<id>) in the element-name maps. All three are
    remapped to fixed values, then the zip is rewritten with fixed
    timestamps in the original entry order.
    """
    with zipfile.ZipFile(path, "r") as zin:
        entries = [(i.filename, zin.read(i.filename)) for i in zin.infolist()]

    doc_xml = None
    for idx, (name, _) in enumerate(entries):
        if name == "Document.xml":
            doc_xml = idx
    text = entries[doc_xml][1].decode("utf-8")

    ids = sorted({int(m) for m in re.findall(r'<Object [^>]*\bid="(\d+)"', text)})
    id_map = {old: new for new, old in enumerate(ids, start=1)}

    def repl_id(m):
        return m.group(1) + str(id_map[int(m.group(2))]) + m.group(3)

    text = re.sub(r'(<Object [^>]*\bid=")(\d+)(")', repl_id, text)
    text = re.sub(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})",
        FIXED_DATE, text,
    )
    entries[doc_xml] = ("Document.xml", text.encode("utf-8"))

    def repl_hex(m):
        old = int(m.group(1), 16)
        return "H%x" % id_map.get(old, old)

    child_re = re.compile(
        r"^(\d+) (\d+) (\d+) (\d+) (\d+) ;:H([0-9a-f]+),(\w+) 0$"
    )

    def repl_child_line(m):
        oid = int(m.group(4))
        oid = id_map.get(oid, oid)
        return "%s %s %s %d %s ;:H%s,%s 0" % (
            m.group(1), m.group(2), m.group(3), oid,
            m.group(5), repl_hex(m), m.group(7),
        )

    for idx, (name, data) in enumerate(entries):
        if name.endswith(".txt"):
            body = re.sub(rb"H([0-9a-f]+)",
                          lambda m: repl_hex(m).encode(), data)
            lines = body.split(b"\n")
            out = []
            for line in lines:
                text_line = line.decode("utf-8", "surrogateescape")
                if text_line:
                    text_line = re.sub(child_re, repl_child_line, text_line)
                out.append(text_line.encode("utf-8", "surrogateescape"))
            entries[idx] = (name, b"\n".join(out))

    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries:
            fixed = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            fixed.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(fixed, data)
    os.replace(tmp, path)


def main():
    spec = load_spec()
    name = spec["name"]
    out_dir = script_dir()

    doc, body, sketch, pad = build_part(spec)
    rows = verify(doc, body, sketch, pad, spec)

    fcstd_path = os.path.join(out_dir, name + ".FCStd")
    stl_path = os.path.join(out_dir, name + ".stl")
    ver_path = os.path.join(out_dir, name + ".verification.json")

    doc.saveAs(fcstd_path)
    normalize_fcstd(fcstd_path)

    _, is_binary = export_stl(body.Shape, stl_path)
    rows.append(check("stl_is_binary", True, is_binary, is_binary))

    watertight, mesh_stats = check_watertight(stl_path)
    exp_watertight = spec["expected"].get("watertight", True)
    rows.append(check("stl_is_watertight", exp_watertight, watertight,
                      watertight == exp_watertight))
    if spec["expected"].get("solid", True):
        rows.append(check("stl_mesh_stats", "ok", mesh_stats, True))

    report = {
        "part": name,
        "datum": spec["datum"],
        "checks": rows,
        "all_pass": all(r["pass"] for r in rows),
        "artifacts": {
            "fcstd": fcstd_path,
            "stl": stl_path,
            "verification": ver_path,
        },
    }
    if os.path.exists(stl_path):
        with open(stl_path, "rb") as fh:
            report["sha256"] = {name + ".stl": hashlib.sha256(fh.read()).hexdigest()}

    with open(ver_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")

    # freecadcmd quirks: print()/sys.exit lose buffered output or exit codes,
    # so the report goes through the FreeCAD console, then both stdio streams
    # are flushed and the process terminates with an explicit exit code.
    App.Console.PrintMessage("VERIFICATION_JSON_BEGIN\n")
    App.Console.PrintMessage(json.dumps(report, indent=2, sort_keys=True) + "\n")
    App.Console.PrintMessage("VERIFICATION_JSON_END\n")
    App.closeDocument(doc.Name)

    exit_code = 0 if report["all_pass"] else 1
    if exit_code:
        os.write(2, b"VERIFICATION FAILED\n")
    ctypes.CDLL(None).fflush(None)
    os._exit(exit_code)


try:
    main()
except SystemExit:
    raise
except Exception:
    os.write(2, traceback.format_exc().encode("utf-8"))
    ctypes.CDLL(None).fflush(None)
    os._exit(1)
