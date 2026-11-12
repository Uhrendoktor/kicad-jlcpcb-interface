# KiCad JLCPCB Interface

A KiCad 11 Schematic Editor IPC add-on for reviewing JLCPCB/LCSC catalogue information for a selected symbol or an entire schematic.

## Visual previews

The previews below are captured from the production component overview and selected-component properties window; symbol drawings are rendered by KiCad's own CLI. Catalogue values are deterministic documentation fixtures, not live supplier data. No package artwork is synthesized: the image card stays empty when the catalogue provides no image.

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-workflow.gif" alt="The schematic component overview with part search, cost estimate and stock coverage" width="900"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-properties-selected.png" alt="JLCPCB catalogue properties for the selected STM32 symbol" width="720"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/jlcpcb-resistor-symbol.svg" alt="KiCad-native resistor symbol preview" width="230"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/jlcpcb-capacitor-symbol.svg" alt="KiCad-native capacitor symbol preview" width="230"></p>

See [Visual evidence and generated previews](docs/visual-evidence.md) for the capture fixture, reproduction command and provenance checks.


**Status: early prototype.** The add-on provides two schematic actions. **JLCPCB Component Properties** opens a focused window for exactly one selected symbol carrying the configurable `LCSC` custom property. **JLCPCB Component Overview** lists all linked symbols, supports local search, and summarizes estimated design cost and catalogue stock coverage. The properties action is a separate window; KiCad's documented IPC plugin API does not expose extension tabs inside the built-in Symbol Properties dialog. Live product details are retrieved from JLCPCB's public parts-page endpoint (an undocumented website endpoint, not its approved partner API), with a community catalogue fallback. Stock/prices are indicative catalogue snapshots, not guaranteed real-time quotes. Catalogue images are shown only when supplied by the provider. Footprint/3D previews and continuous editor synchronization remain future work; cross-sheet navigation uses the KiCad 11 Schematic IPC `FocusOnItems` command.

## Intended workflow

1. Add an `LCSC` custom field to a symbol and set it to an LCSC code (for example `C123456`) or a URL containing that code.
2. To inspect one part, select exactly one linked symbol in Eeschema and launch **JLCPCB Component Properties**. The action opens a focused, property-window-style details view for that symbol.
3. To inspect the design, launch **JLCPCB Component Overview**. Search by reference, value, LCSC code or footprint; the overview groups repeated LCSC codes, estimates component cost from unit price × placed quantity, and reports which catalogue stock levels cover the schematic quantity.
4. In either view, review manufacturer, MPN, package, attributes, catalogue stock/price and source freshness. Copy the part ID or open the product page/datasheet. A catalogue image is displayed only when the data source supplies one.
5. From the overview, select a schematic symbol or all matching instances of the same part on its sheet. KiCad's `FocusOnItems` command navigates to the correct sheet, zooms to the symbol(s), and applies the editor selection.

The part-link field name is editable in the browser, so projects using another property name (for example `JLCPCB` or `LCSC Part Number`) do not need to rename their fields.

## Architecture

- `plugin/kicad-jlcpcb-interface/plugin.json`: KiCad IPC plugin manifest and schematic action registration.
- `plugin/kicad-jlcpcb-interface/main.py`: full-schematic overview action entrypoint.
- `plugin/kicad-jlcpcb-interface/component_properties.py`: selected-symbol properties action entrypoint.
- `plugin/kicad-jlcpcb-interface/kicad_jlcpcb/`: schematic scanning, component model, browser UI and cached catalogue provider.
- `plugin/kicad-jlcpcb-interface/requirements.txt`: ABI-specific binary wheels for the official `kicad-python` IPC bindings.
- `scripts/install_plugin.py`: validates and atomically installs the package into a KiCad version's user plugin directory.
- `tests/`: unit and package/installer tests that do not require a running KiCad process.

The IPC bootstrap follows the working pattern in [kicad-blocks](https://github.com/Uhrendoktor/kicad-blocks): KiCad launches the action as an external Python process, reads `plugin.json`, and manages the plugin's isolated Python environment. This add-on uses its own reverse-DNS ID: `org.uhrendoktor.kicadjlcpcb`.

## Current target

The component cache is stored below the plugin-specific directory returned by KiCad's `get_plugin_settings_path()` API. Catalogue-image downloads are HTTPS-only and capped at 5 MiB; catalogue HTTP calls, image loading and schematic-wide summary lookups run outside the Tk UI thread. The `JlcSearchProvider` prefers the public JLCPCB parts-page detail endpoint, then falls back to the community [jlcsearch](https://jlcsearch.tscircuit.com) JSON API, and marks old cached records as stale. The JLCPCB endpoint is undocumented and may change; it is not the official partner API. No credentials are embedded.

The dependency wheels are pinned to the KiCad Python bindings commit recorded in `plugin/kicad-jlcpcb-interface/kicad-python.lock`. For the initial Linux x86_64 target, the requirements reuse the immutable CPython 3.12/3.13/3.14 wheels published by the kicad-blocks project. The plugin manager installs binary wheels; it will not build the GitLab source dependency itself. Pillow is included for decoding catalogue-supplied images shown in the browser.

The initial installer supports:
- KiCad 10.99 development nightly (the KiCad 11 pre-release directory), or KiCad 11.0+.
- Linux x86_64.
- CPython 3.12, 3.13 or 3.14, with Tkinter available.

Other operating systems and architectures need matching `kicad-python` wheels and installation/CI validation before they can be advertised as supported.

## Install

1. In KiCad, enable the IPC API in **Preferences → Plugins** and configure the Python interpreter to a supported CPython 3.12–3.14 executable.
2. Close KiCad.
3. From the repository root, run the installer for your KiCad version directory, for example:

   ```bash
   python3.14 scripts/install_plugin.py --version 10.99
   ```

   The installer defaults to `KICAD_DOCUMENTS_HOME`, or `~/.local/share` on Linux. Override the root with `--documents-home PATH`. Use `--dry-run` to validate the package and print the destination without installing it.
4. Restart KiCad and launch **JLCPCB Component Properties** or **JLCPCB Component Overview** from Eeschema.

The plugin directory is installed to:

```
<KiCad user-data root>/kicad/<version>/plugins/org.uhrendoktor.kicadjlcpcb/
```

KiCad creates the managed environment and installs the dependencies on first discovery. Do not separately pip-install the plugin requirements into that environment. To recreate a failed environment, right-click the add-on action in Preferences → Plugins and choose **Recreate Plugin Environment**.

## Development

Run the checks from the repository root:

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests scripts
ruff check plugin tests scripts
```

Unit tests use fake schematic objects. A live integration test against the targeted KiCad nightly is still required before calling the UI/selection behavior release-ready.

## Planned milestones

- [x] IPC plugin scaffold, action manifest and installer.
- [x] Scan symbol custom fields and list matching schematic symbols.
- [x] Filter/search by reference, value, part ID and footprint.
- [x] Select a symbol or same-part instances on the target sheet, including child sheets.
- [ ] Verify registration, full-hierarchy scanning and cross-sheet selection against a live KiCad 10.99 nightly GUI.
- [x] JLCPCB data-provider abstraction, exact lookup and SQLite cache/offline fallback.
- [x] Catalogue-image card, manufacturer/package/attribute summary, stock/price and source freshness (no synthetic image fallback).
- [x] Selected-symbol properties action and schematic-wide estimated-cost/stock-coverage summary.
- [ ] Footprint and 3D model previews.
- [x] Catalogue stock/pricing display, clearly labelled as indicative and source-dated.
- [x] Cross-sheet navigation and selection through KiCad's `FocusOnItems` command.
- [ ] Continuous synchronization when the user changes sheets or selection while the browser remains open.
- [ ] Live KiCad nightly GUI integration test and reproducible release package.

## License

MIT. See [LICENSE](LICENSE).
