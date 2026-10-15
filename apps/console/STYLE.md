# LabRat UI style — borrowed from JacobFV/phys-0 (Electron "Lab Console")

Taken from `src/apps/electron/src/renderer/styles.css` dark theme; re-implemented, not copied.

- **Fonts**: Inter 400/500/600 (UI), JetBrains Mono 400/500 (numbers, ids, tool chips, pills). Base 12.5px / 1.45.
- **Surfaces**: true-black ladder `#000 / #050505 / #0a0a0a / #101010`, hover `#141414`.
- **Borders**: hairline `#1a1a1a`, stronger `#2a2a2a`, focus `#444`.
- **Text**: `#ededed / #a8a8a8 / #6b6b6b / #404040`.
- **Accent**: pale gold `#f5d76e` (fg `#1a1300`), soft 10% / strong 28% alpha fills for selection.
- **Glow**: mint `rgb(110,231,200)` for "ok"/live states and focus rings; warn `#d8a64f`; danger `#ff5a5a`.
- **Radii**: tiny and technical — 2/3/4px. No pills-shaped buttons.
- **Headings**: section labels are 11px, uppercase, 0.08em tracking, muted (`h2`). Pane headers 36px tall with hairline bottom border.
- **Brand mark**: small pulsing gold dot next to the name.
- **Theme**: the shipped palette is the light variant of the above (tokens on `:root` in style.css); never hard-code colours.
- **Layout**: three columns: experiment list | the selected experiment as a notebook page (tabs: Experiment, Notebook,
  Colour model, Deck) | 3D twin over agent chat. Below 980px it stacks into one scrolling column. Hairline dividers,
  dense information, mono for all measurements.
- **Status chips** (`.st-<status>`): draft red, ready mint, awaiting_approval gold fill, running blue, done neutral,
  failed/aborted red. Plate beakers are colour circles: left half predicted, right half measured; a gold ring marks the
  one closest to the target (dashed = predicted, solid = measured).
- Phone eyes reuse the mint glow (idle), gold (thinking/happy), blue (listening), red (alert).
