"""Probe-card PCB generator.

Reads a CSV of die probing-pad coordinates and produces a KiCad PCB showing:
  * the die outline (Edge.Cuts)
  * the scaled-up rectangular probe-card aperture (Dwgs.User)
  * the die pads at their CSV coordinates, 80 x 80 um (B.Cu)
  * the probe soldering pads, 1000 x 1000 um, fanned out around the aperture (F.Cu)

Probe soldering pads are placed on a rectangle geometrically similar to the die
("aperture") but scaled up so that:
  * every soldering pad is at least EDGE_CLEARANCE (3 cm) from the die edge, and
  * along each side the 1000 um pads never collide (>= PROBE_PAD_PITCH apart).
Within each side the pads keep the same order as the die pads and are placed to
minimise lateral deviation from a straight-out projection (optimal L2 fan-out),
which minimises probe length while guaranteeing no crossing / no collision.
"""

import csv
import pathlib

from kipy import KiCad
from kipy import board_types as bt
from kipy.geometry import Vector2

board = KiCad().get_board()

HERE = pathlib.Path(__file__).parent
INPUT_DIR = HERE / "auto_probe_pcb_inputs"
OUTPUT_DIR = HERE / "auto_probe_pcb_outputs"
COL_PAD, COL_SIGNAL, COL_X, COL_Y = ("pad", "signal", "x (um)", "y (um)")

# --- geometry, all in um ---
DIE_X = 8170.73             # die "length" (x extent)
DIE_Y = 5155.584            # die "width"  (y extent)
DIE_PAD_SIDE = 80           # chip probing-pad size
PROBE_PAD_SIDE = 1000       # probe soldering-pad size
PROBE_PAD_CLEARANCE = 200   # min gap between adjacent probe pads
PROBE_PAD_PITCH = PROBE_PAD_SIDE + PROBE_PAD_CLEARANCE
EDGE_CLEARANCE = 30000      # 3 cm: min distance from a probe pad to the die edge

# --- KiCad layers ---
LAYER_DIE_OUTLINE = "BL_Edge_Cuts"
LAYER_APERTURE    = "BL_Dwgs_User"
LAYER_DIE_PADS    = "BL_B_Cu"
LAYER_PROBE_PADS  = "BL_F_Cu"


def read_pads(csv_path):
    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
    header_idx = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if COL_X in cells and COL_Y in cells:
            header_idx, header = i, cells
            break
    if header_idx is None:
        raise ValueError(f"No header row with '{COL_X}' and '{COL_Y}'.")
    ix, iy = header.index(COL_X), header.index(COL_Y)
    ipad = header.index(COL_PAD) if COL_PAD in header else None
    isig = header.index(COL_SIGNAL) if COL_SIGNAL in header else None
    pads = []
    for row in rows[header_idx + 1:]:
        if len(row) <= max(ix, iy):
            continue
        try:
            x, y = float(row[ix]), float(row[iy])
        except ValueError:
            continue
        name = row[ipad].strip() if ipad is not None and ipad < len(row) else ""
        signal = row[isig].strip() if isig is not None and isig < len(row) else ""
        pads.append({"name": name, "signal": signal, "x": x, "y": y})
    return pads


def die_center(pads):
    """Centre the given die rectangle on the bounding box of the pads."""
    xs = [p["x"] for p in pads]
    ys = [p["y"] for p in pads]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def classify_side(pad, cx, cy):
    """Assign a pad to the nearest die edge: 'T', 'B', 'L' or 'R'."""
    nx = (pad["x"] - cx) / (DIE_X / 2.0)
    ny = (pad["y"] - cy) / (DIE_Y / 2.0)
    if abs(nx) >= abs(ny):
        return "R" if nx > 0 else "L"
    return "T" if ny > 0 else "B"


def group_by_side(pads, cx, cy):
    sides = {"T": [], "B": [], "L": [], "R": []}
    for p in pads:
        sides[classify_side(p, cx, cy)].append(p)
    return sides


def pava_min_pitch(targets, pitch):
    """Place points near `targets` (already sorted) so consecutive points are
    at least `pitch` apart, minimising sum of squared deviation. Optimal via
    pool-adjacent-violators on the shifted sequence. Preserves order."""
    n = len(targets)
    if n == 0:
        return []
    a = [targets[i] - i * pitch for i in range(n)]
    blocks = []  # [mean, count]
    for x in a:
        blocks.append([x, 1])
        while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0]:
            m2, c2 = blocks.pop()
            m1, c1 = blocks.pop()
            blocks.append([(m1 * c1 + m2 * c2) / (c1 + c2), c1 + c2])
    z = []
    for m, c in blocks:
        z.extend([m] * c)
    return [z[i] + i * pitch for i in range(n)]


def required_scale(sides):
    """Smallest uniform scale of the die rectangle so that the aperture edges
    clear the die by EDGE_CLEARANCE on every side AND each side is long enough
    to hold its probe pads without collision."""
    s = 1.0 + 2.0 * EDGE_CLEARANCE / min(DIE_X, DIE_Y)
    for side, pads in sides.items():
        if not pads:
            continue
        need = (len(pads) - 1) * PROBE_PAD_PITCH + PROBE_PAD_SIDE
        die_dim = DIE_X if side in ("T", "B") else DIE_Y
        s = max(s, need / die_dim)
    return s


def place_probes(sides, cx, cy, scale):
    """Return a list of probe soldering-pad dicts: {x, y, die} (um)."""
    half_ax = scale * DIE_X / 2.0   # aperture half-width
    half_ay = scale * DIE_Y / 2.0   # aperture half-height
    probes = []
    for side, pads in sides.items():
        if not pads:
            continue
        if side in ("T", "B"):                       # horizontal edge: vary x
            pads = sorted(pads, key=lambda p: p["x"])
            xs = pava_min_pitch([p["x"] for p in pads], PROBE_PAD_PITCH)
            y = cy + half_ay if side == "T" else cy - half_ay
            for p, x in zip(pads, xs):
                probes.append({"x": x, "y": y, "die": p})
        else:                                        # vertical edge: vary y
            pads = sorted(pads, key=lambda p: p["y"])
            ys = pava_min_pitch([p["y"] for p in pads], PROBE_PAD_PITCH)
            x = cx + half_ax if side == "R" else cx - half_ax
            for p, y in zip(pads, ys):
                probes.append({"x": x, "y": y, "die": p})
    return probes


def rect(cx, cy, w, h, layer, filled=False):
    """A BoardRectangle of w x h um centred on (cx, cy) um (um -> mm)."""
    r = bt.BoardRectangle()
    r.top_left = Vector2.from_xy_mm((cx - w / 2.0) / 1000.0, (cy + h / 2.0) / 1000.0)
    r.bottom_right = Vector2.from_xy_mm((cx + w / 2.0) / 1000.0, (cy - h / 2.0) / 1000.0)
    r.layer = bt.BoardLayer.Value(layer)
    r.attributes.fill.filled = filled
    return r


def square(cx, cy, side, layer, filled=False):
    return rect(cx, cy, side, side, layer, filled)


def build_board(board, pads):
    cx, cy = die_center(pads)
    sides = group_by_side(pads, cx, cy)
    scale = required_scale(sides)
    probes = place_probes(sides, cx, cy, scale)

    # start from a clean board so re-runs are idempotent
    existing = board.get_shapes()
    if existing:
        board.remove_items(existing)

    items = []
    items.append(rect(cx, cy, DIE_X, DIE_Y, LAYER_DIE_OUTLINE))                 # die
    items.append(rect(cx, cy, scale * DIE_X, scale * DIE_Y, LAYER_APERTURE))    # aperture
    for p in pads:
        items.append(square(p["x"], p["y"], DIE_PAD_SIDE, LAYER_DIE_PADS, filled=True))
    for pr in probes:
        items.append(square(pr["x"], pr["y"], PROBE_PAD_SIDE, LAYER_PROBE_PADS, filled=True))
    board.create_items(items)

    counts = {s: len(v) for s, v in sides.items()}
    print(f"Die {DIE_X} x {DIE_Y} um, centre ({cx:.1f}, {cy:.1f})")
    print(f"Per-side pad counts: {counts}")
    print(f"Aperture scale x{scale:.2f}  ->  "
          f"{scale*DIE_X/1000:.1f} x {scale*DIE_Y/1000:.1f} mm")
    print(f"Drew {len(pads)} die pads ({DIE_PAD_SIDE} um) and "
          f"{len(probes)} probe pads ({PROBE_PAD_SIDE} um).")
    return probes


def find_input_csv():
    csvs = sorted(INPUT_DIR.glob("*.csv"))
    if not csvs:
        raise SystemExit(f"No .csv found in {INPUT_DIR} -- put your pinout there.")
    if len(csvs) > 1:
        names = ", ".join(p.name for p in csvs)
        raise SystemExit(f"Multiple .csv files in {INPUT_DIR} ({names}); keep just one.")
    return csvs[0]


def main():
    csv_path = find_input_csv()
    pads = read_pads(csv_path)
    build_board(board, pads)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{csv_path.stem}_probe_pcb.kicad_pcb"
    board.save_as(str(out_path), overwrite=True)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
