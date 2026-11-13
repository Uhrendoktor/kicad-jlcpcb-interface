# User guide

This guide explains the three KiCad Schematic Editor actions, how to link symbols to LCSC records, how catalogue results are interpreted, and what the add-on deliberately does not automate.

## 1. Before you begin

### Supported environment

The current installer target is:

- KiCad 10.99 development nightly (the KiCad 11 pre-release directory) or KiCad 11.0+.
- Linux x86_64.
- CPython 3.12, 3.13 or 3.14 with Tkinter available.

Other operating systems and CPU architectures are not validated yet. The KiCad IPC API must be enabled and configured to use a compatible Python interpreter.

### Appearance

The browser and passive review windows follow the desktop appearance where it can be detected: Windows app theme, macOS appearance, and Linux GTK/GNOME or KDE settings. ttk controls, tables, fields, scrollbars and the review-notes text area share the same palette.

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

## 2. Link a symbol to an LCSC record

The browser discovers schematic symbols through a custom property. The default property name is `LCSC`.

For each component to include:

1. Open the symbol's Properties dialog in Eeschema.
2. Add a custom field named `LCSC`.
3. Set its value to an LCSC code such as `C60489`. A URL containing an LCSC code is also accepted.
4. Save the schematic.

The `LCSC` field name is case-insensitive. In the browser, the **Part-link field** entry can be changed to another property name such as `JLCPCB`, `LCSC Part Number` or `JLCPCB Part Number`. The search includes reference, value, part number / stored link, MPN, footprint and sheet path.

An MPN field is optional. When present, the browser can compare JLCPCB keyword-search listings for that manufacturer part number. Recognized field names include `MPN` and common Manufacturer Part Number variants.

## 3. JLCPCB Component Overview

Launch **JLCPCB Component Overview** from Eeschema. It scans the schematic hierarchy and lists every symbol containing the configured part-link property.

![Overview window with linked schematic components, filters, catalogue detail and the project summary](https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-initial.png)

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

## 4. JLCPCB Component Properties

Launch **JLCPCB Component Properties** only after selecting exactly one schematic symbol in Eeschema. That symbol must carry a non-empty configured part-link custom property. The action opens a focused details window for the selected symbol. It does not add a tab to KiCad's native Symbol Properties dialog.

![Focused JLCPCB properties view with supplier photo, catalogue data, MPN comparison and schematic fields](https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-properties-selected.png)

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

## 5. JLCPCB Add or Update Passive

This single action handles two closely related tasks: adding catalogue data to an existing generic passive, or creating a new project-local passive symbol. It chooses the mode from the current Eeschema selection:

- **Exactly one stock `Device:R` or `Device:C` selected:** update that placeholder's fields. Its symbol graphics, pin numbers, electrical pin types and pin mapping are preserved.
- **Nothing selected:** search the catalogue and add a new passive symbol to the active project.
- **Multiple items or an unsupported symbol selected:** the action stops and explains how to choose one supported placeholder or clear the selection.

Both modes use the same review-first dialog. The selected mode changes the lookup controls and the action performed after confirmation.

### Update an existing placeholder

Use this mode when a generic resistor or capacitor is already placed in the schematic and you want to fill in its catalogue-derived properties without changing its electrical symbol.

1. In Eeschema, select exactly one compatible generic `Device:R` or `Device:C` symbol.
2. Launch **JLCPCB Add or Update Passive**.
3. Enter the LCSC code and choose **Fetch and propose**.
4. Review the detected component type, confidence, proposed properties, catalogue source and review notes.
5. Confirm **Apply reviewed fields to selected symbol** to write the proposal to KiCad. Cancel the confirmation to leave the symbol unchanged.

The review screen lets you verify the values before they are written to the selected placeholder:

![Screenshot showing the proposed catalogue fields for updating an existing generic resistor.](https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-proposed.png)

The proposal may include Value, Footprint, Datasheet, Description, LCSC, Manufacturer, MPN, JLCPCB Category and selected catalogue attributes. Empty or uncertain values are not guessed. Existing custom fields with matching names are updated; unrelated fields are preserved. Newly created metadata fields are hidden in the schematic drawing.

After confirmation, the action sends the updated symbol through the KiCad IPC API and saves the schematic. Review the resulting component and footprint before continuing with the PCB.

#### When Apply remains disabled

Automatic update is disabled when:

- The catalogue record does not confidently identify a resistor or capacitor.
- The selected symbol's library ID does not match the proposed generic symbol type.
- The proposal contains no safe fields to write.

A missing or unsupported footprint or an uncertain value is left unchanged and explained in the review notes. The UI does not convert an arbitrary symbol into a resistor or capacitor.

### Add a new project-local passive

Use this mode when the part is not yet present in the schematic. The add-on creates or reuses a reusable symbol in the project-local library, then places an instance.

1. Save the KiCad project and clear the Eeschema selection.
2. Launch **JLCPCB Add or Update Passive** from the active Schematic Editor.
3. Enter either an LCSC code (for example, `C60489`) or a resistor/capacitor value (for example, `10k`, `4k7`, `470R`, `100nF`, `0.1uF` or `4.7uF`), then choose **Search catalogue**.
4. For a value search, review the exact-value matches. Basic listings are ranked first; Extended listings are retained as alternatives. Select a row to inspect its proposed Value, package, footprint, voltage/tolerance attributes, metadata and warnings. An LCSC-code lookup reviews that single catalogue record.
5. Choose **Place at cursor in KiCad** or **Use X/Y coordinates (mm)**. Cursor placement is selected by default when the action was launched from Eeschema on an X11 desktop with `xdotool`; otherwise use coordinates.
6. Confirm **Generate and place passive**. In cursor mode, the add-on opens KiCad's native Add Symbol chooser, selects the generated symbol, and returns to Eeschema with it attached to the mouse pointer. Left-click to place it or press Escape to cancel. Coordinate mode places the instance at the entered position.

The proposal includes the catalogue match and placement controls so you can confirm both the selected part and where it will enter the design:

![Screenshot showing catalogue matches and placement options before adding a reusable passive symbol.](https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-proposed.png)

The first matching Basic part is preselected when one is available. Basic/Extended is JLCPCB's library classification; it is not a statement that a part is electrically interchangeable with another listing. Compare voltage rating, tolerance, dielectric, power rating, package and other attributes before generating the symbol.

#### What is generated

For a supported resistor or capacitor, the add-on:

- Clones the corresponding KiCad stock `Device:R` or `Device:C` symbol graphics and two-pin electrical topology.
- Adds the inferred Value, supported footprint, datasheet and Description.
- Adds hidden LCSC, Manufacturer, MPN, `JLCPCB Library Type` (Basic/Extended), category and selected attribute properties.
- Writes or extends `JLCPCB_Generated.kicad_sym` in the project directory.
- Registers the project library in `sym-lib-table` using a project-relative KiCad project variable URI.
- Reloads the project library before opening KiCad's native chooser or placing an instance at the X/Y coordinates.
- Lets KiCad assign the reference in cursor mode; coordinate mode chooses the next free R or C reference.

The symbol ID is deterministic for the LCSC part, so an existing generated symbol is reused rather than duplicated. The generator protects unowned files and refuses to overwrite a generated symbol that has been manually modified. The library writer uses atomic file replacement and a backup when extending an existing generated library.

#### First generated library in a project

Eeschema loads the project symbol table when it opens the project. When this action creates the *first* project-local library table row while Eeschema is already running, the current editor session may not know that new library nickname. In that case:

1. The generated symbol file and table entry are saved.
2. The plugin reports that the active session could not reload the new library.
3. No symbol is opened in the chooser and no schematic instance is placed.
4. Close and reopen the project in Eeschema.
5. Run **JLCPCB Add or Update Passive** again with the same LCSC code or value query.

The existing generated symbol is detected and reused; placement can continue when the reopened editor resolves the library. Once the nickname is loaded, later additions can extend and reload that library within the running session.

#### Placement and electrical checks

Value search only returns exact nominal-value matches that can be confidently classified as resistors or capacitors and mapped to one of the reviewed SMD footprints (0201, 0402, 0603, 0805, 1206, 1210, 1812, 2010 or 2512). It does not compare voltage rating, tolerance, power rating, temperature coefficient, dielectric or manufacturer equivalence. Review those fields and the datasheet before generating a symbol.

Cursor placement uses the native Eeschema Add Symbol hotkey `A` through `xdotool`. It requires the action to have been launched while the Eeschema window was focused on an X11 desktop. Check Eeschema Preferences → Hotkeys if the native chooser does not open. X/Y placement remains available when cursor handoff cannot be used.

The generated footprint association is a KiCad footprint-library reference, not a footprint generator. Always inspect the symbol and PCB footprint. The generator preserves stock two-pin passive topology; it does not infer component-specific pin mapping or arbitrary electrical behavior.

## 6. Catalogue data, cache and privacy

- The plugin stores component catalogue records in a SQLite cache beneath the KiCad plugin settings directory returned by its IPC API.
- Live detail data is preferred. A community-search result or cached snapshot may be used as a fallback and is labelled accordingly.
- The overview and entry dialogs perform catalogue lookups outside the Tk UI thread so network waits do not freeze the window.
- Product images are downloaded only from HTTPS image URLs and are limited to 5 MiB.
- Signed image URLs are retained in memory only; they are stripped from persistent catalogue cache records because they expire.
- The plugin contains no JLCPCB credentials. No private partner API is configured.
- Price and stock are time-specific supplier responses, not order guarantees.

## 7. Troubleshooting

| Symptom | What to check |
| --- | --- |
| Overview says no linked components were found | Confirm that symbols have a non-empty custom field matching the browser's **Part-link field** value. Default is `LCSC`. |
| Component Properties rejects the selection | Select exactly one symbol, and confirm it has the configured part-link field. |
| Add or Update Passive rejects the selection | Select exactly one stock `Device:R` or `Device:C` to update it, or clear the selection to add a new part. |
| Proposal is incomplete / Apply is disabled | Check the confidence and review notes. The component may not be a supported passive, value may be ambiguous, or package may be unmapped. Complete the component manually. |
| Catalogue information is cached or stale | Check the source/freshness label. Supplier endpoints may be unavailable or may have changed; do not treat cached stock/price as current. |
| Product image is missing | The record may lack an image, the supplier image endpoint may be unavailable, or the HTTPS image download/decoder may have failed. |
| Add or Update Passive saved a library but did not place a part | Reopen the project and run the same LCSC action again. See [First generated library in a project](#first-generated-library-in-a-project). |
| The add-on or its Python environment is missing | Check the IPC API and interpreter under **Preferences → Plugins**. Use **Recreate Plugin Environment** for a failed managed environment. Confirm that the installed directory matches the KiCad version directory. |

## 8. Current limits

The add-on is an early prototype, with a deliberately conservative scope:

- Automatic create/populate flows support resistor/capacitor records only, and only the stock two-pin topology.
- Arbitrary IC symbol generation, pin mapping, electrical pin type inference and custom symbol geometry are not implemented.
- PCB footprint creation, pad mapping, footprint preview and 3D model preview are not implemented.
- Catalogue endpoints are unofficial website endpoints and may change without notice.
- Stock/price data is indicative and not a binding quote.
- The installer and binary dependencies are validated for Linux x86_64 with KiCad 10.99 nightly / 11.0+ and CPython 3.12–3.14. Other platform combinations are not yet advertised as supported.
- The visual CI fixture verifies the three action windows, both passive-component modes, live Eeschema selection and write-back in a root-sheet test project. Plugin-manager discovery and interactive cross-sheet navigation in a populated hierarchical GUI project remain separate validation items.

## 9. Developer checks

From the repository root:

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests scripts
ruff check plugin tests scripts
```

The CI visual workflow generates the screenshot bundle with KiCad nightly, Eeschema IPC, the production Tk windows and KiCad CLI. The generator lives in `tests/capture_visual_docs.py`; its asset manifest records hashes for the generated files and the source commit. See [visual evidence](visual-evidence.md) for details.
