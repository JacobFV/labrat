# Print: build plates for a Creality Ender-3 V3 SE

Every printed part of the bench, packed onto 220 × 220 plates (5 mm between parts) for an Ender-3 V3 SE with a 0.4 mm
nozzle. This is the only place plates are built. The parts come from the generated `../*/stl/` folders, which already
hold each part in its print orientation. Nothing here is rotated off its bed face. Parts are only turned 90° about Z to pack them.

- `plate_XX_*.3mf`: one object per part, at its place on the bed. Open it in Cura (or any slicer) and slice it there.
- `plate_XX_*.gcode`: sliced with the CuraEngine 5.13.0 bundled in Cura, using the `creality_ender3v3se` definition,
  the 0.4 nozzle and the Creality "standard" quality: 0.2 mm layers (first layer too), 20 % infill, a 2-line skirt
  3 mm out. The per-part walls and infill below come from the part docs. The G-code is git-ignored because
  plate 01 is about 100 MB. Regenerate it with the scripts.

| plate | parts (qty) | material | supports | est. time | filament |
|---|---|---|---|---|---|
| `plate_01_pla_toolhead_deck` | DECK-001 beaker matrix, SYR-100 head body, SYR-103 carriage + rack, SYR-112 G4/G5, TM-110 microscope module, TM-120 probe bracket, SYR-105 thumb-disc cap (1 each) | PLA | none | 22 h 13 m | 80 m, ~240 g |
| `plate_02_pla_toolhead_deck` | SYR-101 gear plate 2, SYR-111 G2/G3, SYR-102 flange clamp, TM-122 probe arm, TM-121 probe lid, SYR-110 G1 (1 each) | PLA | none | 2 h 44 m | 8.5 m, ~25 g |
| `plate_03_petg_stand_base` | PS-100 stand base (1) | PETG | none | 8 h 27 m | 51 m, ~155 g |
| `plate_04_petg_stand` | PS-101 base lid, PS-120 yoke, PS-131 clamp jaw **(2)**, PS-132 pinion, PS-133 idler spacer | PETG | none | 9 h 47 m | 47 m, ~143 g |
| `plate_05_petg_stand` | PS-110 pan housing, PS-111 retainer ring, PS-130 cradle (1 each) | PETG | none | 8 h 07 m | 41 m, ~124 g |
| `plate_06_pla_so101_follower_x2` | one SO-101 follower arm: all 11 parts, with the WaveShare mounting plate. **Print it twice** (`so_a`, `so_b`) | PLA+ | everywhere, 45° | 37 h 06 m each | 115 m, ~340 g each |

The times are CuraEngine estimates at the V3 SE profile's speeds. The filament weights assume 1.75 mm filament at
1.24 g/cm³ for PLA and 1.27 g/cm³ for PETG. The total is about 125 h and 1.37 kg (PLA ~0.95 kg, PETG ~0.42 kg),
with the SO-101 plate counted twice.

## Materials and settings

- **Tool head (SYR-1xx, TM-1xx) and deck: PLA.** The tool-head README says "PLA (or PETG)", and the rack guide
  relies on PLA sliding on PLA. The BOM still labels the parts `petg_print`. To print them in PETG, change the
  material in Cura and keep both SYR-100 and SYR-103 in the same material. The deck docs give no material, so it
  uses the default, PLA. Slicing settings: 205 °C nozzle, 60 °C bed, 6 walls on SYR-101, SYR-103, SYR-110, SYR-111
  and SYR-112, and 2 walls elsewhere (profile default). The BOM asks for 25 % gyroid on the body; these plates use
  20 % cubic.
- **Phone stand (PS-1xx): PETG**, per its BOM. Slicing settings: 235 °C nozzle, 75 °C bed, fan 50 %. Walls are 4,
  and 5 on PS-110 and PS-120. Infill is 15 % on PS-100, 25 % on PS-120 and **100 % on the PS-131 jaws and the PS-132
  pinion**.
- **SO-101 follower: PLA+**, per upstream: 210 °C nozzle, 60 °C bed, supports everywhere except slopes steeper than
  45°. The plate is the upstream pre-oriented `STL/SO101/Follower/Ender_Follower_SO101.stl` at the pinned commit
  `a758567`. It is not in the repo, because `hardware/so101/stl/` holds the parts in their design frame, not their
  print orientation. Upstream says **no supports in horizontal screw holes**, and the CLI slice can't block them.
  Before printing, open the 3MF in Cura and paint support blockers into the screw holes, or remove the supports
  from those holes afterwards. Print the STS3215 fit gauge first (see `hardware/so101/README.md`).

## Orientation and notes

- Every tool-head, deck and phone-stand part is support-free in this orientation. The tool-head and phone-stand
  builds check this.
- **Plate 01** puts the head body (153 mm tall, upright on its plug) on the same plate as the deck. Together they
  take 22 h. If you don't want to risk the deck on a long print, move SYR-100 to its own plate in Cura. The
  tool-head README prints it alone.
- **Plate 03 and plate 06 have no skirt** (`adhesion_type none`). PS-100 is 164 × 212 mm and the SO-101 plate is
  216 × 215 mm, so a skirt would not fit on the bed. The start G-code's purge lines at X −3/−2 prime the nozzle.
  The SO-101 supports reach to within 0.5 mm of the bed edges, so level the corners.
- PS-120 yoke (147 mm tall): add a brim in Cura if your bed adhesion is marginal. The phone-stand README allows an
  optional brim on the yoke.
- Parts that are not printed: SYR-104 (turned POM), SYR-130 spacers (brass tube), SYR-311 electrode (rod) and
  PS-134 sleeve (brass tube). The SO-101 leader-only parts are left out because the bench uses two follower builds.
- Before you print gears, bearing bores and insert holes, print a tolerance coupon. The tool-head README covers this
  under "FDM tolerances".

## Regenerate

```bash
cd hardware/print
W=/tmp/plates; mkdir -p $W
curl -L -o $W/Ender_Follower_SO101.stl \
  https://raw.githubusercontent.com/TheRobotStudio/SO-ARM100/a758567c3978dfeefe282ede0500085a48fe8f78/STL/SO101/Follower/Ender_Follower_SO101.stl
uv run --with trimesh --with numpy --with lxml --with networkx python pack_plates.py . $W/Ender_Follower_SO101.stl $W
python3 slice_plates.py $W .        # needs ~/Applications/cura-extract/squashfs-root (the extracted Cura AppImage)
```

`pack_plates.py` reads the parts from `hardware/*/stl/` and writes the `.3mf` plates here; `slice_plates.py` writes
the G-code here (git-ignored). The CuraEngine CLI only reads each setting's `default_value`. It does not evaluate
Cura's `value` formulas, so `slice_plates.py` evaluates the whole definition stack itself and passes every setting
with `-s`. On an aarch64 host the x86_64 engine runs under qemu-user; the slowest plate takes about 15 minutes.
