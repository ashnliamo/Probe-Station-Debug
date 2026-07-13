"""Probe-card PCB generator.

Reads a CSV of die probing-pad coordinates and produces TWO KiCad files:

  1. <stem>_probe_pcb.kicad_pcb   -- a quick LAYOUT DRAWING (gr_rect graphics):
        die outline, aperture, die pads (B.Cu), probe pads (F.Cu).

  2. <stem>_probe_card.kicad_pcb  -- a MANUFACTURABLE board to fab and send to
        Accuprobe:
          * 4.5" square board outline with the aperture cut out (Edge.Cuts)
          * probe soldering lands = through-vias, 1000 um land + 0.02" plated
            drill, with soldermask openings on both sides (solderable + top-side
            signal tap)
          * die + die pads drawn as reference only on Dwgs.User (NOT fabricated)
     plus <stem>_wiring_map.csv mapping every land -> signal + position.

Probe lands sit on a rectangle geometrically similar to the die ("aperture")
scaled up so every land is >= EDGE_CLEARANCE (3 cm) from the die edge and the
1000 um lands never collide. Within each side the lands keep die order and are
placed to minimise lateral deviation (optimal L2 fan-out) -> minimum probe
length, no crossing, no collision.
"""

import csv
import math
import pathlib

from kipy import KiCad
from kipy import board_types as bt
from kipy import common_types as ct
from kipy.geometry import Vector2

HERE = pathlib.Path(__file__).parent
INPUT_DIR = HERE / "auto_probe_pcb_inputs"
OUTPUT_DIR = HERE / "auto_probe_pcb_outputs"
COL_PAD, COL_SIGNAL, COL_X, COL_Y = ("pad", "signal", "x (um)", "y (um)")

# --- geometry, all in um ---
DIE_X = 8170.73             # die "length" (x extent)
DIE_Y = 5155.584            # die "width"  (y extent)
DIE_PAD_SIDE = 80           # chip probing-pad size
PROBE_PAD_SIDE = 1000       # probe soldering-land size (annular copper OD)
PROBE_PAD_CLEARANCE = 200   # min gap between adjacent probe lands
PROBE_PAD_PITCH = PROBE_PAD_SIDE + PROBE_PAD_CLEARANCE
EDGE_CLEARANCE = 25000      # 3 cm: min distance from a probe land to the die edge

BOARD_CENTER = (4000 * 25.4, 3000 * 25.4)   # place board/die centre here (um);
                                            # 4000 x 3000 mil -> keeps board on-sheet
VIA_DRILL = 508             # 0.02" plated through-hole (um)
MASK_EXPAND = 50            # soldermask opening = land radius + this (um)
CARD_SIZE = 114300          # 4.5" square board outline (um)
APERTURE_INSET = PROBE_PAD_SIDE / 2 + 300   # cut edge sits this far inside lands

LABEL_SIZE = 400            # signal-label text height (um)
LABEL_THICK = 60            # label stroke width (um)
LABEL_GAP = 200             # gap from land edge to start of its label (um)
PROBE_WIRE_WIDTH = 150      # visual probe-needle line width (um); not fabricated

# --- KiCad layers ---
LAYER_EDGE     = "BL_Edge_Cuts"
LAYER_REF      = "BL_Dwgs_User"     # die + die pads: reference only, not fabricated
LAYER_F_CU     = "BL_F_Cu"
LAYER_B_CU     = "BL_B_Cu"
LAYER_F_MASK   = "BL_F_Mask"
LAYER_B_MASK   = "BL_B_Mask"
LAYER_F_SILK   = "BL_F_SilkS"
LAYER_PROBE    = "BL_Cmts_User"     # probe needles: visual only, not fabricated

# --- Altium DelphiScript export layers (mechanical layers used for docs) ---
ALTIUM_OUTLINE_LAYER = "eMechanical1"    # board outline + aperture guide
ALTIUM_REF_LAYER     = "eMechanical13"   # die, die pads, probe wires (reference)


# ----------------------------------------------------------------------------
# input
# ----------------------------------------------------------------------------
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


# ----------------------------------------------------------------------------
# layout math
# ----------------------------------------------------------------------------
def die_center(pads):
    xs = [p["x"] for p in pads]
    ys = [p["y"] for p in pads]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def classify_side(pad, cx, cy):
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
    """Place points near sorted `targets` with consecutive gap >= pitch,
    minimising sum of squared deviation (pool-adjacent-violators). Keeps order."""
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
    """Smallest uniform scale of the die rectangle so the aperture clears the die
    by EDGE_CLEARANCE on every side AND each side fits its lands without collision."""
    s = 1.0 + 2.0 * EDGE_CLEARANCE / min(DIE_X, DIE_Y)
    for side, pads in sides.items():
        if not pads:
            continue
        need = (len(pads) - 1) * PROBE_PAD_PITCH + PROBE_PAD_SIDE
        die_dim = DIE_X if side in ("T", "B") else DIE_Y
        s = max(s, need / die_dim)
    return s


def place_probes(sides, cx, cy, scale):
    """Return probe land dicts: {x, y, side, id, die}."""
    half_ax = scale * DIE_X / 2.0
    half_ay = scale * DIE_Y / 2.0
    probes = []
    for side, pads in sides.items():
        if not pads:
            continue
        if side in ("T", "B"):
            pads = sorted(pads, key=lambda p: p["x"])
            xs = pava_min_pitch([p["x"] for p in pads], PROBE_PAD_PITCH)
            y = cy + half_ay if side == "T" else cy - half_ay
            coords = [(x, y) for x in xs]
        else:
            pads = sorted(pads, key=lambda p: p["y"])
            ys = pava_min_pitch([p["y"] for p in pads], PROBE_PAD_PITCH)
            x = cx + half_ax if side == "R" else cx - half_ax
            coords = [(x, yy) for yy in ys]
        for i, (p, (x, y)) in enumerate(zip(pads, coords), start=1):
            probes.append({"x": x, "y": y, "side": side,
                           "id": f"{side}{i}", "die": p})
    return probes


def compute_layout(pads):
    # translate all pads so the die/board centre sits at BOARD_CENTER
    cx0, cy0 = die_center(pads)
    dx, dy = BOARD_CENTER[0] - cx0, BOARD_CENTER[1] - cy0
    for p in pads:
        p["x"] += dx
        p["y"] += dy
    cx, cy = die_center(pads)
    sides = group_by_side(pads, cx, cy)
    scale = required_scale(sides)
    probes = place_probes(sides, cx, cy, scale)
    return {"cx": cx, "cy": cy, "sides": sides, "scale": scale, "probes": probes}


# ----------------------------------------------------------------------------
# kicad primitives (um in, mm out)
# ----------------------------------------------------------------------------
def rect(cx, cy, w, h, layer, filled=False):
    r = bt.BoardRectangle()
    r.top_left = Vector2.from_xy_mm((cx - w / 2.0) / 1000.0, (cy + h / 2.0) / 1000.0)
    r.bottom_right = Vector2.from_xy_mm((cx + w / 2.0) / 1000.0, (cy - h / 2.0) / 1000.0)
    r.layer = bt.BoardLayer.Value(layer)
    r.attributes.fill.filled = filled
    return r


def square(cx, cy, side, layer, filled=False):
    return rect(cx, cy, side, side, layer, filled)


def circle(cx, cy, radius, layer, filled=False):
    c = bt.BoardCircle()
    c.center = Vector2.from_xy_mm(cx / 1000.0, cy / 1000.0)
    c.radius_point = Vector2.from_xy_mm((cx + radius) / 1000.0, cy / 1000.0)
    c.layer = bt.BoardLayer.Value(layer)
    c.attributes.fill.filled = filled
    return c


def via_land(x, y):
    """An SMD-pad-with-via land: a solid 1000 um copper disc on F.Cu (the SMD
    solder pad) with a 0.02" plated through-hole in it (the via, for the
    top-side signal tap), and soldermask openings on both sides."""
    v = bt.Via()
    v.position = Vector2.from_xy_mm(x / 1000.0, y / 1000.0)
    v.diameter = int(PROBE_PAD_SIDE * 1000)     # nm
    v.drill_diameter = int(VIA_DRILL * 1000)    # nm
    v.type = bt.ViaType.VT_THROUGH
    mask_r = PROBE_PAD_SIDE / 2.0 + MASK_EXPAND
    return [v,
            circle(x, y, PROBE_PAD_SIDE / 2.0, LAYER_F_CU, filled=True),  # SMD pad
            circle(x, y, mask_r, LAYER_F_MASK, filled=True),
            circle(x, y, mask_r, LAYER_B_MASK, filled=True)]


def probe_wire(pr):
    """A line from a landing pad to the die pad it probes -- a visual stand-in
    for the probe needle. On a documentation layer, NOT fabricated."""
    d = pr["die"]
    s = bt.BoardSegment()
    s.start = Vector2.from_xy_mm(pr["x"] / 1000.0, pr["y"] / 1000.0)
    s.end = Vector2.from_xy_mm(d["x"] / 1000.0, d["y"] / 1000.0)
    s.layer = bt.BoardLayer.Value(LAYER_PROBE)
    s.width = int(PROBE_WIRE_WIDTH * 1000)
    return s


def signal_label(x, y, side, text):
    """A silkscreen label placed just outside a land, running radially outward
    (vertical on top/bottom, horizontal on left/right) so labels don't collide
    at the land pitch."""
    off = PROBE_PAD_SIDE / 2.0 + LABEL_GAP
    # KiCad text extends: angle 90 -> HA_LEFT=down, HA_RIGHT=up;
    #                     angle 0  -> HA_LEFT=right, HA_RIGHT=left.
    # Pick the alignment that makes each label read radially outward.
    if side == "T":
        pos, angle, ha = (x, y + off), 90.0, "HA_RIGHT"
    elif side == "B":
        pos, angle, ha = (x, y - off), 90.0, "HA_LEFT"
    elif side == "L":
        pos, angle, ha = (x - off, y), 0.0, "HA_RIGHT"
    else:  # R
        pos, angle, ha = (x + off, y), 0.0, "HA_LEFT"
    t = bt.BoardText()
    t.value = text
    t.position = Vector2.from_xy_mm(pos[0] / 1000.0, pos[1] / 1000.0)
    t.layer = bt.BoardLayer.Value(LAYER_F_SILK)
    a = t.attributes
    a.size = Vector2.from_xy_mm(LABEL_SIZE / 1000.0, LABEL_SIZE / 1000.0)
    a.stroke_width = int(LABEL_THICK * 1000)
    a.angle = angle
    a.keep_upright = False
    a.horizontal_alignment = ct.HorizontalAlignment.Value(ha)
    a.vertical_alignment = ct.VerticalAlignment.Value("VA_CENTER")
    return t


def clear_board(board):
    for getter in ("get_shapes", "get_vias", "get_footprints"):
        try:
            items = getattr(board, getter)()
            if items:
                board.remove_items(items)
        except Exception:
            pass


# ----------------------------------------------------------------------------
# builders
# ----------------------------------------------------------------------------
def build_fab(board, pads, L):
    """Manufacturable board: outline + aperture cutout + solderable via-lands."""
    cx, cy, scale = L["cx"], L["cy"], L["scale"]
    clear_board(board)
    items = []
    # board outline + aperture cutout (both closed Edge.Cuts loops)
    items.append(rect(cx, cy, CARD_SIZE, CARD_SIZE, LAYER_EDGE))
    cut_w = scale * DIE_X - 2 * APERTURE_INSET
    cut_h = scale * DIE_Y - 2 * APERTURE_INSET
    items.append(rect(cx, cy, cut_w, cut_h, LAYER_EDGE))
    # reference-only die + die pads (not fabricated)
    items.append(rect(cx, cy, DIE_X, DIE_Y, LAYER_REF))
    for p in pads:
        items.append(square(p["x"], p["y"], DIE_PAD_SIDE, LAYER_REF))
    # solderable SMD-pad-with-via lands + silkscreen signal labels + probe wires
    for pr in L["probes"]:
        items.extend(via_land(pr["x"], pr["y"]))
        items.append(signal_label(pr["x"], pr["y"], pr["side"],
                                   pr["die"]["signal"] or pr["id"]))
        items.append(probe_wire(pr))
    board.create_items(items)
    check_fit(L)


def check_fit(L):
    """Warn if any land+mask leaves the card, or the copper frame is thin."""
    cx, cy = L["cx"], L["cy"]
    half = CARD_SIZE / 2.0
    mask_r = PROBE_PAD_SIDE / 2.0 + MASK_EXPAND
    worst = min(min(half - abs(pr["x"] - cx), half - abs(pr["y"] - cy))
                for pr in L["probes"]) - mask_r
    frame_lr = half - (L["scale"] * DIE_X / 2.0)  # card edge to left/right lands
    if worst < 0:
        print(f"  WARNING: lands extend {-worst:.0f} um past the card outline.")
    else:
        print(f"  Min land-to-card-edge clearance: {worst:.0f} um.")
    print(f"  Left/right copper frame width ~ {frame_lr - PROBE_PAD_SIDE/2:.0f} um.")


# ----------------------------------------------------------------------------
# outputs
# ----------------------------------------------------------------------------
def _altium_label(pr):
    """Anchor (mm), rotation (deg) and text for an Altium silk label, placed so
    it reads radially outward. Altium stroke text is anchored bottom-left and
    extends up/right, so left/bottom labels are shifted by an estimated length."""
    off = (PROBE_PAD_SIDE / 2.0 + LABEL_GAP) / 1000.0
    h = LABEL_SIZE / 1000.0
    x, y = pr["x"] / 1000.0, pr["y"] / 1000.0
    sig = pr["die"]["signal"] or pr["id"]
    ln = max(1, len(sig)) * h * 0.75  # rough text length
    if pr["side"] == "T":
        return (x - h / 2, y + off, 90.0, sig)
    if pr["side"] == "B":
        return (x - h / 2, y - off - ln, 90.0, sig)
    if pr["side"] == "L":
        return (x - off - ln, y - h / 2, 0.0, sig)
    return (x + off, y - h / 2, 0.0, sig)  # R


def _pas_str(s):
    return "'" + s.replace("'", "''") + "'"


def write_altium_script(path, pads, L):
    """Emit a self-contained Altium DelphiScript (.pas). Run it in Altium
    Designer (File > Run Script..., choose GenerateProbeCard) to build a PCB
    equivalent to the manufacturable KiCad board: 4.5" outline, aperture cutout,
    round through-hole solder lands (1000 um pad, 0.02" plated hole) with silk
    signal labels, and reference die/die-pads/probe-wires on a mech layer."""
    cx, cy, scale = L["cx"], L["cy"], L["scale"]
    H = CARD_SIZE / 2.0
    cw = scale * DIE_X - 2 * APERTURE_INSET
    ch = scale * DIE_Y - 2 * APERTURE_INSET
    PAD = PROBE_PAD_SIDE / 1000.0
    HOLE = VIA_DRILL / 1000.0
    TEXTH = LABEL_SIZE / 1000.0
    TEXTW = LABEL_THICK / 1000.0
    PROBEW = PROBE_WIRE_WIDTH / 1000.0
    DIEPAD = DIE_PAD_SIDE / 1000.0
    pcbdoc = str(path.with_suffix(".PcbDoc"))

    def mm(v):
        return f"{v:.4f}"

    o = []
    w = o.append
    # ---- header + helpers (// comments so no { } clash) ----
    w(f"""// Auto-generated by auto_probe_pcb.py -- Altium DelphiScript.
// In Altium Designer: File > Run Script..., then run GenerateProbeCard.
// Builds a PCB equivalent to the KiCad probe-card board.

Var
    Board : IPCB_Board;

Procedure RegisterObj(Obj);
Begin
    Board.AddPCBObject(Obj);
    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
        PCBM_BoardRegisteration, Obj.I_ObjectAddress);
End;

Procedure AddLand(XMM, YMM : Double; Desig : String);
Var Pad;
Begin
    Pad := PCBServer.PCBObjectFactory(ePadObject, eNoDimension, eCreate_Default);
    Pad.X := MMsToCoord(XMM);
    Pad.Y := MMsToCoord(YMM);
    Pad.Layer := eMultiLayer;
    Pad.TopShape := eRounded;
    Pad.MidShape := eRounded;
    Pad.BotShape := eRounded;
    Pad.TopXSize := MMsToCoord({mm(PAD)}); Pad.TopYSize := MMsToCoord({mm(PAD)});
    Pad.MidXSize := MMsToCoord({mm(PAD)}); Pad.MidYSize := MMsToCoord({mm(PAD)});
    Pad.BotXSize := MMsToCoord({mm(PAD)}); Pad.BotYSize := MMsToCoord({mm(PAD)});
    Pad.HoleSize := MMsToCoord({mm(HOLE)});
    Pad.Plated := True;
    Pad.Name := Desig;
    RegisterObj(Pad);
End;

Procedure AddText(XMM, YMM, Rot : Double; S : String);
Var T;
Begin
    T := PCBServer.PCBObjectFactory(eTextObject, eNoDimension, eCreate_Default);
    T.XLocation := MMsToCoord(XMM);
    T.YLocation := MMsToCoord(YMM);
    T.Layer := eTopOverlay;
    T.Size := MMsToCoord({mm(TEXTH)});
    T.Width := MMsToCoord({mm(TEXTW)});
    T.Rotation := Rot;
    T.Text := S;
    RegisterObj(T);
End;

Procedure AddRefTrack(X1, Y1, X2, Y2, Wid : Double);
Var Tr;
Begin
    Tr := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
    Tr.X1 := MMsToCoord(X1); Tr.Y1 := MMsToCoord(Y1);
    Tr.X2 := MMsToCoord(X2); Tr.Y2 := MMsToCoord(Y2);
    Tr.Width := MMsToCoord(Wid);
    Tr.Layer := {ALTIUM_REF_LAYER};
    RegisterObj(Tr);
End;

Procedure AddOutlineTrack(X1, Y1, X2, Y2, Wid : Double);
Var Tr;
Begin
    Tr := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
    Tr.X1 := MMsToCoord(X1); Tr.Y1 := MMsToCoord(Y1);
    Tr.X2 := MMsToCoord(X2); Tr.Y2 := MMsToCoord(Y2);
    Tr.Width := MMsToCoord(Wid);
    Tr.Layer := {ALTIUM_OUTLINE_LAYER};
    RegisterObj(Tr);
End;

Procedure AddRefRect(X1, Y1, X2, Y2, Wid : Double);
Begin
    AddRefTrack(X1, Y1, X2, Y1, Wid);
    AddRefTrack(X2, Y1, X2, Y2, Wid);
    AddRefTrack(X2, Y2, X1, Y2, Wid);
    AddRefTrack(X1, Y2, X1, Y1, Wid);
End;

Procedure AddOutlineRect(X1, Y1, X2, Y2, Wid : Double);
Begin
    AddOutlineTrack(X1, Y1, X2, Y1, Wid);
    AddOutlineTrack(X2, Y1, X2, Y2, Wid);
    AddOutlineTrack(X2, Y2, X1, Y2, Wid);
    AddOutlineTrack(X1, Y2, X1, Y1, Wid);
End;

Procedure AddDiePad(XMM, YMM : Double);
Var Hf;
Begin
    Hf := {mm(DIEPAD / 2.0)};
    AddRefRect(XMM - Hf, YMM - Hf, XMM + Hf, YMM + Hf, 0.02);
End;

Procedure AddProbe(X1, Y1, X2, Y2 : Double);
Begin
    AddRefTrack(X1, Y1, X2, Y2, {mm(PROBEW)});
End;

// --- board shape: rectangular outline. Standard resize-outline pattern;
// --- may need tweaking per Altium version.
Procedure SetRectBoard(X1, Y1, X2, Y2 : Double);
Begin
    Board.BoardOutline.Invalidate;
    Board.BoardOutline.PointCount := 4;
    Board.BoardOutline.Segments[0].Kind := ePolySegmentLine;
    Board.BoardOutline.Segments[0].vx := MMsToCoord(X1);
    Board.BoardOutline.Segments[0].vy := MMsToCoord(Y1);
    Board.BoardOutline.Segments[1].Kind := ePolySegmentLine;
    Board.BoardOutline.Segments[1].vx := MMsToCoord(X2);
    Board.BoardOutline.Segments[1].vy := MMsToCoord(Y1);
    Board.BoardOutline.Segments[2].Kind := ePolySegmentLine;
    Board.BoardOutline.Segments[2].vx := MMsToCoord(X2);
    Board.BoardOutline.Segments[2].vy := MMsToCoord(Y2);
    Board.BoardOutline.Segments[3].Kind := ePolySegmentLine;
    Board.BoardOutline.Segments[3].vx := MMsToCoord(X1);
    Board.BoardOutline.Segments[3].vy := MMsToCoord(Y2);
    Board.BoardOutline.Validate;
End;

// --- aperture: a board-cutout region. Region-contour API is the part most
// --- likely to need adjustment for your Altium version.
Procedure AddCutout(X1, Y1, X2, Y2 : Double);
Var Rgn, C;
Begin
    Rgn := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    Rgn.SetState_Kind(eRegionKind_BoardCutout);
    C := PCBServer.PCBContourFactory;
    C.AddPoint(MMsToCoord(X1), MMsToCoord(Y1));
    C.AddPoint(MMsToCoord(X2), MMsToCoord(Y1));
    C.AddPoint(MMsToCoord(X2), MMsToCoord(Y2));
    C.AddPoint(MMsToCoord(X1), MMsToCoord(Y2));
    Rgn.SetOutlineContour(C);
    Rgn.Layer := eTopLayer;
    RegisterObj(Rgn);
End;
""")

    # ---- data procedures ----
    w("Procedure BuildLands;\nBegin")
    for pr in L["probes"]:
        w(f"    AddLand({mm(pr['x']/1000)}, {mm(pr['y']/1000)}, {_pas_str(pr['id'])});")
    w("End;\n")

    w("Procedure BuildLabels;\nBegin")
    for pr in L["probes"]:
        lx, ly, rot, sig = _altium_label(pr)
        w(f"    AddText({mm(lx)}, {mm(ly)}, {mm(rot)}, {_pas_str(sig)});")
    w("End;\n")

    w("Procedure BuildDiePads;\nBegin")
    for p in pads:
        w(f"    AddDiePad({mm(p['x']/1000)}, {mm(p['y']/1000)});")
    w("End;\n")

    w("Procedure BuildProbes;\nBegin")
    for pr in L["probes"]:
        d = pr["die"]
        w(f"    AddProbe({mm(pr['x']/1000)}, {mm(pr['y']/1000)}, "
          f"{mm(d['x']/1000)}, {mm(d['y']/1000)});")
    w("End;\n")

    # ---- entry point ----
    w(f"""Procedure GenerateProbeCard;
Begin
    If PCBServer = Nil Then
    Begin
        ShowMessage('PCBServer is nil -- the PCB editor is not loaded.');
        Exit;
    End;
    Board := PCBServer.GetCurrentPCBBoard;
    If Board = Nil Then
    Begin
        ShowMessage('No PCB is open. Create a blank PCB first: ' +
            'File > New > PCB, make that tab active, then run this script again.');
        Exit;
    End;
    PCBServer.PreProcess;
    SetRectBoard({mm((cx-H)/1000)}, {mm((cy-H)/1000)}, {mm((cx+H)/1000)}, {mm((cy+H)/1000)});
    AddCutout({mm((cx-cw/2)/1000)}, {mm((cy-ch/2)/1000)}, {mm((cx+cw/2)/1000)}, {mm((cy+ch/2)/1000)});
    AddOutlineRect({mm((cx-cw/2)/1000)}, {mm((cy-ch/2)/1000)}, {mm((cx+cw/2)/1000)}, {mm((cy+ch/2)/1000)}, 0.1);
    AddRefRect({mm((cx-DIE_X/2)/1000)}, {mm((cy-DIE_Y/2)/1000)}, {mm((cx+DIE_X/2)/1000)}, {mm((cy+DIE_Y/2)/1000)}, 0.05);
    BuildDiePads;
    BuildLands;
    BuildLabels;
    BuildProbes;
    PCBServer.PostProcess;
    Board.ViewManager_FullUpdate;
    Client.SendMessage('PCB:Zoom', 'Action=Redraw', 255, Client.CurrentView);
    ShowMessage('Probe card built on the active PCB. ' +
        'Use File > Save As to save it as a .PcbDoc.');
End;
""")

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(o))


def write_wiring_map(path, L):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["land_id", "side", "signal", "die_pad",
                    "land_x_mm", "land_y_mm", "die_x_um", "die_y_um"])
        for pr in L["probes"]:
            d = pr["die"]
            w.writerow([pr["id"], pr["side"], d["signal"], d["name"],
                        f"{pr['x']/1000:.3f}", f"{pr['y']/1000:.3f}",
                        f"{d['x']:.3f}", f"{d['y']:.3f}"])


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
    L = compute_layout(pads)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    counts = {s: len(v) for s, v in L["sides"].items()}
    print(f"Die {DIE_X} x {DIE_Y} um, centre ({L['cx']:.1f}, {L['cy']:.1f})")
    print(f"Per-side pad counts: {counts}")
    print(f"Aperture scale x{L['scale']:.2f} -> "
          f"{L['scale']*DIE_X/1000:.1f} x {L['scale']*DIE_Y/1000:.1f} mm")

    # --- outputs that DON'T need KiCad (always produced) ---
    map_path = OUTPUT_DIR / f"{csv_path.stem}_wiring_map.csv"
    write_wiring_map(map_path, L)
    print(f"Wrote wiring map:   {map_path}")

    pas_path = OUTPUT_DIR / f"{csv_path.stem}_probe_card.pas"
    write_altium_script(pas_path, pads, L)
    print(f"Wrote Altium script:{pas_path}")

    print(f"{len(pads)} die pads / {len(L['probes'])} probe lands "
          f"({VIA_DRILL} um dia drill, {PROBE_PAD_SIDE} um land).")

    # --- outputs that DO need a running KiCad (IPC API) ---
    try:
        board = KiCad().get_board()
    except Exception as e:
        print(f"\nKiCad not reachable -- skipped .kicad_pcb outputs "
              f"(open a board in KiCad and rerun). Details: {e}")
        return

    build_fab(board, pads, L)
    fab_path = OUTPUT_DIR / f"{csv_path.stem}_probe_card.kicad_pcb"
    board.save_as(str(fab_path), overwrite=True)
    print(f"Wrote fab board:    {fab_path}")


if __name__ == "__main__":
    main()
