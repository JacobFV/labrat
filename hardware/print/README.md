# Print: build plates for a Creality Ender-3 V3 SE

Every printed part of the bench, packed onto 220 × 220 plates (5 mm between parts) for an Ender-3 V3 SE with a 0.4 mm
nozzle. This is the only place plates are built. The parts come from the generated `../*/stl/` folders, which already
hold each part in its print orientation. Nothing here is rotated off its bed face. Parts are only turned 90° about Z to pack them.

- `plate_XX_*.3mf`: one object per part, at its place on the bed. Open it in Cura (or any slicer) and slice it there.
- `plate_XX_*.gcode`: sliced with the CuraEngine 5.13.0 bundled in Cura, using the `creality_ender3v3se` definition,
  the 0.4 nozzle and the Creality "standard" quality: 0.2 mm layers (first layer too), 20 % infill, a 2-line skirt
  3 mm out. The per-part walls and infill below come from the part docs. The G-code is git-ignored because
  plate 01 is about 60 MB. Regenerate it with the scripts.

| plate | parts (qty) | material | supports | est. time | filament |
|---|---|---|---|---|---|
| `plate_01_pla_toolhead_deck` | DECK-001 beaker matrix, SYR-100 head body, SYR-103 carriage + rack, SYR-112 G4/G5, TM-110 microscope module, TM-120 probe bracket, SYR-105 thumb-disc cap (1 each) | PLA | none | 17 h 18 m | 62 m, ~184 g |
| `plate_02_pla_toolhead_deck` | SYR-101 gear plate 2, SYR-111 G2/G3, SYR-102 flange clamp, TM-122 probe arm, TM-121 probe lid, SYR-110 G1 (1 each) | PLA | none | 2 h 44 m | 8.5 m, ~25 g |
| `plate_03_petg_stand_base` | PS-100 stand base (1) | PETG | none | 7 h 24 m | 43 m, ~133 g |
| `plate_04_petg_stand` | PS-101 base lid, PS-120 yoke, PS-131 clamp jaw **(2)**, PS-132 pinion, PS-133 idler spacer | PETG | none | 9 h 47 m | 47 m, ~143 g |
| `plate_05_petg_stand` | PS-110 pan housing, PS-111 retainer ring, PS-130 cradle (1 each) | PETG | none | 8 h 07 m | 41 m, ~124 g |

The times are CuraEngine estimates at the V3 SE profile's speeds. The filament weights assume 1.75 mm filament at
1.24 g/cm³ for PLA and 1.27 g/cm³ for PETG. The total is about 45 h and 0.61 kg (PLA ~0.21 kg, PETG ~0.40 kg).
The SO-101 arms are already built and are not printed here ([`../so101`](../so101)).

## Materials and settings

- **Tool head (SYR-1xx, TM-1xx) and deck: PLA.** The tool-head README says "PLA (or PETG)", and the rack guide
  relies on PLA sliding on PLA. The BOM still labels the parts `petg_print`. To print them in PETG, change the
  material in Cura and keep both SYR-100 and SYR-103 in the same material. The deck docs give no material, so it
  uses the default, PLA. Slicing settings: 205 °C nozzle, 60 °C bed, 6 walls on SYR-101, SYR-103, SYR-110, SYR-111
  and SYR-112, and 2 walls elsewhere (profile default). The BOM asks for 25 % gyroid on the body; these plates use
  20 % cubic.
- **Phone stand (PS-1xx): PETG**, per its BOM. Slicing settings: 235 °C nozzle, 75 °C bed, fan 50 %. Walls are 4,
  5 on PS-110 and PS-120, and 3 on PS-100. Infill is 10 % on PS-100 (the steel shot gives the weight), 25 % on
  PS-120 and **100 % on the PS-131 jaws and the PS-132 pinion**.

## Orientation and notes

- Every tool-head, deck and phone-stand part is support-free in this orientation. The tool-head and phone-stand
  builds check this.
- **Plate 01** puts the head body (153 mm tall, upright on its plug) on the same plate as the deck. Together they
  take about 17 h. If you don't want to risk the deck on a long print, move SYR-100 to its own plate in Cura. The
  tool-head README prints it alone.
- **Plate 03 has no skirt** (`adhesion_type none`). PS-100 is 164 × 212 mm, so a skirt would not fit on the bed. The
  start G-code's purge lines at X −3/−2 prime the nozzle.
- PS-120 yoke (147 mm tall): add a brim in Cura if your bed adhesion is marginal. The phone-stand README allows an
  optional brim on the yoke.
- Parts that are not printed: SYR-104 (turned POM), SYR-130 spacers (brass tube), SYR-311 electrode (rod) and
  PS-134 sleeve (brass tube).
- Before you print gears, bearing bores and insert holes, print a tolerance coupon. The tool-head README covers this
  under "FDM tolerances".

## Regenerate

```bash
cd hardware/print
W=/tmp/plates; mkdir -p $W
uv run --with trimesh --with numpy --with lxml --with networkx python pack_plates.py . $W
python3 slice_plates.py $W .        # needs ~/Applications/cura-extract/squashfs-root (the extracted Cura AppImage)
```

`pack_plates.py` reads the parts from `hardware/*/stl/` and writes the `.3mf` plates here; `slice_plates.py` writes
the G-code here (git-ignored). The CuraEngine CLI only reads each setting's `default_value`. It does not evaluate
Cura's `value` formulas, so `slice_plates.py` evaluates the whole definition stack itself and passes every setting
with `-s`. On an aarch64 host the x86_64 engine runs under qemu-user; the slowest plate (01) takes about 10 minutes.
