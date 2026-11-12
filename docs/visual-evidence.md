# Visual evidence and generated previews

KiCad JLCPCB Interface publishes generated visual evidence from a real KiCad Eeschema session. The demo schematic is populated with KiCad library symbols and controlled example LCSC/MPN properties through the live IPC API. Catalogue descriptions, manufacturer, stock, price ladder, MPN alternatives and the product photo are requested from JLCPCB during capture; no supplier data or imagery is hard-coded. Resistor/capacitor symbol drawings are exported by KiCad CLI.

## Current previews

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-workflow.gif" alt="Real schematic before component selection, after component selection, and with the JLCPCB properties window open" width="900"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-initial.png" alt="Schematic overview including estimated cost and stock coverage" width="49%"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-properties-selected.png" alt="Selected-component properties window with JLCPCB catalogue details" width="49%"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/jlcpcb-resistor-symbol.svg" alt="Resistor symbol exported by KiCad CLI from the Device symbol library" width="240"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/jlcpcb-capacitor-symbol.svg" alt="Capacitor symbol exported by KiCad CLI from the Device symbol library" width="240"></p>

The schematic fixture values (reference/value/LCSC/MPN) are controlled test inputs, but all supplier facts shown in the capture are fresh JLCPCB HTTP responses. The visual capture fails if it receives a cached response, a community fallback instead of JLCPCB detail data, no stock/price, no product image, no MPN-search results, or no match for the selected demo MPN. The image card contains a downloaded supplier image, not generated art. These values represent the supplier response at capture time, not a binding quote or a reserved stock allocation.

## Generation and validation

The visual-docs CI job installs KiCad nightly, the matching `kicad-python` wheel, Tk, Pillow, Xvfb and `xdotool`. It enables the Eeschema IPC server in an isolated CI profile, launches the real schematic editor, places library symbols through IPC, adds LCSC/MPN properties, and verifies that the hierarchy scan reads them back. The production overview is rendered with fresh JLCPCB lookups. The GIF contains three full-desktop frames: schematic before selection, schematic after the R1 symbol is selected through IPC, and the schematic with the component-properties window open for that same selected R1. A separate window screenshot shows the supplier photo, catalogue facts and the MPN keyword results, with exact matches separated from related candidates. Capture fails closed when the direct JLCPCB data/image or the live editor interaction is unavailable. Resistor/capacitor symbol SVGs remain native KiCad CLI exports.

The generated bundle contains an exact asset inventory and SHA-256 hashes tied to the checked-out source commit. Pull-request CI uploads the bundle as a workflow artifact for review. Successful pushes to main publish the verified files to the separate docs/previews branch. The provenance JSON records the source commit, workflow run and asset hashes. The publisher verifies the remote branch head after pushing.

To reproduce the capture locally on Ubuntu:

    sudo apt-get install kicad-nightly kicad-nightly-symbols python3.12-venv python3-tk python3-pil python3-pil.imagetk xvfb xauth xdotool
    python3.12 -m venv --system-site-packages .venv-visual
    .venv-visual/bin/pip install -r plugin/kicad-jlcpcb-interface/requirements.txt
    xvfb-run -a -s "-screen 0 1920x1500x24" \
      .venv-visual/bin/python tests/capture_visual_docs.py --output-dir artifacts/visual-docs

The script also accepts KICAD_CLI and KICAD_SYMBOL_DIR to select explicit KiCad executable/library paths. Generated files are disposable outputs; change the fixture or capture generator and regenerate them instead of editing preview images by hand.

## Evidence boundary

The generated screenshots demonstrate a real KiCad selection-to-properties workflow and a catalogue UI populated from live JLCPCB web responses at capture time. Because the supplier endpoints are undocumented, a successful capture verifies only the response shape and values returned on that run; it is not a guarantee of future endpoint stability, stock reservation or a binding price quote.
