# User guide

This guide explains the three KiCad Schematic Editor actions, how to link symbols to LCSC records, how catalogue results are interpreted, and what the add-on deliberately does not automate.

<p><strong>On this page:</strong> <a href="#setup">Setup</a> · <a href="#link-a-symbol-to-an-lcsc-record">Link parts</a> · <a href="#jlcpcb-component-overview">Component Overview</a> · <a href="#jlcpcb-component-properties">Component Properties</a> · <a href="#jlcpcb-add-or-update-component">Add or Update Component</a> · <a href="#catalogue-data-cache-and-privacy">Catalogue data</a> · <a href="#troubleshooting">Troubleshooting</a> · <a href="#current-limits">Limits</a> · <a href="#developer-checks">Developer checks</a></p>

## Setup

### Supported environment

The current installer target is:

- KiCad 10.99 development nightly (the KiCad 11 pre-release directory) or KiCad 11.0+.
- Linux x86_64.
- CPython 3.12, 3.13 or 3.14 with Tkinter available.

Other operating systems and CPU architectures are not validated yet. The KiCad IPC API must be enabled and configured to use a compatible Python interpreter.

### Appearance

The browser and component review windows follow the desktop appearance where it can be detected: Windows app theme, macOS appearance, and Linux GTK/GNOME or KDE settings. ttk controls, tables, fields, scrollbars and the review-notes text area share the same palette.

Override the detected appearance by setting `KICAD_JLCPCB_THEME` before starting KiCad:

| Value | Behavior |
| --- | --- |
| `system` | Detect the desktop preference (default). |
| `light` | Force the light palette. |
| `dark` | Force the dark palette. |

For example, on Linux:

```bash
KICAD_JLCPCB_THEME=dark kicad
```

The override is read when each window opens. Restart KiCad after changing the launch environment; no project or schematic settings are modified.

### Install

From the repository root:

1. In KiCad, open **Preferences → Plugins**. Enable the IPC API and select a supported Python interpreter.
2. Close KiCad before installation.
3. Run the installer for the KiCad user-data version directory. For the 10.99 nightly directory:

   ```bash
   python3.14 scripts/install_plugin.py --version 10.99
   ```

   Substitute the KiCad version directory installed on your system (for example, `--version 11.0` for a matching KiCad 11 installation). The installer defaults to `KICAD_DOCUMENTS_HOME`, or `~/.local/share` on Linux. `--documents-home PATH` overrides the user-data root; `--dry-run` validates the package and prints the target without copying files.
4. Restart KiCad. KiCad discovers the IPC add-on and prepares its managed Python environment.

The package is installed at:

   ```text
   <KiCad user-data root>/kicad/<version>/plugins/org.uhrendoktor.kicadjlcpcb/
   ```

Do not pip-install plugin dependencies into KiCad's managed environment by hand. If its environment creation fails, use **Preferences → Plugins → right-click the add-on → Recreate Plugin Environment**.

## Link a symbol to an LCSC record

The browser discovers schematic symbols through a custom property. The default property name is `LCSC`.

For each component to include:

1. Open the symbol's Properties dialog in Eeschema.
2. Add a custom field named `LCSC`.
3. Set its value to an LCSC code such as `C60489`. A URL containing an LCSC code is also accepted.
4. Save the schematic.

The `LCSC` field name is case-insensitive. In the browser, the **Part-link field** entry can be changed to another property name such as `JLCPCB`, `LCSC Part Number` or `JLCPCB Part Number`. The search includes reference, value, part number / stored link, MPN, footprint and sheet path.

An MPN field is optional. When present, the browser can compare JLCPCB keyword-search listings for that manufacturer part number. Recognized field names include `MPN` and common Manufacturer Part Number variants.

## JLCPCB Component Overview

Launch **JLCPCB Component Overview** from Eeschema. It scans the schematic hierarchy and lists every symbol containing the configured part-link property.

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-initial.png" alt="Overview window with linked schematic components, filters, catalogue detail and the project summary" width="680"></p>

### Find a component

- Type in **Search** to filter locally as you enter text. The filter checks reference, value, LCSC/JLCPCB field, normalized part code, MPN, footprint and sheet path.
- Change **Part-link field** if the project uses a custom property name.
- Select a row to load the catalogue information for that part.
- Use **Refresh** to rescan the schematic and refresh the overview.

The component list shows reference, value, part-link value, footprint and sheet. Repeated instances of the same LCSC code remain individual schematic rows.

### Understand the project summary

The summary reports:

- **Linked components** — number of linked schematic symbol instances.
- **Unique parts** — number of distinct part-link codes.
- **Estimated total (USD)** — returned catalogue unit price multiplied by the number of linked instances for that LCSC code, summed over priced records.
- **Stock coverage** — unique part codes for which returned stock covers the schematic quantity, those that are short, and those whose stock could not be determined.

The estimate is not a cart quote. It does not include shipping, taxes, assembly charges, order constraints or any future stock/price change. The status below the summary notes that unavailable prices are excluded and that catalogue stock is not reserved.

### Open a part in the schematic

Select a row and use the detail panel's actions:

- **Select in schematic** navigates to the selected component's sheet, zooms to its location and selects it in Eeschema.
- **Select same part on sheet** selects all linked instances with the same LCSC code on that component's sheet. It does not select occurrences on other sheets in the same operation.
- **Copy part ID** copies the normalized LCSC code to the clipboard.
- **Search LCSC** opens an LCSC search for the selected code.
- **Open product page** opens the supplier product page when available.
- **Open datasheet** opens the catalogue or schematic datasheet URL when one is available.

The selection action uses KiCad's `FocusOnItems` IPC command to focus a target sheet before selection. The helper is unit-tested for sheet-path handling; the visual demo currently exercises selection in the root-sheet fixture, not every multi-sheet editor interaction.

## JLCPCB Component Properties

Launch **JLCPCB Component Properties** only after selecting exactly one schematic symbol in Eeschema. That symbol must carry a non-empty configured part-link custom property. The action opens a focused details window for the selected symbol. It does not add a tab to KiCad's native Symbol Properties dialog.

<p align="center"><img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-properties-selected.png" alt="Focused JLCPCB properties view with supplier photo, catalogue data, MPN comparison and schematic fields" width="620"></p>

### Catalogue information

The details panel combines the schematic properties with a JLCPCB catalogue lookup. Depending on what the supplier returns, it can show:

- Product photo and description.
- Manufacturer, manufacturer part number and package.
- Stock, lowest listed unit price and the available price-break ladder.
- JLCPCB category, Basic/Extended library class, preferred status and selected attributes.
- Schematic reference, value, LCSC link, MPN property, footprint, datasheet and sheet.
- Source and freshness label.

The catalogue lookup runs outside the Tk UI thread. Product images are fetched only from HTTPS URLs and are bounded to 5 MiB. When an image or field is missing, the view shows an empty / unavailable state rather than fabricating a value.

### Compare MPN listings

If the schematic symbol has an MPN custom property, the browser queries JLCPCB's parts-search endpoint using that string. If the schematic MPN field is empty, the catalogue MPN may be used.

The alternatives table can display LCSC code, manufacturer P/N, package, Exact/Related match, Basic/Extended class, stock or pre-order status, minimum order and lowest price break. Exact normalized-MPN matches are shown first. Double-click a row to open that listing's product page.

A **Related** row is a search suggestion, not a declared substitute. Compare package, electrical ratings, tolerance, pinout and other attributes against the design before considering it. The fact that JLCPCB returns a row does not establish electrical or mechanical equivalence.

### Freshness and source

The source line distinguishes direct JLCPCB website responses, community-search fallback and cached snapshots. A fresh lookup is attempted when a component is opened. When supplier endpoints cannot be reached, a previous cached record may be shown with a cached or stale label. Treat cached information as historical.

The direct JLCPCB detail and MPN endpoints are undocumented website endpoints, not a supported public components API. Their response formats and availability may change.

## JLCPCB Add or Update Component

This unified action handles two related tasks: updating catalogue properties on an existing schematic symbol, or adding a new project-local copy of a KiCad stock symbol. The mode follows the current Eeschema selection:

- **Exactly one editable schematic symbol selected:** update that symbol's catalogue fields. The selected library identity, graphics, pins, units and electrical pin types are preserved; the action never converts the selected symbol into another component type.
- **Nothing selected:** search the catalogue and add a project-local symbol copy.
- **Multiple items or a non-symbol object selected:** the action stops and asks you to select one symbol or clear the selection.

Both modes use the same review-first dialog. The symbol and footprint controls remain editable when catalogue inference is incomplete.

### Update an existing schematic symbol

Use this mode when a symbol is already placed, including an IC, connector, sensor, protection device or passive. You can add catalogue fields without changing the selected symbol's electrical definition.

1. In Eeschema, select exactly one schematic symbol.
2. Launch **JLCPCB Add or Update Component**.
3. Enter the LCSC part number and choose **Fetch and propose**.
4. Review the proposed fields, source/freshness label and warnings. The Value and Footprint inputs are editable; a suggested footprint can be replaced with the one verified for the selected part.
5. Confirm **Apply reviewed fields to selected symbol** to write the proposal. Cancel the confirmation to leave the schematic unchanged.

The proposal can include Value, Footprint, Datasheet, Description, LCSC, Manufacturer, MPN, JLCPCB Category and selected catalogue attributes. Empty or uncertain values are not guessed. The Value and Footprint inputs start with the best available suggestion, or with the selected symbol's existing value/footprint when no confident suggestion exists. Leaving a field blank does not clear an existing schematic field. Existing custom fields with matching names are updated; unrelated fields are preserved. New metadata fields are hidden in the schematic drawing.

If the catalogue classification suggests a different family from the selected library symbol, the review notes show that mismatch. The selected symbol is not replaced; check the proposed Value and Footprint against the datasheet before applying. The action saves the schematic after KiCad confirms the IPC write-back.

### Add a new project-local symbol

Use this mode when the component is not yet in the schematic. The add-on creates or reuses a reusable project-local symbol copied from a standalone KiCad stock-library symbol, then places an instance.

1. Save the KiCad project and clear the Eeschema selection.
2. Launch **JLCPCB Add or Update Component** from the active Schematic Editor.
3. Search by LCSC code, manufacturer part number, device name or another keyword. Passive-value queries such as 10k, 4k7, 470R, 100nF and 4.7uF use exact-value candidate filtering; other free-text searches show catalogue candidates to review.
4. Select a catalogue row when the search returns multiple parts. Review the suggested symbol, Value, package, Footprint, metadata and warnings.
5. In **Component details**, accept or edit the suggested KiCad Library:Symbol. When the part is not confidently classified, enter a stock ID manually, for example Amplifier_Operational:LM358. Edit Value and optional Footprint if the catalogue suggestion is incomplete. Reference prefix can also be edited.
6. Confirm **Generate and place component**. KiCad opens its native Add Symbol chooser, selects the generated project-local copy and returns to Eeschema with the symbol attached to the mouse cursor. Click in the schematic to place it or press Escape to cancel. Add mode has no X/Y coordinate fallback.

The inferred stock-symbol suggestions currently cover common resistors, capacitors, inductors, diodes, LEDs, fuses and crystals. Package mappings are deliberately narrower: the UI leaves Footprint blank when no reviewed mapping exists, and lets you enter a KiCad footprint identifier yourself. Unknown/ambiguous part categories remain at **Choose manually** rather than guessing an IC pinout.

#### What is generated

For the selected KiCad Library:Symbol, the add-on:

- Reads the named stock symbol from the matching KiCad symbol library.
- Copies its original symbol graphics, pin numbers, electrical pin types, units and other symbol-definition data. It does not synthesize pin functions from catalogue text.
- Refuses symbols that extend another library symbol, because the generator copies a standalone definition rather than resolving inherited definitions. Choose a standalone stock symbol instead.
- Writes the selected Value, optional Footprint, Datasheet and Description plus hidden LCSC, Manufacturer, MPN, category and catalogue attribute properties.
- Writes or extends the project-local JLCPCB_Generated.kicad_sym library and registers it in sym-lib-table using a project-relative KiCad project variable URI.
- Reloads the project library before opening KiCad's native Add Symbol chooser.

The generated symbol ID uses the selected reference prefix and LCSC part number, so repeat generation for the same part can reuse the existing project-local symbol. The generator protects unowned files and refuses to overwrite a generated symbol that has been manually modified. The library writer uses atomic file replacement and creates a backup when extending an existing generated library.

#### First generated library in a project

Eeschema loads the project symbol table when it opens the project. When this action creates the first project-local library-table row while Eeschema is already running, the current editor session may not know the new library nickname. In that case:

1. The generated symbol file and table entry are saved.
2. The plugin reports that the active session could not reload the new library.
3. No schematic instance is placed.
4. Close and reopen the project in Eeschema.
5. Run **JLCPCB Add or Update Component** again with the same LCSC code or query.

The existing generated symbol is detected and reused; placement can continue when the reopened editor resolves the library. Once the nickname is loaded, later additions can extend and reload that library within the running session.

#### Placement and electrical checks

Passive-value search only accepts exact nominal-value matches for supported resistor/capacitor queries. It does not compare voltage rating, tolerance, power rating, temperature coefficient, dielectric or manufacturer equivalence. General keyword matches are ranked catalogue candidates, not declared drop-in replacements. Review the selected part's details and datasheet.

Add mode is cursor-only. It uses the native Eeschema Add Symbol hotkey `A` through `xdotool` and requires the action to have been launched while the Eeschema window was focused on an X11 desktop. If that requirement is not met, add mode is disabled with an explanation; there is no X/Y coordinate fallback. Check Eeschema Preferences → Hotkeys if the native chooser does not open.

The optional Footprint field is a KiCad footprint-library reference, not a footprint generator. A symbol can be created without a footprint, but it will need a suitable footprint before PCB layout. Always inspect the symbol/footprint pair and verify pin numbers, electrical types, package dimensions and ratings against the manufacturer datasheet.
## Catalogue data, cache and privacy

- The plugin stores component catalogue records in a SQLite cache beneath the KiCad plugin settings directory returned by its IPC API.
- Live detail data is preferred. A community-search result or cached snapshot may be used as a fallback and is labelled accordingly.
- The overview and entry dialogs perform catalogue lookups outside the Tk UI thread so network waits do not freeze the window.
- Product images are downloaded only from HTTPS image URLs and are limited to 5 MiB.
- Signed image URLs are retained in memory only; they are stripped from persistent catalogue cache records because they expire.
- The plugin contains no JLCPCB credentials. No private partner API is configured.
- Price and stock are time-specific supplier responses, not order guarantees.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Overview says no linked components were found | Confirm that symbols have a non-empty custom field matching the browser's **Part-link field** value. Default is LCSC. |
| Component Properties rejects the selection | Select exactly one symbol, and confirm it has the configured part-link field. |
| Add or Update Component rejects the selection | Select exactly one schematic symbol to update it, or clear the selection to add a new project-local symbol. Non-symbol objects and multiple selections are rejected. |
| Proposal is incomplete / Generate and place is disabled | Check the review notes. Choose a KiCad Library:Symbol and enter a Value when no confident mapping was available; Footprint can be supplied later. |
| The selected symbol type differs from the catalogue suggestion | The update action preserves the selected symbol's pins and graphics. Check the proposed fields against the datasheet or cancel without writing. |
| Catalogue information is cached or stale | Check the source/freshness label. Supplier endpoints may be unavailable or may have changed; do not treat cached stock/price as current. |
| Product image is missing | The record may lack an image, the supplier image endpoint may be unavailable, or the HTTPS image download/decoder may have failed. |
| Add or Update Component saved a library but did not place a part | Reopen the project and run the same LCSC action again. See [First generated library in a project](#first-generated-library-in-a-project). |
| The add-on or its Python environment is missing | Check the IPC API and interpreter under **Preferences → Plugins**. Use **Recreate Plugin Environment** for a failed managed environment. Confirm that the installed directory matches the KiCad version directory. |

## Current limits

The add-on is an early prototype, with a deliberately conservative scope:

- Update mode writes reviewed catalogue fields to one selected editable schematic symbol of any component family; it does not replace the symbol or rewrite pins.
- Add mode can copy a standalone KiCad stock symbol selected by the user. Current automatic suggestions cover common resistor/capacitor/inductor, diode, LED, fuse and crystal categories; ambiguous categories require manual symbol selection.
- The generator does not infer manufacturer-specific pin maps, electrical pin types or IC symbol geometry. It copies the pin definitions of the symbol selected by the user.
- Footprint suggestions cover only reviewed package/type combinations. Footprint creation, pad mapping, footprint preview and 3D model generation are not implemented.
- Catalogue endpoints are unofficial website endpoints and may change without notice.
- Stock/price data is indicative and not a binding quote.
- The installer and binary dependencies are validated for Linux x86_64 with KiCad 10.99 nightly / 11.0+ and CPython 3.12–3.14. Other platform combinations are not yet advertised as supported.
- The visual CI fixture exercises the three action windows, both modes of Add or Update Component, live Eeschema selection and write-back in a root-sheet test project. It does not yet visually validate every manually selected symbol family or plugin-manager discovery.
## Developer checks

From the repository root:

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests scripts
ruff check plugin tests scripts
```

The CI visual workflow generates the screenshot bundle with KiCad nightly, Eeschema IPC, the production Tk windows and KiCad CLI. The generator lives in `tests/capture_visual_docs.py`; its asset manifest records hashes for the generated files and the source commit. See [visual evidence](visual-evidence.md) for details.
