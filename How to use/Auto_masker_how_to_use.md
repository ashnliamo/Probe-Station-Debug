PROBE-CARD CONTACT-TEST MASK GENERATOR

This project has two scripts that work as a pair:

  1. io_pair_wiring_parallel.py  -- GENERATES the GDS test-chip mask from a
     pinout.
  2. find_missing_probes.py      -- DECODES a parallel resistance reading from a
     fabricated chip to tell you which probes on the probe card failed to make
     contact.

WHAT THE TEST CHIP DOES

Each INPUT pad on the chip is wired through a resistor of a known, unique value
to a shared central node, which is then wired to all the output pads. When
you land the whole probe card and measure the resistance from the inputs to the
output, you are reading all the input resistors in parallel. The single parallel number tells you exactly which probes are
touching and which are open.

The resistor values are chosen as a binary ladder: 1x, 2x, 4x, 8x, and so on.
COIL_BASE_R is the smallest resistor and each rung doubles from there. Probes add
in CONDUCTANCE, and doubling makes every subset sum distinct, so each pattern of
landed and open probes reads as its own number.

Watch the die size as you raise MAX_BINARY_INPUTS. Doubling every rung means the
biggest resistor is 2 to the power n-1 times the smallest, so an 8-input coupon
needs a 128x resistor and a very long trace. The program warns when the ladder
forces a large resistor or a large die.

The program also prints the worst coupon's decode margin on every run and warns if
it drops below 0.1 percent. That margin is the fraction of full scale your meter
has to resolve to tell every pattern apart, so it is the number to watch.

ONE-TIME SETUP

Install Python 3, version 3.10 or newer.
Install the one dependency, gdstk.


FOLDERS

inputs/         your pinout CSV goes here      -- input to script 1
outputs/        script 1 writes results here
decode_inputs/  the CSV the decoder reads      -- input to script 2
tests/          self-checks, optional

============================================================================
SCRIPT 1 -- GENERATE THE MASKS   io_pair_wiring_parallel.py
============================================================================

STEP 1A:  Prepare the pinout

Put your probe-card pinout in the inputs folder. Any single .csv or .xlsx file
works -- for a workbook the first sheet holding the X and Y header is used, and an
open-in-Excel lock file starting with ~$ is ignored.

Append a column named "I/O" after the Net Class column. Column names are
case-insensitive and extra columns are ignored. Tag each pad:

INPUTn      this pad is an input  in group n
OUTPUTn     this pad is an output in group n
OUTPUT      a common output, shared by every group and present on every chip
OUTPUTSPLIT an output on every chip, halved across the chip's two layers
INPUTSINGLE an input on the shorted continuity coupon
OUTPUTSINGLE the return on the shorted continuity coupon

A bare OUTPUT tag, with no number, marks a common output. It is added to every
group, so it appears on every chip. When a common OUTPUT is present the generator
puts one group per chip, so two groups can never tie their shared output together.

An OUTPUTSPLIT tag also puts the pad on every chip, but instead of joining every
group it is halved across the chip's two layers -- half of the OUTPUTSPLIT pads
wire to the top-layer group, the other half to the bottom-layer group. No pad is
shared between the two layers, so the two groups stay independent and you keep two
layers per chip. This gives each layer extra, redundant output taps so a coupon is
still readable if some output probes miss contact. Pads are alternated in list
order -- 1st, 3rd, 5th to the top layer, 2nd, 4th, 6th to the bottom -- so listing
them in position order spreads each layer's half across the die. This assumes the
OUTPUTSPLIT pads are independently routed, NOT shorted together externally. Do not
combine OUTPUTSPLIT with a bare OUTPUT, since the bare OUTPUT forces one layer per
chip and there is then nothing to halve across.

INPUTSINGLE and OUTPUTSINGLE build a separate continuity coupon on its own layer.
Use this when your input signals are NOT shorted together externally, so a parallel
resistance reading is impossible. On that layer every INPUTSINGLE pad and its return
are shorted straight to one plane, with no resistors. You then test each INPUTSINGLE
probe on its own, measuring continuity to the shared return: a landed probe reads a
short, a missing probe reads open. Because the inputs are independent you measure one
at a time, so each is resolved individually.

The single coupon lives on one layer, kept apart from the parallel coupons, but it can
share a chip with a parallel coupon on the other layer. Its return can be OUTPUTSINGLE
pads, the OUTPUTSPLIT half that lands on the single layer, or both. So INPUTSINGLE must
be paired with OUTPUTSINGLE or OUTPUTSPLIT, and if neither is present the tool stops
with an error. OUTPUTSINGLE only pairs with INPUTSINGLE, and it too errors if there are
no INPUTSINGLE pads. In the results CSV these rows show a resistance of 0 and are marked
continuity to set them apart from the parallel-decode rows.

INPUTSINGLE and OUTPUTSINGLE build a continuity coupon for input signals that are
NOT shorted together externally, so a parallel resistance measurement is not
possible. All INPUTSINGLE and OUTPUTSINGLE pads are shorted together on one layer,
with no resistors. Because the signals are independent, you test each INPUTSINGLE
one at a time -- measure continuity from that pin to any OUTPUTSINGLE return, with
the other pins left floating. Landed reads near zero ohm, open reads infinite. The
OUTPUTSINGLE pads are the shared return, so use two or more for redundancy in case
a return probe misses. This coupon lives on its own layer but can share a chip with
a normal parallel group, so a two-layer chip can carry one parallel group and the
continuity coupon. You must supply at least one INPUTSINGLE and at least one
OUTPUTSINGLE together, or the program stops with an error.

STEP 1B:  Run the program

It will ask how many metal layers per chip:

1 = one IO group per chip    -- single metal layer, no vias
2 = two IO groups per chip   -- second group on metal 2, via-stitched

STEP 1C:  What you get, in outputs/

outputs/
  <pinout>_parallel.csv         the decode key

  gds/
    groups_<n>.gds              one GDS per chip; the name lists its group or
                                groups, e.g. groups_3.0_4.gds is group 3 part 0
                                plus group 4
  schematics/
    schematic_<n>.svg           a circuit diagram per chip, one block per layer,
                                showing every pad, resistor, and the output node
  calibration/
    calibration_resistors.gds   a coupon of big, easy-to-probe resistors at the
                                same ladder values, for measuring real sheet
                                resistance
    calibration_resistors.csv   the theoretical values for that coupon

The console also prints the die size, the resistor-per-edge protrusion, and
how many chips were made.

STEP 1D:  Columns in the key CSV

chip                  which chip and GDS this resistor is on
layer                 which metal layer, 1 or 2
input_pad             the input pad name, the probe you are testing
input_signal          its net name
output_pads           the shared output pads for the group
actual_R_ohm          the resistor's real resistance
total_len_um          length of the resistor trace, microns
group_parallel_R_ohm  resistance with every probe in the group landed

============================================================================
SCRIPT 2 -- DECODE A MEASUREMENT   find_missing_probes.py
============================================================================

STEP 2A:  Give the decoder the key file

Copy the decode key the generator made into the decode_inputs folder. Any single
.csv name works. If you are calibrating, also copy the calibration CSV into the
same folder, see Step 2D. The decoder tells the two apart by their columns, so
both can sit there at once.

STEP 2B:  Take the measurement on the bench

Land the probe card on the chip you want to test. With the whole probe card
landed, measure the group's parallel resistance from its inputs to its output.
Note the chip number, the layer number, and that reading.

STEP 2C:  Run find_missing_probes.py

It will:

  1. List the available chips.
  2. Ask whether to apply calibration offsets, see Step 2D. Answer n the first
     time if you just want a quick look.
  3. Ask for a chip number, then a layer number.
  4. Show the resistors on that coupon, then ask for your reading.
  5. Print the result: which probes are NOT in contact, which are, and a decode
     margin telling how accurate your reading must be to be sure. It warns you
     if the answer is ambiguous or the reading looks wrong.

STEP 2D:  Calibration

The real fabricated resistors are never exactly the calculated values because
metal thickness varies. The calibration coupon lets the decoder correct for this
automatically from a CSV:

  1. Probe each resistor on the calibration chip and note its measured value.
  2. Open the generated calibration CSV, outputs/calibration/calibration_resistors.csv,
     and add a column named measured_R_ohm. Fill in your reading on each row.
  3. Copy that CSV into decode_inputs, alongside the decode key.
  4. Run the decoder. It finds the calibration CSV, computes a per-rung scale of
     measured divided by calculated, and multiplies every prediction by the scale
     of its nearest rung. No typing is needed, and it is reused for the whole
     session.

If no calibration CSV with measured values is present, the decoder instead asks
you to type each measured value by hand.

TUNING

The design knobs live at the top of io_pair_wiring_parallel.py:

PAD_SIZE            pad size in microns
WIRE_WIDTH          resistor trace width
COIL_GAP            gap from the pad to the start of its resistor
COIL_BASE_R         resistance of the smallest resistor in a group
MAX_BINARY_INPUTS   groups bigger than this are split across more chips
