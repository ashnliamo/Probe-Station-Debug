PROBE-CARD CONTACT-TEST MASK GENERATOR

This project has two scripts that work as a pair:

  1. io_pair_wiring_parallel.py  -- GENERATES the GDS test-chip mask from a
     pinout.
  2. find_missing_probes.py      -- DECODES a parallel resistance reading from a
     fabricated chip to tell you which probes on the probe card failed to make
     contact.

WHAT THE TEST CHIP DOES

Each INPUT pad on the chip is wired through a resistor of a known, unique value
to a shared central node, which is then wired to all the output pads. The
resistor values are chosen as a binary ladder: 1x, 2x, 4x, 8x, and so on. When
you land the whole probe card and measure the resistance from the inputs to the
output, you are reading all the input resistors in parallel. The single parallel number tells you exactly which probes are
touching and which are open.

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

STEP 1A:  Prepare the pinout CSV

Put your probe-card pinout in the inputs folder. Any single .csv name works.

Append a column named "I/O" after the Net Class column. Column names are
case-insensitive and extra columns are ignored. Tag each pad:

INPUTn     this pad is an input  in group n
OUTPUTn    this pad is an output in group n
OUTPUT     a common output, shared by every group and present on every chip

A bare OUTPUT tag, with no number, marks a common output. It is added to every
group, so it appears on every chip. When a common OUTPUT is present the generator
puts one group per chip, so two groups can never tie their shared output together.

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
