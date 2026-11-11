The references use the original, ungraded photos from the comparison sheets
supplied in the user's NX300M conversation (01a1c341-9d88-7360-8348-6a393cbafbb6).
They were packed as 144 x 84 RGB images on 2026-11-11, in this scene order:

- `lut-reference.png`: portrait holding a cake; source `34_09_before.jpg`
  from LumixLutSearch's `app/src/main/assets/songying/` sample images.
- `lut-reference-street.png`: daylight city buildings; source
  `113_01_before.jpg` from the same sample collection.
- `lut-reference-landscape.png`: blue car, person and clouds; Original panel
  of CXO FilmPack's `docs/images/raw-landscape.jpg` (P1176257.RW2).
  Crop `(0, 58, width/3, height)` removes the comparison header and graded panels.

Source repositories:
https://github.com/MangDao/LumixLutSearch
https://github.com/Macchen666816/CXO-FilmPack

All three use a centered 12:7 crop and Lanczos downsampling, with no added
LUT or color adjustment. They retain their source provenance; this asset
replacement does not reclassify the photographs as project-owned originals.

`lut_reference_rgb.h` contains the PNGs decoded to row-major RGB bytes.
Three images now contain 108,864 RGB bytes, down from 193,536. The menu
redraws the selected reference when the image, table or tuning changes.
These are visual comparison sources, not color calibration targets.

Run `tools/export_ui_assets.py` to rebuild the RGB header from these PNGs.
That exporter also refreshes the camera lens list from NXConnect presets.
