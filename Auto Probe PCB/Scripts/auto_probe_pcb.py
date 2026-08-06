import csv
import math
import pathlib

HERE = pathlib.Path(__file__).parent
INPUT_DIR = HERE.parent / "auto_probe_pcb_inputs"
OUTPUT_DIR = HERE.parent / "auto_probe_pcb_outputs"
COL_PAD, COL_SIGNAL, COL_X, COL_Y = ("pad", "signal", "x (um)", "y (um)")
COL_NETCLASS = "net class"
UNCLASSIFIED = "UNCLASSIFIED"

DIE_X = 8170.73
DIE_Y = 5155.584
PROBE_PAD_SIDE = 1000
PROBE_PAD_CLEARANCE = 200
PROBE_PAD_PITCH = PROBE_PAD_SIDE + PROBE_PAD_CLEARANCE
PROBE_WIRE_WIDTH = 150

LAND_ROWS = 2
NEEDLE_CLEARANCE = 127
ROW_GAP = PROBE_PAD_SIDE + PROBE_PAD_CLEARANCE
ROW_PITCH = max(PROBE_PAD_PITCH,
                PROBE_PAD_SIDE + PROBE_WIRE_WIDTH + 2 * NEEDLE_CLEARANCE)
STAGGER_STEP = ROW_PITCH / float(LAND_ROWS)

BOARD_CENTER = (4000 * 25.4, 3000 * 25.4)
VIA_DRILL = 508 # 0.02" via hole
BOARD_WIDTH = 114500
BOARD_HEIGHT = 188500
APERTURE_CLEARANCE = 3175
KEEP_OUT_WIDTH = 44450
KEEP_OUT_HEIGHT = 38100
LAND_KEEPOUT_GAP = 1500

LABEL_SIZE = 400
LABEL_THICK = 60
LABEL_GAP = 508
MARKER_LINE = 150

ALTIUM_MARKER_LAYER  = "eMechanical15"
ALTIUM_REF_LAYER     = "eMechanical13"

# shared by the PcbLib footprint and the SchLib component -- the two must match
# for Altium to resolve the symbol's footprint model.
PROBECARD_NAME = "ProbeCard"

# --- SchLib symbol (one net-class part per block), all in mils ---
SCH_PIN_PITCH = 100
SCH_PIN_LENGTH = 300
SCH_BLOCK_WIDTH = 1200


def read_pads(csv_path): # parsing the inputted CSV
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
    inet = header.index(COL_NETCLASS) if COL_NETCLASS in header else None
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
        netclass = row[inet].strip() if inet is not None and inet < len(row) else ""
        pads.append({"name": name, "signal": signal, "x": x, "y": y,
                     "netclass": netclass or UNCLASSIFIED})
    return pads


def die_center(pads): # finds the center of the die based on the pads
    xs = [p["x"] for p in pads]
    ys = [p["y"] for p in pads]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def group_by_edge(pads, cx, cy): # classifies each pad into top, bottom, left, or right and groups them
    edges = {"T": [], "B": [], "L": [], "R": []}
    for p in pads:
        nx = (p["x"] - cx) / (DIE_X / 2.0)
        ny = (p["y"] - cy) / (DIE_Y / 2.0)
        if abs(nx) >= abs(ny):
            edge = "R" if nx > 0 else "L"
        else:
            edge = "T" if ny > 0 else "B"
        edges[edge].append(p)
    return edges


def smooth_die_pad_spacing(targets, pitch): # spaces landing pads evenly along the edge of the die, while keeping them as close as possible to their original positions
    n = len(targets)
    if n == 0:
        return []
    a = [targets[i] - i * pitch for i in range(n)]
    blocks = []
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


def required_scale(edges): 
    s = 1.0 + 2.0 * LAND_KEEPOUT_GAP / min(KEEP_OUT_WIDTH, KEEP_OUT_HEIGHT)
    for edge, pads in edges.items():
        if not pads:
            continue
        need = (len(pads) - 1) * STAGGER_STEP + PROBE_PAD_SIDE
        ko_dim = KEEP_OUT_WIDTH if edge in ("T", "B") else KEEP_OUT_HEIGHT
        s = max(s, need / ko_dim)
    return s


def place_probes(edges, cx, cy, scale):
    half_ax = scale * KEEP_OUT_WIDTH / 2.0
    half_ay = scale * KEEP_OUT_HEIGHT / 2.0
    probes = []
    for edge, pads in edges.items():
        if not pads:
            continue
        if edge in ("T", "B"):
            pads = sorted(pads, key=lambda p: p["x"])
            us = smooth_die_pad_spacing([p["x"] for p in pads], STAGGER_STEP)
        else:
            pads = sorted(pads, key=lambda p: p["y"])
            us = smooth_die_pad_spacing([p["y"] for p in pads], STAGGER_STEP)
        for i, (p, u) in enumerate(zip(pads, us)):
            row = i % LAND_ROWS
            if edge == "T":
                x, y = u, cy + half_ay + row * ROW_GAP
            elif edge == "B":
                x, y = u, cy - half_ay - row * ROW_GAP
            elif edge == "R":
                x, y = cx + half_ax + row * ROW_GAP, u
            else:
                x, y = cx - half_ax - row * ROW_GAP, u
            probes.append({"x": x, "y": y, "edge": edge, "row": row,
                           "id": f"{edge}{i + 1}", "die": p})
    return probes


def compute_layout(pads):
    cx0, cy0 = die_center(pads)
    dx, dy = BOARD_CENTER[0] - cx0, BOARD_CENTER[1] - cy0
    for p in pads:
        p["x"] += dx
        p["y"] += dy
    cx, cy = die_center(pads)
    edges = group_by_edge(pads, cx, cy)
    scale = required_scale(edges)
    probes = place_probes(edges, cx, cy, scale)
    return {"cx": cx, "cy": cy, "edges": edges, "scale": scale, "probes": probes}


def aperture_radius():
    return math.hypot(DIE_X / 2.0, DIE_Y / 2.0) + APERTURE_CLEARANCE


def marker_dims():
    return KEEP_OUT_WIDTH, KEEP_OUT_HEIGHT


def check_fit(L):
    cx, cy = L["cx"], L["cy"]
    half_w = BOARD_WIDTH / 2.0
    half_h = BOARD_HEIGHT / 2.0
    pad_half = PROBE_PAD_SIDE / 2.0
    worst = min(min(half_w - abs(pr["x"] - cx), half_h - abs(pr["y"] - cy))
                for pr in L["probes"]) - pad_half
    frame_lr = half_w - (L["scale"] * KEEP_OUT_WIDTH / 2.0)
    if worst < 0:
        print(f"  WARNING: lands extend {-worst:.0f} um past the card outline.")
    else:
        print(f"  Min land-to-card-edge clearance: {worst:.0f} um.")
    print(f"  Left/right copper frame width ~ {frame_lr - PROBE_PAD_SIDE/2:.0f} um.")


def _altium_label(pr):
    off = ((LAND_ROWS - 1 - pr["row"]) * ROW_GAP
           + PROBE_PAD_SIDE / 2.0 + LABEL_GAP) / 1000.0
    h = LABEL_SIZE / 1000.0
    x, y = pr["x"] / 1000.0, pr["y"] / 1000.0
    sig = pr["die"]["signal"] or pr["id"]
    ln = max(1, len(sig)) * h * 1.1
    edge = pr["edge"]
    if edge == "T":
        return (x - h / 2, y + off, 90.0, sig)
    if edge == "B":
        return (x - h / 2, y - off - ln, 90.0, sig)
    if edge == "R":
        return (x + off, y - h / 2, 0.0, sig)
    return (x - off - ln, y - h / 2, 0.0, sig)


def _pas_str(s):
    return "'" + s.replace("'", "''") + "'"


def _part_name(netclass):
    # one component/footprint per net class -- the SchLib component and its
    # PcbLib footprint share this name so Altium resolves the footprint model.
    safe = "".join(c if c.isalnum() else "_" for c in netclass)
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_")
    return f"{PROBECARD_NAME}_{safe}" if safe else PROBECARD_NAME


def write_altium_script(path, L):
    cx, cy = L["cx"], L["cy"]
    HW = BOARD_WIDTH / 2.0
    HH = BOARD_HEIGHT / 2.0
    APR = aperture_radius()
    MW, MH = marker_dims()
    PAD = PROBE_PAD_SIDE / 1000.0
    HOLE = VIA_DRILL / 1000.0
    TEXTH = LABEL_SIZE / 1000.0
    TEXTW = LABEL_THICK / 1000.0
    PROBEW = PROBE_WIRE_WIDTH / 1000.0

    def mm(v):
        return f"{v:.4f}"

    o = []
    w = o.append
    w(f"""

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

Procedure AddProbe(X1, Y1, X2, Y2 : Double);
Begin
    AddRefTrack(X1, Y1, X2, Y2, {mm(PROBEW)});
End;

// --- everything belonging to one probe: the solder land, its silk label,
// --- and the probe needle running from the land to the die-pad coordinate.
Procedure EmitLand(LX, LY : Double; Desig : String;
                   TX, TY, Rot : Double; Sig : String; DX, DY : Double);
Begin
    AddLand(LX, LY, Desig);
    AddText(TX, TY, Rot, Sig);
    AddProbe(LX, LY, DX, DY);
End;

// --- board shape: rectangular outline.
// --- Segments[i] returns the record BY VALUE, so "Segments[i].vx := ..."
// --- edits a throwaway copy and does nothing. Build a local TPolySegment
// --- and assign it back into Segments[i].
Procedure SetOutlinePoint(Idx : Integer; XMM, YMM : Double);
Var Seg : TPolySegment;
Begin
    Seg := Board.BoardOutline.Segments[Idx];
    Seg.Kind := ePolySegmentLine;
    Seg.vx := MMsToCoord(XMM);
    Seg.vy := MMsToCoord(YMM);
    Board.BoardOutline.Segments[Idx] := Seg;
End;

Procedure SetRectBoard(X1, Y1, X2, Y2 : Double);
Begin
    Board.BoardOutline.Invalidate;
    Board.BoardOutline.PointCount := 4;
    SetOutlinePoint(0, X1, Y1);
    SetOutlinePoint(1, X2, Y1);
    SetOutlinePoint(2, X2, Y2);
    SetOutlinePoint(3, X1, Y2);
    Board.BoardOutline.Validate;
    Board.ViewManager_FullUpdate;
End;

// --- circular aperture: board-cutout region approximated by a 72-gon.
// --- The region-contour API is the part most likely to need adjustment
// --- for your Altium version.
Procedure AddCircleCutout(CXmm, CYmm, Rmm : Double);
Var Rgn, C, i, ang;
Begin
    Rgn := PCBServer.PCBObjectFactory(eRegionObject, eNoDimension, eCreate_Default);
    Rgn.SetState_Kind(eRegionKind_BoardCutout);
    C := PCBServer.PCBContourFactory;
    For i := 0 To 71 Do
    Begin
        ang := i * 6.28318530717959 / 72.0;
        C.AddPoint(MMsToCoord(CXmm + Rmm * Cos(ang)), MMsToCoord(CYmm + Rmm * Sin(ang)));
    End;
    Rgn.SetOutlineContour(C);
    Rgn.Layer := eTopLayer;
    RegisterObj(Rgn);
End;

// --- die-aspect clearance marker on a non-fabricated mechanical layer.
Procedure AddMarkerTrack(X1, Y1, X2, Y2, Wid : Double);
Var Tr;
Begin
    Tr := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
    Tr.X1 := MMsToCoord(X1); Tr.Y1 := MMsToCoord(Y1);
    Tr.X2 := MMsToCoord(X2); Tr.Y2 := MMsToCoord(Y2);
    Tr.Width := MMsToCoord(Wid);
    Tr.Layer := {ALTIUM_MARKER_LAYER};
    RegisterObj(Tr);
End;

Procedure AddMarkerRect(X1, Y1, X2, Y2, Wid : Double);
Begin
    AddMarkerTrack(X1, Y1, X2, Y1, Wid);
    AddMarkerTrack(X2, Y1, X2, Y2, Wid);
    AddMarkerTrack(X2, Y2, X1, Y2, Wid);
    AddMarkerTrack(X1, Y2, X1, Y1, Wid);
End;
""")

    w("Procedure BuildAll;\nBegin")
    for pr in L["probes"]:
        d = pr["die"]
        lx, ly, rot, sig = _altium_label(pr)
        w(f"    EmitLand({mm(pr['x']/1000)}, {mm(pr['y']/1000)}, {_pas_str(pr['id'])}, "
          f"{mm(lx)}, {mm(ly)}, {mm(rot)}, {_pas_str(sig)}, "
          f"{mm(d['x']/1000)}, {mm(d['y']/1000)});")
    w("End;\n")

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
    SetRectBoard({mm((cx-HW)/1000)}, {mm((cy-HH)/1000)}, {mm((cx+HW)/1000)}, {mm((cy+HH)/1000)});
    AddCircleCutout({mm(cx/1000)}, {mm(cy/1000)}, {mm(APR/1000)});
    AddMarkerRect({mm((cx-MW/2)/1000)}, {mm((cy-MH/2)/1000)}, {mm((cx+MW/2)/1000)}, {mm((cy+MH/2)/1000)}, {mm(MARKER_LINE/1000)});
    BuildAll;
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
        w.writerow(["land_id", "edge", "row", "signal", "die_pad",
                    "land_x_mm", "land_y_mm", "die_x_um", "die_y_um"])
        for pr in L["probes"]:
            d = pr["die"]
            w.writerow([pr["id"], pr["edge"], pr["row"], d["signal"], d["name"],
                        f"{pr['x']/1000:.3f}", f"{pr['y']/1000:.3f}",
                        f"{d['x']:.3f}", f"{d['y']:.3f}"])


def _edge_order(pr):
    return ("TBLR".index(pr["edge"]), int(pr["id"][1:]))


def group_by_netclass(probes):
    classes = {}
    for pr in probes:
        classes.setdefault(pr["die"]["netclass"], []).append(pr)
    # within a block, keep shared nets contiguous, then order by land id
    for members in classes.values():
        members.sort(key=lambda pr: (pr["die"]["signal"], _edge_order(pr)))
    classes = merge_diff_pairs(classes)
    # emit the blocks in descending size so the widest sheet reads left-heavy
    return dict(sorted(classes.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def _interleave_pairs(p_members, n_members):
    # place each P land next to its N partner (the signal differing in exactly
    # one position -- the polarity letter, e.g. CLKEXTP<0> / CLKEXTM<0>), so the
    # merged column reads P, N, P, N... Unmatched lands trail at the end.
    pool = list(n_members)
    ordered = []
    for p in p_members:
        sig = p["die"]["signal"]
        best_i, best_d = None, None
        for i, q in enumerate(pool):
            s = q["die"]["signal"]
            if len(s) != len(sig):
                continue
            d = sum(a != b for a, b in zip(sig, s))
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        ordered.append(p)
        if best_i is not None and best_d == 1:
            ordered.append(pool.pop(best_i))
    ordered.extend(pool)
    return ordered


def merge_diff_pairs(classes):
    # merge net-class pairs that differ only by a trailing " P"/" N" (a
    # differential pair split across two classes) into one block named by the
    # shared prefix, with each P land interleaved next to its N partner.
    out = {}
    used = set()
    for name in classes:
        if name in used:
            continue
        base = None
        if name.endswith(" P") and (name[:-2] + " N") in classes:
            base, p_name, n_name = name[:-2], name, name[:-2] + " N"
        elif name.endswith(" N") and (name[:-2] + " P") in classes:
            base, p_name, n_name = name[:-2], name[:-2] + " P", name
        if base is not None:
            out[base.strip()] = _interleave_pairs(classes[p_name], classes[n_name])
            used.add(p_name)
            used.add(n_name)
        else:
            out[name] = classes[name]
    return out


def write_pcb_library_script(path, L):
    # Emit ONE PcbLib footprint PER NET CLASS -- each holds only that class's
    # lands, as pads at their real board positions (relative to the die centre),
    # pad designators = land ids. Altium allows one footprint per component, so
    # these pair 1:1 with the per-net-class SchLib components of the same name.
    # Because every footprint keeps absolute board coordinates, placing all of
    # them at the same origin reconstructs the full land pattern.
    # UNVERIFIED against a live Altium.
    classes = group_by_netclass(L["probes"])
    cx, cy = L["cx"], L["cy"]
    PAD = PROBE_PAD_SIDE / 1000.0
    HOLE = VIA_DRILL / 1000.0

    def mm(v):
        return f"{v:.4f}"

    o = []
    w = o.append
    w(f"""// Auto-generated by auto_probe_pcb.py -- Altium PCB-library DelphiScript.
// Open a PCB library FIRST: File > New > Library > PCB Library, make that
// .PcbLib the ACTIVE document, then File > Run Script... and run
// GenerateFootprint. Builds one footprint per net class; each holds that
// class's probe-card lands (pad designators = land ids) at their real board
// positions relative to the die centre. Place them all at the same origin to
// reconstruct the complete land pattern.

Var
    Lib : IPCB_Library;
    FP  : IPCB_LibComponent;

Procedure AddFPPad(XMM, YMM : Double; Desig : String);
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
    FP.AddPCBObject(Pad);
End;

// --- start a named footprint. Drops any footprint of the same name from a
// --- previous run first, so re-running replaces instead of colliding.
// --- CreateNewComponent is the library-level call used by working scripts;
// --- it is equivalent to PCBServer.CreatePCBLibComp.
Procedure StartFP(Nm : String);
Var Old;
Begin
    Old := Lib.GetComponentByName(Nm);
    If Old <> Nil Then
    Begin
        Lib.RemoveComponent(Old);
        Lib.DeRegisterComponent(Old);
    End;
    FP := Lib.CreateNewComponent;
    FP.Name := Nm;
End;

Procedure EndFP;
Begin
    Lib.RegisterComponent(FP);
    // must SetState_CurrentComponent (not a plain property assign) or the
    // footprint origin / bounding box come out wrong.
    Lib.SetState_CurrentComponent(FP);
End;
""")

    build_calls = []
    for ci, (netclass, members) in enumerate(classes.items()):
        proc = f"BuildFP{ci}"
        build_calls.append(proc)
        w(f"\n// {netclass} -- {len(members)} lands")
        w(f"Procedure {proc};\nBegin")
        w(f"    StartFP({_pas_str(_part_name(netclass))});")
        for pr in members:
            w(f"    AddFPPad({mm((pr['x']-cx)/1000)}, {mm((pr['y']-cy)/1000)}, "
              f"{_pas_str(pr['id'])});")
        w("    EndFP;\nEnd;")

    w("""
Procedure GenerateFootprint;
Begin
    If PCBServer = Nil Then
    Begin
        ShowMessage('PCBServer is nil -- the PCB editor is not loaded.');
        Exit;
    End;
    Lib := PCBServer.GetCurrentPCBLibrary;
    If Lib = Nil Then
    Begin
        ShowMessage('No PCB library open. Open the .PcbLib, click its tab so it ' +
            'is the ACTIVE document, then run this again.');
        Exit;
    End;

    PCBServer.PreProcess;""")
    for proc in build_calls:
        w(f"    {proc};")
    w(f"""    PCBServer.PostProcess;

    Lib.Board.ViewManager_FullUpdate;
    ShowMessage('Built {len(build_calls)} probe-card footprints ' +
        '({len(L["probes"])} pads total). Save the .PcbLib.');
End;""")

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(o))


def write_sch_library_script(path, L):
    # Emit ONE single-part SchLib component PER NET CLASS. Each is a rectangle
    # with one pin per land (designator = land id, name = signal) and its own
    # footprint-model reference to the PcbLib footprint of the same name --
    # Altium allows one footprint per component, which is why these are five
    # separate components rather than one five-part component. Pins map to pads
    # by matching name, so no explicit pin map is needed. Each component gets
    # its own library page, so all are drawn at the origin.
    # UNVERIFIED against a live Altium -- footprint models are the most
    # version-sensitive part of the Sch API.
    classes = group_by_netclass(L["probes"])

    o = []
    w = o.append
    w("""// Auto-generated by auto_probe_pcb.py -- Altium Sch-library DelphiScript.
// Open a schematic library FIRST: File > New > Library > Schematic Library,
// make that .SchLib the ACTIVE document, then File > Run Script... and run
// GenerateSymbol. Builds one single-part component per net class, each
// carrying a footprint-model reference to the PcbLib footprint of the same
// name. Place all of them to cover the whole probe card.

Var
    Lib  : ISch_Lib;
    Comp : ISch_Component;

Procedure AddRect(Xmil, Ymil, Wmil, Hmil : Integer);
Var R;
Begin
    R := SchServer.SchObjectFactory(eRectangle, eCreate_GlobalCopy);
    R.OwnerPartId := 1;
    R.OwnerPartDisplayMode := 0;
    R.Location := Point(MilsToCoord(Xmil), MilsToCoord(Ymil - Hmil));
    R.Corner := Point(MilsToCoord(Xmil + Wmil), MilsToCoord(Ymil));
    R.LineWidth := eSmall;
    R.IsSolid := True;
    R.AreaColor := $00E7FFFF;
    R.Color := $00000080;
    Comp.AddSchObject(R);
End;

Procedure AddPin(Xmil, Ymil : Integer; Desig, Nm : String);
Var Pin;
Begin
    Pin := SchServer.SchObjectFactory(ePin, eCreate_GlobalCopy);
    Pin.OwnerPartId := 1;
    Pin.OwnerPartDisplayMode := 0;
    Pin.Orientation := eRotate0;
    Pin.Location := Point(MilsToCoord(Xmil - """ + str(SCH_PIN_LENGTH) + """), MilsToCoord(Ymil));
    Pin.PinLength := MilsToCoord(""" + str(SCH_PIN_LENGTH) + """);
    Pin.Designator := Desig;
    Pin.Name := Nm;
    Pin.ShowName := True;
    Pin.ShowDesignator := True;
    Pin.Electrical := eElectricPassive;
    Comp.AddSchObject(Pin);
End;

Procedure StartComp(LibRef, Descr : String);
Begin
    Comp := SchServer.SchObjectFactory(eSchComponent, eCreate_GlobalCopy);
    Comp.LibReference := LibRef;
    Comp.ComponentDescription := Descr;
    Comp.Designator.Text := 'PC?';
    Comp.PartCount := 1;
    Comp.CurrentPartID := 1;
    Comp.DisplayMode := 0;
End;

// --- attach the matching PcbLib footprint and file the component away.
Procedure EndComp(FpName : String);
Var Impl;
Begin
    Impl := Comp.AddSchImplementation;
    Impl.ModelName := FpName;
    Impl.ModelType := 'PCBLIB';
    Impl.IsCurrent := True;
    Lib.AddSchComponent(Comp);
    Lib.CurrentSchComponent := Comp;
    Comp.GraphicallyInvalidate;
End;
""")

    build_calls = []
    for ci, (netclass, members) in enumerate(classes.items()):
        height = (len(members) + 1) * SCH_PIN_PITCH
        nm = _part_name(netclass)
        proc = f"BuildComp{ci}"
        build_calls.append(proc)
        w(f"\n// {netclass} -- {len(members)} lands")
        w(f"Procedure {proc};\nBegin")
        w(f"    StartComp({_pas_str(nm)}, "
          f"{_pas_str('Probe card lands -- ' + netclass)});")
        w(f"    AddRect(0, 0, {SCH_BLOCK_WIDTH}, {height});")
        for k, pr in enumerate(members):
            py = -(k + 1) * SCH_PIN_PITCH
            sig = pr["die"]["signal"] or pr["id"]
            w(f"    AddPin(0, {py}, {_pas_str(pr['id'])}, {_pas_str(sig)});")
        w(f"    EndComp({_pas_str(nm)});\nEnd;")

    # NOTE: Lib.AddSchComponent always ADDs -- it never replaces a same-named
    # component. Re-running into a library that already holds these components
    # piles copies under the same names (overlapping rectangles). Delete the
    # existing ProbeCard_* components in the SCH Library panel before re-running.
    w("""
Procedure GenerateSymbol;
Begin
    If SchServer = Nil Then
    Begin
        ShowMessage('SchServer is nil -- the schematic editor is not loaded.');
        Exit;
    End;
    Lib := SchServer.GetCurrentSchDocument;
    If Lib = Nil Then
    Begin
        ShowMessage('No schematic library open. Create one first: ' +
            'File > New > Library > Schematic Library, make it active, then run again.');
        Exit;
    End;""")
    for proc in build_calls:
        w(f"    {proc};")
    w(f"""    ShowMessage('Built {len(build_calls)} probe-card components ' +
        '({len(L["probes"])} pins total). Save the .SchLib.');
End;""")

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(o))
    return classes


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

    counts = {e: len(v) for e, v in L["edges"].items()}
    print(f"Die {DIE_X} x {DIE_Y} um, centre ({L['cx']:.1f}, {L['cy']:.1f})")
    print(f"Per-edge pad counts: {counts}")
    print(f"Land ring = keep-out x{L['scale']:.2f} -> "
          f"{L['scale']*KEEP_OUT_WIDTH/1000:.1f} x "
          f"{L['scale']*KEEP_OUT_HEIGHT/1000:.1f} mm")
    MW, MH = marker_dims()
    print(f"Keep-out {MW/1000:.1f} x {MH/1000:.1f} mm; inner-row land clears it by "
          f"X {L['scale']*KEEP_OUT_WIDTH/2 - MW/2:.0f} um, "
          f"Y {L['scale']*KEEP_OUT_HEIGHT/2 - MH/2:.0f} um")
    apr = aperture_radius()
    if min(MW, MH) / 2.0 < apr:
        print(f"  WARNING: keep-out half-extent {min(MW, MH)/2:.0f} um is inside the "
              f"{apr:.0f} um aperture radius -- the marker crosses the cutout.")
    check_fit(L)

    map_path = OUTPUT_DIR / f"{csv_path.stem}_wiring_map.csv"
    write_wiring_map(map_path, L)
    print(f"Wrote wiring map:   {map_path}")

    pas_path = OUTPUT_DIR / f"{csv_path.stem}_probe_card.pas"
    write_altium_script(pas_path, L)
    print(f"Wrote Altium script:{pas_path}")

    fp_path = OUTPUT_DIR / f"{csv_path.stem}_probecard_footprint.pas"
    write_pcb_library_script(fp_path, L)
    print(f"Wrote PCB-lib footprint: {fp_path}")

    sym_path = OUTPUT_DIR / f"{csv_path.stem}_probecard_symbol.pas"
    classes = write_sch_library_script(sym_path, L)
    print(f"Wrote Sch-lib symbol:    {sym_path}")
    cc = {k: len(v) for k, v in classes.items()}
    print(f"Net-class parts ({len(cc)}): {cc}")

    print(f"{len(pads)} die pads / {len(L['probes'])} probe lands "
          f"({VIA_DRILL} um dia drill, {PROBE_PAD_SIDE} um land).")


if __name__ == "__main__":
    main()
