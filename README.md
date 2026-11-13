# KiCad JLCPCB Interface

A KiCad 11 Schematic Editor IPC add-on for reviewing JLCPCB/LCSC catalogue information for a selected symbol or an entire schematic.

## Motivation and current scope

The long-term goal is to make common catalogue parts almost as quick to add as entering an LCSC part number. For a basic part such as a cheap 10 kΩ resistor, the LCSC record should eventually provide the symbol, footprint, value, description, manufacturer part number, datasheet and other useful properties with minimal manual work. For more complex devices, the tool should make conservative suggestions and leave uncertain pin mappings, footprints or properties for the user to complete.

**The current implementation generates reusable project-local symbols for simple passives, but not arbitrary components.** **JLCPCB Add Component from LCSC** reviews the inferred value and supported footprint, clones KiCad's stock two-pin `Device:R` or `Device:C` topology into `JLCPCB_Generated.kicad_sym`, registers it in the project `sym-lib-table`, reloads it in Eeschema, and places an instance linked to the generated library symbol. **JLCPCB Populate Selected Symbol** can still apply fields to an already placed compatible generic passive placeholder. Both require explicit confirmation and leave uncertain values, unsupported packages and complex types for manual completion. This does not infer arbitrary IC pins or generate copper footprints or 3D models.

## Visual previews

The previews are captured from a real KiCad Eeschema session. The demo schematic contains fixed example LCSC/MPN inputs, while catalogue facts, stock, price breaks and the product photograph are fetched live from JLCPCB during capture. The image is the supplier's own product photo; no package artwork or catalogue values are generated.

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-workflow.gif" alt="Live Eeschema workflow: a 10 kΩ resistor before selection, after selection, and with its JLCPCB properties window open" width="900"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-initial.png" alt="Full-width schematic overview with linked parts, design cost, stock coverage and selected resistor catalogue details" width="49%"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-properties-selected.png" alt="JLCPCB catalogue properties for the selected 10 kΩ YAGEO resistor (LCSC C60489)" width="49%"></p>

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/jlcpcb-resistor-symbol.svg" alt="KiCad-native resistor symbol preview" width="230"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/jlcpcb-capacitor-symbol.svg" alt="KiCad-native capacitor symbol preview" width="230"></p>

See [Visual evidence and generated previews](docs/visual-evidence.md) for the capture fixture, reproduction command and provenance checks.


**Status: early prototype.** The add-on provides four schematic actions. **JLCPCB Component Properties** opens a focused window for exactly one selected symbol carrying the configurable `LCSC` custom property. **JLCPCB Component Overview** lists all linked symbols, supports local search, and summarizes estimated design cost and catalogue stock coverage. **JLCPCB Populate Selected Symbol** previews fields inferred from an LCSC record and applies them to one selected compatible placeholder only after confirmation. **JLCPCB Add Component from LCSC** creates and places a reusable project-local symbol cloned from KiCad's known resistor/capacitor topology. The properties action is a separate window; KiCad's documented IPC plugin API does not expose extension tabs inside the built-in Symbol Properties dialog. Catalogue facts are queried from JLCPCB's own parts-page detail endpoint; MPN alternatives are queried from JLCPCB's own parts-search endpoint. These are undocumented website endpoints rather than a supported public components API, and may change. The source/freshness line distinguishes JLCPCB, community fallback and cached data. MPN search displays all purchasable keyword results returned within the paginated search limit, sorting exact normalized-MPN matches first and marking related results separately. The table compares candidate MPN, stock, minimum order quantity, library class and price breaks. Related results are not guaranteed drop-in replacements; compare package and attributes before substituting. Images are downloaded only from supplier-provided HTTPS URLs. Stock/prices remain snapshots, not a guaranteed quote or reservation. Footprint/3D previews and continuous editor synchronization remain future work; cross-sheet navigation uses the KiCad 11 Schematic IPC `FocusOnItems` command.

## Intended workflow

1. To generate a reusable passive from an LCSC number, save the KiCad project, clear the Eeschema selection and launch **JLCPCB Add Component from LCSC**. Enter the LCSC code and X/Y coordinates in millimetres, review the proposed value, supported footprint, datasheet and custom properties, then explicitly confirm. The action creates or extends the project-local `JLCPCB_Generated.kicad_sym` library, registers the `${KIPRJMOD}` table entry and places a linked symbol using the next free R/C reference. When this is the project's first generated-library entry, the running Eeschema session may not know the new table row: the action saves the library and asks you to close/reopen the project, then repeat the same LCSC action. The existing symbol is reused, and the linked instance is placed after KiCad loads the table entry. Later additions can reload and place directly. Automatic generation is disabled when the component type, value or footprint cannot be inferred reliably.
2. To populate an existing generic passive, select exactly one compatible `Device:R` or `Device:C` symbol and launch **JLCPCB Populate Selected Symbol**. Enter an LCSC code, review the proposed fields, then confirm the update. The action does not replace symbol graphics, pins or electrical pin mapping.
3. For catalogue inspection, add an `LCSC` custom field to an existing symbol and set it to an LCSC code (for example `C123456`) or a URL containing that code.
4. To inspect one part, select exactly one linked symbol in Eeschema and launch **JLCPCB Component Properties**. The action opens a focused, property-window-style details view for that symbol.
5. To inspect the design, launch **JLCPCB Component Overview**. Search by reference, value, LCSC code or footprint; the overview groups repeated LCSC codes, estimates component cost from live catalogue unit price × placed quantity, and reports which catalogue stock levels cover the schematic quantity.
6. In the properties view, the supplier image is placed beside the catalogue facts. If the symbol has an MPN custom field (for example `MPN` or `Manufacturer Part Number`), the alternatives table shows all purchasable JLCPCB keyword-search results returned for that MPN. Exact results are marked and shown first; related candidates follow, each with MPN, LCSC code, package, Basic/Extended class, stock or pre-order status, minimum quantity and lowest price break. Double-click a row to open its supplier page. Related candidates must be checked for package and electrical compatibility before substituting.
7. In either view, review manufacturer, MPN, package, attributes, stock, full price breaks and source/freshness. Copy the part ID or open the product page/datasheet.
8. From the overview, select a schematic symbol or all matching instances of the same part on its sheet. KiCad's `FocusOnItems` command navigates to the correct sheet, zooms to the symbol(s), and applies the editor selection.

The part-link field name is editable in the browser, so projects using another property name (for example `JLCPCB` or `LCSC Part Number`) do not need to rename their fields.

## Architecture

- `plugin/kicad-jlcpcb-interface/plugin.json`: KiCad IPC plugin manifest and schematic action registration.
- `plugin/kicad-jlcpcb-interface/main.py`: full-schematic overview action entrypoint.
- `plugin/kicad-jlcpcb-interface/component_properties.py`: selected-symbol properties action entrypoint.
- `plugin/kicad-jlcpcb-interface/populate_component.py` and `kicad_jlcpcb/draft.py`: review-first LCSC field inference and selected-symbol IPC write-back.
- `plugin/kicad-jlcpcb-interface/create_component.py`, `kicad_jlcpcb/placement.py` and `kicad_jlcpcb/library.py`: project-local `.kicad_sym` generation, library registration/reload and linked placement.
- `plugin/kicad-jlcpcb-interface/kicad_jlcpcb/`: schematic scanning, component model, browser UI and cached catalogue provider.
- `plugin/kicad-jlcpcb-interface/requirements.txt`: ABI-specific binary wheels for the official `kicad-python` IPC bindings.
- `scripts/install_plugin.py`: validates and atomically installs the package into a KiCad version's user plugin directory.
- `tests/`: unit and package/installer tests that do not require a running KiCad process.

The IPC bootstrap follows the working pattern in [kicad-blocks](https://github.com/Uhrendoktor/kicad-blocks): KiCad launches the action as an external Python process, reads `plugin.json`, and manages the plugin's isolated Python environment. This add-on uses its own reverse-DNS ID: `org.uhrendoktor.kicadjlcpcb`.

## Current target

The component cache is stored below the plugin-specific directory returned by KiCad's `get_plugin_settings_path()` API. Catalogue-image downloads are HTTPS-only and capped at 5 MiB; catalogue HTTP calls, image loading, exact-MPN alternative searches and schematic-wide summary lookups run outside the Tk UI thread. The provider forces a current lookup when a component is opened, prefers JLCPCB's own detail endpoint, and labels community-search or cached fallbacks explicitly. The MPN alternative table uses JLCPCB's own paginated parts search and filters results by normalized exact MPN. These public website endpoints are undocumented and may change; they are not the official partner API. Signed image URLs are retained only in memory and removed from persistent cache. No credentials are embedded.

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
4. Restart KiCad and launch **JLCPCB Add Component from LCSC**, **JLCPCB Populate Selected Symbol**, **JLCPCB Component Properties** or **JLCPCB Component Overview** from Eeschema.

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

Unit tests use fake schematic objects. The CI visual job also launches KiCad 10.99 Eeschema with a project-local library row already registered, extends and reloads that library from a fresh LCSC record, places an instance linked to the generated ID, verifies native/custom properties by reading the schematic back, exports the generated library symbol through KiCad CLI, and captures live selection/properties windows and supplier data. The first-use reopen/retry behavior is covered by a unit test. Full plugin registration/discovery and cross-sheet GUI navigation still need separate validation before release.

## Planned milestones

- [x] IPC plugin scaffold, action manifest and installer.
- [x] Scan symbol custom fields and list matching schematic symbols.
- [x] Filter/search by reference, value, part ID and footprint.
- [x] Select a symbol or same-part instances on the target sheet, including child sheets.
- [ ] Verify native plugin discovery/registration, full-hierarchy scanning and cross-sheet GUI selection against a live KiCad 10.99 nightly.
- [x] JLCPCB data-provider abstraction, exact lookup and SQLite cache/offline fallback.
- [x] Compact supplier-photo/catalogue detail layout, manufacturer/package/attribute summary, stock and full price breaks.
- [x] JLCPCB MPN-keyword alternatives with exact/related match labels, stock, minimum order and price comparisons.
- [x] Selected-symbol properties action and schematic-wide estimated-cost/stock-coverage summary.
- [x] End-to-end live Eeschema selection-to-properties GIF and supplier-data visual evidence, captured from real KiCad 10.99 IPC and live JLCPCB responses.
- [x] Live fixture verifies IPC-created LCSC/MPN fields remain readable after their display annotations are hidden.
- [x] Populate an already placed compatible resistor/capacitor symbol from an LCSC number, with conservative field inference, review/confirmation and manual completion warnings.
- [x] Place a new stock generic Device:R or Device:C from an LCSC number with supported inferred value/footprint and next free reference.
- [x] Generate project-local resistor/capacitor symbols by cloning KiCad's stock two-pin graphics/electrical topology, adding native fields and hidden LCSC metadata, registering a project symbol library and placing linked instances.
- [ ] Generate arbitrary custom KiCad symbols and footprints from LCSC records, including complex graphics, pin mapping, copper pad geometry and 3D models.
- [ ] Footprint and 3D model previews.
- [x] Catalogue stock/pricing display, clearly labelled as indicative and source-dated.
- [x] Cross-sheet navigation and selection through KiCad's `FocusOnItems` command.
- [ ] Continuous synchronization when the user changes sheets or selection while the browser remains open.
- [ ] Reproducible release package and broader platform/KiCad-version validation.

## License

MIT. See [LICENSE](LICENSE).
