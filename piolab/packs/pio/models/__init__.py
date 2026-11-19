"""Models of the PIO pack, one sub-package each (`base`: a single state machine).

The pack finds them with `discover_models("piolab.packs.pio.models")`, so a new variant
is a new folder exporting `Engine` and `HANDLERS`. This is the layout downstream packs
use (`<pkg>/models/<name>/`).
"""
