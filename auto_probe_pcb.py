import csv
import re
import sys
import math
import pathlib

from kipy import KiCad
board = KiCad().get_board()

HERE = pathlib.Path(__file__).parent
INPUT_DIR = HERE / "auto_probe_pcb_inputs"
OUTPUT_DIR = HERE / "auto_probe_pcb_outputs"
COL_PAD, COL_SIGNAL, COL_X, COL_Y = ("pad", "signal", "x (um)", "y (um)")

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

def find_input_csv():
    """The single spreadsheet in inputs/ (any .csv name). Errors if none or several."""
    csvs = sorted(INPUT_DIR.glob("*.csv"))
    if not csvs:
        raise SystemExit(f"No .csv found in {INPUT_DIR} -- put your pinout there.")
    if len(csvs) > 1:
        names = ", ".join(p.name for p in csvs)
        raise SystemExit(f"Multiple .csv files in {INPUT_DIR} ({names}); keep just one, "
                         f"or pass the path: py io_pair_wiring_parallel.py <file.csv>")
    return csvs[0]

def main():
    csv_path = find_input_csv()
    pads = read_pads(csv_path)
    for p in pads:
        print(p)
    

if __name__ == "__main__":
    main()
