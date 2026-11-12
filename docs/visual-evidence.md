# Visual evidence and generated previews

KiCad JLCPCB Interface publishes UI previews as generated evidence. The screenshots and GIF come from the production overview and selected-component Tk windows; symbol drawings are exported by KiCad CLI. The fixture replaces only the schematic/catalogue data boundary so captures remain deterministic and do not depend on network availability or a local project. Product photos are not drawn or synthesized.

## Current previews

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-workflow.gif" alt="Schematic overview with component list, filter and selected state" width="900"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-initial.png" alt="Schematic overview including estimated cost and stock coverage" width="49%"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-properties-selected.png" alt="Selected-component properties window with JLCPCB catalogue details" width="49%"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/jlcpcb-resistor-symbol.svg" alt="Resistor symbol exported by KiCad CLI from the Device symbol library" width="240"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/jlcpcb-capacitor-symbol.svg" alt="Capacitor symbol exported by KiCad CLI from the Device symbol library" width="240"></p>

The fixture catalogue values are illustrative and do not represent live availability or a JLCPCB quote. The capture deliberately provides no product image, so the UI shows its no-image placeholder rather than fabricated package artwork. In a live session, a catalogue image appears only when the configured provider supplies one.

## Generation and validation

The visual-docs CI job installs the KiCad development CLI, Tk, Pillow and Xvfb. It opens the real production overview and selected-component properties windows with a fixture schematic and fixture catalogue provider, captures the initial, filtered and selected states, creates an animated GIF from the overview captures, and exports resistor/capacitor symbol SVGs through the native KiCad CLI.

The generated bundle contains an exact asset inventory and SHA-256 hashes tied to the checked-out source commit. Pull-request CI uploads the bundle as a workflow artifact for review. Successful pushes to main publish the verified files to the separate docs/previews branch. The provenance JSON records the source commit, workflow run and asset hashes. The publisher verifies the remote branch head after pushing.

To reproduce the capture locally on Ubuntu:

    sudo apt-get install kicad-nightly kicad-nightly-symbols python3-tk python3-pil python3-pil.imagetk xvfb xauth
    xvfb-run -a -s "-screen 0 1920x1500x24" \
      python3 tests/capture_visual_docs.py --output-dir artifacts/visual-docs

The script also accepts KICAD_CLI and KICAD_SYMBOL_DIR to select explicit KiCad executable/library paths. Generated files are disposable outputs; change the fixture or capture generator and regenerate them instead of editing preview images by hand.

## Evidence boundary

The screenshots demonstrate the overview layout, deterministic filtering, selected-component properties layout and catalogue detail rendering. They do not prove live JLCPCB connectivity, live Eeschema IPC selection, or the accuracy of catalogue stock/prices. Those require separate integration tests against KiCad and the live provider.
