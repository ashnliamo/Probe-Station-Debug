"""Range tests for auto_probe_pcb.py.

Generates synthetic pinout CSVs across the expected input space and checks the
invariants that define a correct probe-card layout:

  * every die pad -> exactly one land (1:1), only on sides that have pads
  * lands on a side keep die order and never collide (gap >= pad pitch)
  * every land is >= EDGE_CLEARANCE from the die edge
  * the Altium .pas and wiring-map generate with the right element counts

Ranges covered: sides 2-4, pad count 150-300, die 8x5mm down to 5x3mm.

FAIL  = crash or a broken invariant (a real bug).
WARN  = layout is correct but the lands overflow the 4.5" card (a capacity
        limit -- would need 2/4-row lands or a bigger card, not a code bug).

Run:  py auto_probe_pcb_tests.py      (no KiCad needed)
"""

import csv
import pathlib
import tempfile

import auto_probe_pcb as m

EPS = 1.0  # um tolerance


def make_pads(die_w, die_h, sides, total):
    """Synthetic pads: `total` pads spread as evenly as possible over `sides`,
    inset 40 um inside the die edge, within the central 80% of each edge so the
    geometric side-classifier is unambiguous. Die centred at the origin."""
    k = len(sides)
    counts = [total // k] * k
    for i in range(total - sum(counts)):
        counts[i] += 1
    pads = []
    n = 1
    for side, cnt in zip(sides, counts):
        for j in range(cnt):
            t = ((j + 0.5) / cnt - 0.5) * 0.8   # -0.4 .. 0.4 along the edge
            if side in ("T", "B"):
                x = t * die_w
                y = (die_h / 2 - 40) if side == "T" else -(die_h / 2 - 40)
            else:
                y = t * die_h
                x = (die_w / 2 - 40) if side == "R" else -(die_w / 2 - 40)
            pads.append({"name": f"{side}{j+1}", "signal": f"SIG{n}", "x": x, "y": y})
            n += 1
    return pads


def write_csv(path, pads):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Pad", "Signal", "X (um)", "Y (um)"])
        for p in pads:
            w.writerow([p["name"], p["signal"], f"{p['x']:.3f}", f"{p['y']:.3f}"])


def check_case(name, die_w, die_h, sides, total, tmp):
    fails, warns = [], []
    m.DIE_X, m.DIE_Y = die_w, die_h

    csv_path = tmp / f"{name}.csv"
    write_csv(csv_path, make_pads(die_w, die_h, sides, total))

    try:
        pads = m.read_pads(csv_path)
    except Exception as e:
        return [f"read_pads crashed: {e}"], warns
    if len(pads) != total:
        fails.append(f"read {len(pads)} pads, expected {total}")

    try:
        L = m.compute_layout(pads)
    except Exception as e:
        return [f"compute_layout crashed: {e}"], warns

    probes, cx, cy = L["probes"], L["cx"], L["cy"]

    if len(probes) != total:
        fails.append(f"{len(probes)} lands != {total} pads (not 1:1)")
    got = set(p["side"] for p in probes)
    if got != set(sides):
        fails.append(f"sides {sorted(got)} != requested {sorted(sides)}")

    pitch = m.PROBE_PAD_PITCH
    for s in sides:
        sp = [p for p in probes if p["side"] == s]
        axis = [p["x"] if s in ("T", "B") else p["y"] for p in sp]
        if any(axis[i + 1] < axis[i] - EPS for i in range(len(axis) - 1)):
            fails.append(f"side {s}: land order not monotonic")
        tight = [axis[i + 1] - axis[i] for i in range(len(axis) - 1)
                 if axis[i + 1] - axis[i] < pitch - EPS]
        if tight:
            fails.append(f"side {s}: {len(tight)} colliding gaps "
                         f"(min {min(tight):.0f} < {pitch} um)")
        for p in sp:
            clr = (abs(p["y"] - cy) - die_h / 2) if s in ("T", "B") \
                else (abs(p["x"] - cx) - die_w / 2)
            if clr < m.EDGE_CLEARANCE - EPS:
                fails.append(f"side {s}: land only {clr:.0f} um from die "
                             f"(< {m.EDGE_CLEARANCE})")
                break

    # capacity (not a bug): do the lands fit the 4.5" card?
    mr = m.PROBE_PAD_SIDE / 2 + m.MASK_EXPAND
    over = max(max(abs(p["x"] - cx), abs(p["y"] - cy)) for p in probes) + mr \
        - m.CARD_SIZE / 2
    if over > 0:
        warns.append(f"lands exceed 4.5in card by {over/1000:.1f} mm")

    # outputs
    try:
        m.write_wiring_map(tmp / f"{name}_map.csv", L)
        m.write_altium_script(tmp / f"{name}.pas", pads, L)
    except Exception as e:
        fails.append(f"output generation crashed: {e}")
        return fails, warns

    rows = sum(1 for _ in open(tmp / f"{name}_map.csv"))
    if rows != total + 1:
        fails.append(f"wiring map has {rows} rows, expected {total+1}")
    pas = (tmp / f"{name}.pas").read_text()
    if "{" in pas or "}" in pas:
        fails.append("Altium .pas contains stray brace characters")
    for proc, label in (("AddLand(", "lands"), ("AddText(", "labels"),
                        ("AddDiePad(", "die pads"), ("AddProbe(", "probes")):
        c = pas.count(proc)
        if c != total + 1:            # +1 for the procedure definition
            fails.append(f".pas has {c-1} {label} calls, expected {total}")
    return fails, warns


CASES = [
    # name,                 die_w,    die_h,   sides,          pads
    ("4side_8x5_228",       8170.73,  5155.584, "TBLR",        228),
    ("4side_8x5_300",       8170.73,  5155.584, "TBLR",        300),
    ("4side_5x3_150",       5000.0,   3000.0,   "TBLR",        150),
    ("4side_6p5x4_300",     6500.0,   4000.0,   "TBLR",        300),
    ("3side_TBL_8x5_225",   8170.73,  5155.584, "TBL",         225),
    ("3side_TLR_6x4_200",   6000.0,   4000.0,   "TLR",         200),
    ("3side_TBR_5x3_300",   5000.0,   3000.0,   "TBR",         300),
    ("2side_TB_8x5_200",    8170.73,  5155.584, "TB",          200),
    ("2side_LR_8x5_150",    8170.73,  5155.584, "LR",          150),
    ("2side_TR_6x4_250",    6000.0,   4000.0,   "TR",          250),
    ("2side_LR_5x3_300",    5000.0,   3000.0,   "LR",          300),  # stress
    ("2side_TB_5x3_300",    5000.0,   3000.0,   "TB",          300),  # stress
]


def main():
    orig = (m.DIE_X, m.DIE_Y)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="probecard_tests_"))
    n_fail = n_warn = 0
    print(f"scratch: {tmp}\n")
    print(f"{'case':22} {'die(mm)':9} {'sides':5} {'pads':>4}  result")
    print("-" * 70)
    for name, dw, dh, sides, total in CASES:
        fails, warns = check_case(name, dw, dh, list(sides), total, tmp)
        status = "PASS" if not fails else "FAIL"
        if fails:
            n_fail += 1
        if warns:
            n_warn += 1
        note = ""
        if warns and not fails:
            note = "  WARN: " + "; ".join(warns)
        print(f"{name:22} {dw/1000:.1f}x{dh/1000:.1f}  {sides:5} {total:>4}  {status}{note}")
        for msg in fails:
            print(f"    - {msg}")
    m.DIE_X, m.DIE_Y = orig
    print("-" * 70)
    print(f"{len(CASES)} cases: {len(CASES)-n_fail} passed, {n_fail} failed, "
          f"{n_warn} with capacity warnings.")
    print("ALL TESTS PASSED" if n_fail == 0 else "SOME TESTS FAILED")


if __name__ == "__main__":
    main()
