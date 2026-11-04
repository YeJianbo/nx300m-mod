`lut-reference.png` is a 192 x 112 crop of NASA's public-domain portrait of
astronaut Eileen Collins, distributed as `skimage/data/astronaut.png` in
scikit-image v0.24.0. Source:
https://raw.githubusercontent.com/scikit-image/scikit-image/v0.24.0/skimage/data/astronaut.png

`lut_reference_rgb.h` contains the three PNGs decoded to row-major RGB bytes.
The menu redraws the selected reference when the image, table or tuning
changes. This is a software LUT reference, not a sensor or LCD measurement.

`lut-reference-landscape.png`: crop/resize of `china.jpg`, by danielbuechele,
CC BY 2.0 (https://creativecommons.org/licenses/by/2.0/), distributed by
scikit-learn 1.5.2. Original: https://www.flickr.com/photos/danielbuechele/6061409035/
Source: https://raw.githubusercontent.com/scikit-learn/scikit-learn/1.5.2/sklearn/datasets/images/china.jpg

`lut-reference-night.png`: crop/resize of SpaceX's public-domain DSCOVR launch
photo, distributed as scikit-image 0.18.3 `rocket.jpg`.
Original: https://www.flickr.com/photos/spacexphotos/16511594820/
Source: https://raw.githubusercontent.com/scikit-image/scikit-image/v0.18.3/skimage/data/rocket.jpg

Run `tools/export_ui_assets.py` to rebuild the RGB header from these PNGs.
