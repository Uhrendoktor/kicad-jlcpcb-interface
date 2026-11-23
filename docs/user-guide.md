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

The browser and component review windows follow the desktop appearance where it can be detected: Windows app theme, macOS appearance, and Linux GTK/GNOME or KDE settings. ttk controls, tables, fields, scrollbars and the review-notes text area share the same palette. Scrollbars in the Component Overview and Add/Update windows use a narrow, low-contrast idle rail that expands during scrolling, dragging or hover, then collapses after 900 ms without interaction.

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
- The displayed LCSC part ID in the **Selected Part** card is clickable and copies the normalized code to the clipboard; it is also keyboard-accessible with Enter or Space.
- **Open in LCSC** opens the LCSC page/search for the selected code.
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

The alternatives table shows LCSC code, manufacturer P/N, package, a compact match icon, Basic/Extended class, stock or pre-order status, minimum order and the lowest available quantity-price break. Hover over a match icon for its meaning. Basic rows are highlighted in green; Extended and unclassified rows use the muted text color. Exact normalized-MPN matches are shown first. Double-click a row to open that listing's product page.

A **Related** row is a search suggestion, not a declared substitute. Compare package, electrical ratings, tolerance, pinout and other attributes against the design before considering it. The fact that JLCPCB returns a row does not establish electrical or mechanical equivalence.

### Freshness and source

The source line distinguishes direct JLCPCB website responses, community-search fallback and cached snapshots. A fresh lookup is attempted when a component is opened. When supplier endpoints cannot be reached, a previous cached record may be shown with a cached or stale label. Treat cached information as historical.

The direct JLCPCB detail and MPN endpoints are undocumented website endpoints, not a supported public components API. Their response formats and availability may change.

## JLCPCB Add or Update Component

**There is one Eeschema action: JLCPCB Add or Update Component.** The action chooses its mode from the current schematic selection; you do not launch separate add and update commands.

| Selection when the action starts | Mode | Behavior |
| --- | --- | --- |
| Exactly one editable schematic symbol | **Update** | Look up a catalogue part and apply reviewed fields to the selected symbol. Its library identity, graphics, pins, units and electrical pin types are not replaced. Any editable symbol family is accepted, including ICs, connectors, sensors, protection devices and passives. |
| Nothing selected | **Add** | Search the catalogue, review a suggested or manually selected KiCad symbol, then create and place a project-local copy. |
| Multiple items or a non-symbol object | — | The action rejects the selection. Select one symbol to update it, or clear the selection to add a component. |

Both contexts use the same workspace hierarchy: a compact search toolbar, Basic-first results, a selected-part preview, editable KiCad details, proposed fields, review notes and one persistent action footer. The preview shows the supplier image when one is available, plus description, manufacturer, MPN, LCSC code, package, stock, indicative unit price, library class and source freshness. When a local KiCad symbol or footprint mapping is missing, an additional CAD section searches public EasyEDA data by LCSC number and displays separate schematic-symbol and PCB-footprint previews when available. Product-page and datasheet links are enabled only when the catalogue provides HTTPS URLs. Add mode automatically maps common passives and exposes a manual **Library:Symbol** override only when required. KiCad inserts the generated symbol at the sheet center, focuses it and leaves it selected for the Move tool; there are no coordinate controls.

### Shared review process

1. Launch **JLCPCB Add or Update Component** from the active Schematic Editor.
2. Use the primary **Value or part number** field for a passive value, LCSC code, manufacturer part number or keyword. Basic listings are always ranked before Extended and unclassified listings. Check **Basic parts only** to hide those other library classes; leave it unchecked to keep them available. This checkbox does not auto-search, and a direct LCSC-code lookup still returns the exact requested record. Expand the funnel button to reveal a compact one-row strip with Package/footprint and Tolerance fields. Both dropdowns are editable: their options are built-in common-value suggestions, not live catalogue data, and you can type a custom package, KiCad footprint ID or percentage. Active constraints are summarized beside the funnel. Choosing a preset does not run a search; press **Search** or Enter to query the catalogue and apply the optional filters to the returned records.
3. When a local mapping is missing, the CAD panel searches the public EasyEDA service using the selected LCSC number. This lookup does not require an account, API key or other credentials. Symbol and footprint results are independent; one may be available without the other. Review the source labels and both previews when provided.
4. Review the proposed fields, inference confidence, catalogue source/freshness and warnings before applying anything. Edit **Value**, **Footprint**, and (in Add mode) **KiCad Library:Symbol** as needed. Downloaded CAD is a candidate, not a verified electrical/mechanical equivalent; your manual edits take precedence over late search results.
5. In Update mode, click **Update component** to write the reviewed fields directly to the selected symbol. If the Footprint field still matches the downloaded candidate, its project-local footprint library is registered first. The selected symbol identity, graphics, pins, units and electrical types remain unchanged. In Add mode, **Generate & select component** imports the reviewed CAD assets into project-local libraries, creates the project-local symbol and inserts it into the schematic. Neither action opens an extra confirmation window.

The review workspace keeps editable KiCad properties and field-specific guidance in
the left column, with the supplier record and tabbed Symbol, Footprint and 3D model
previews in the right column. An invalid symbol or footprint identifier is called out
directly below its input; a missing required symbol or component value blocks
generation. Optional footprints are warnings rather than blocking errors. The review
notes use explicit **Error**, **Warning** and **Info** labels as well as icons and
theme-aware colors, so severity is never communicated by color alone.

The review controls are deliberately editable. An uncertain symbol or footprint is not silently treated as correct, and an optional footprint can be filled in later. Catalogue images and CAD previews load in the background; stale search results are ignored and user-edited fields are not overwritten. A missing CAD record, unavailable preview or conversion error leaves the existing manual workflow usable.

### Understand the inference confidence

The confidence label describes how complete the catalogue-to-symbol suggestion is; it is not a guarantee that the selected part is electrically interchangeable:

- **High** — a resistor, capacitor or inductor was classified, a value was inferred, and a reviewed footprint mapping is available.
- **Partial** — a component family was recognized, but the value or reviewed footprint may be incomplete, or the symbol is a generic template such as a diode, LED, fuse or crystal.
- **Metadata only** — the catalogue record does not support a confident stock-symbol classification. In add mode, select a KiCad `Library:Symbol` manually.

Exact-value resistor/capacitor searches return at most five high-confidence exact matches. The primary search field contains only the value. Basic listings rank first for both exact passive-value and general keyword searches. The **Basic parts only** checkbox, available for catalogue searches, excludes Extended and unclassified listings when enabled; otherwise those listings remain available after Basic matches. A direct LCSC-code lookup is always exact and is not filtered by the checkbox. Expand the compact **Advanced filters** strip to optionally add a package/footprint and tolerance constraint on one horizontal row; every displayed passive match must satisfy all supplied filters. The dropdown entries are hard-coded shortcuts for common package sizes and tolerance values. Both controls remain editable, so custom strings can be entered. They do not auto-query: **Search** or Enter sends the value query to JLCPCB, then package and tolerance matching is applied to the returned catalogue results. Package matching uses the supplier package or a mapped KiCad footprint; tolerance matching uses a structured supplier attribute or an explicitly stated ± percentage in the description. The alternatives table shows the mapped KiCad footprint and supplier-stated tolerance. Update mode starts with the selected passive's Value in the primary field, so searching immediately reveals alternatives for that value. Stock and price are secondary ranking factors, not a guarantee of purchase availability. General keyword results are catalogue candidates to inspect, not declared drop-in replacements.

### Update an existing schematic symbol

Use update mode when exactly one editable symbol is selected in Eeschema. It is not limited to resistor and capacitor placeholders: it can add catalogue fields to an IC, connector, sensor, protection device or any other editable schematic symbol without changing the selected symbol's electrical definition.

1. Select exactly one schematic symbol.
2. Launch **JLCPCB Add or Update Component**.
3. The selected resistor/capacitor's Value is prefilled in the primary search field. Choose **Search** to show alternatives for that value, or enter an LCSC part number to load one exact record. Basic parts rank first; check **Basic parts only** to hide Extended and unclassified alternatives. Expand the funnel button to set a package/footprint or tolerance in the compact filter strip. Select a result to load its proposal for the selected symbol.
4. Review the proposed fields, source/freshness label and warnings. The **Value** and **Footprint** inputs are editable; replace a suggested footprint with the one verified for the selected part.
5. Click **Update component** to write the reviewed fields directly. No additional confirmation dialog is shown.

The proposal can include Value, Footprint, Datasheet, Description, LCSC, Manufacturer, MPN, JLCPCB Category and selected catalogue attributes. The selected symbol's library identity, graphics, pins, units and electrical pin types remain unchanged, even when the catalogue classification suggests another component family. A mismatch is surfaced in the review notes; check the proposed Value and Footprint against the datasheet before applying.

Existing fields with matching names are updated; unrelated custom fields are preserved. Leaving Value or Footprint blank does not clear an existing field. New catalogue metadata fields are hidden in the schematic drawing. The action saves the schematic after KiCad confirms the IPC write-back.

#### Sync reviewed properties with the symbol library

When the selected part has changed and you want the same reviewed properties applied to all instances of this symbol, use **Sync with library** in Update mode. The action summarizes the affected references and asks for confirmation. It then updates Value, Footprint, Datasheet, Description and catalogue metadata in the symbol definition, followed by every schematic instance that uses the same `Library:Symbol` identity. Graphics, pins, units and library identity are preserved.

Sync is limited to writable project-local or user-owned symbol libraries. KiCad stock/system libraries are deliberately left untouched. For a stock symbol, first use KiCad Symbol Editor to copy the symbol into a project or user library and select that library in the schematic. If the schematic write-back fails, the add-on attempts to restore both the library file and the affected schematic symbols.

### Add a new project-local symbol

Use add mode when nothing is selected in Eeschema. The add-on creates or reuses a project-local symbol copied from a standalone KiCad stock-library symbol, inserts it at the current sheet center through KiCad IPC, and leaves it selected in the editor.

1. Save the KiCad project and clear the Eeschema selection.
2. Search by LCSC code, manufacturer part number, device name or another keyword. Basic listings rank first; check **Basic parts only** to hide Extended and unclassified results. For a passive, enter only its value in the primary field, such as `10k`, `4k7` or `100nF`. Expand the funnel button to optionally set a package/footprint and tolerance in the compact one-row strip. Value matching is exact; every supplied advanced filter must also match.
3. For a passive value search, choose from the shortlist of up to five exact matches. Basic parts are listed first. Selecting a candidate automatically fills the stock `Device:R` or `Device:C` symbol, reference prefix, Value, reviewed footprint mapping and catalogue metadata. The product preview shows the supplier photo and available specifications. You can still edit Value or Footprint before generation.
4. For other part searches, select a catalogue row and review the suggested symbol, Value, package, Footprint, metadata, confidence and warnings. When no suitable local symbol or footprint mapping exists, the CAD panel retrieves public EasyEDA assets by LCSC number and displays separate symbol and footprint previews. Available assets populate the editable Symbol and Footprint inputs. You can replace either identifier manually; a missing footprint does not prevent symbol creation.
5. Choose **Generate & select component**. KiCad copies the selected symbol into the project-local symbol library and, when the proposed footprint is the retrieved candidate, registers it in a project-local `.pretty` library and `fp-lib-table`. It then inserts the symbol at the center of the current sheet, brings it into view and selects it. Use the normal Move tool to move it to the desired location.

Automatic symbol suggestions currently cover common resistors, capacitors, inductors, diodes, LEDs, fuses and crystals. The suggestions are generic symbol templates, not manufacturer-specific pin maps. When classification is ambiguous or a local stock symbol is missing, a public EasyEDA CAD lookup can provide a downloadable symbol candidate keyed by the LCSC part number. The original pin definitions are copied as supplied; pin equivalence is never inferred from the catalogue description.

Package mappings are narrower than symbol classification. When no reviewed local mapping exists, the same public lookup can provide footprint geometry and a separate SVG preview. The imported footprint is stored in a project-local `.pretty` library and registered in `fp-lib-table`. Footprint is optional for symbol generation, but a suitable footprint is needed before normal PCB layout.

#### Export CAD assets to reusable libraries

When a CAD candidate is available, choose **Export to library** to save the available assets into locations you select. Symbol destinations must be `.kicad_sym` files, footprint destinations must be `.pretty` directories, and a STEP model is saved to a separate directory together with its footprint. The add-on registers symbol and footprint destinations in the active project's `sym-lib-table` and `fp-lib-table`. A STEP reference is written relative to ${KIPRJMOD} when the model directory is inside the project; an external directory is stored as an absolute path and is less portable.

An existing symbol or footprint with the same name is reused only when its contents match exactly. Different content is reported as a conflict instead of silently replacing the existing library item. Exporting a CAD symbol in Update mode does not replace the selected schematic symbol or its pin mapping.

#### What is generated

For the selected KiCad Library:Symbol, the add-on:

- Reads the named stock symbol from an installed KiCad library or, when selected, the cached EasyEDA conversion file.
- Copies the symbol graphics, pin numbers, electrical pin types, units and other symbol-definition data supplied by that source. It does not synthesize pin functions from catalogue text.
- Refuses symbols that extend another library symbol, because the generator copies a standalone definition rather than resolving inherited definitions. Choose a standalone stock symbol instead.
- Writes the selected Value, optional Footprint, Datasheet and Description plus hidden LCSC, Manufacturer, MPN, category and catalogue attribute properties.
- Writes or extends the project-local `JLCPCB_Generated.kicad_sym` library and registers it in `sym-lib-table` using a project-relative KiCad project variable URI.
- When the reviewed Footprint field matches the retrieved CAD candidate, copies the converted `.kicad_mod` into a project-local `JLCPCB_<LCSC>.pretty` library and registers it in `fp-lib-table`. Existing modified or unowned footprint libraries are not overwritten.
- When either retrieved symbol or footprint geometry is accepted, copies the converter's source/author/licence notes into `JLCPCB_<LCSC>_CREDITS.txt` beside the project. Existing user-owned credits files are never overwritten.
- Reloads the project library and opens KiCad's native Add Symbol chooser with the generated part selected when X11/xdotool is available. Select it manually in the chooser on sessions without X11 automation, then click to place or press Esc to cancel.

The generated symbol ID uses the selected reference prefix and LCSC part number, so repeat generation for the same part can reuse the existing project-local symbol. The generator protects unowned files and refuses to overwrite a generated symbol that has been manually modified. The library writer uses atomic file replacement and creates a backup when extending an existing generated library.

#### First generated library in a project

Eeschema loads the project symbol table when it opens the project. When this action creates the first project-local library-table row while Eeschema is already running, the current editor session may not know the new library nickname. In that case:

1. The generated symbol file and table entry are saved.
2. The plugin reports that the active session could not reload the new library.
3. No schematic instance is placed.
4. Close and reopen the project in Eeschema.
5. Run **JLCPCB Add or Update Component** again with the same LCSC code or query.

The existing generated symbol is detected and reused; the native Add Symbol chooser can select it after the reopened editor resolves the project library. Once the nickname is loaded, later additions can extend and reload that library within the running session.

#### Placement and electrical checks

Add mode opens KiCad's native Add Symbol chooser and hands the generated symbol to the cursor. With X11/xdotool, the chooser search is filled automatically; otherwise choose the generated symbol manually. Click to place it or press Esc to cancel. The add-on checks the IPC action status before treating the chooser as open, and falls back to the configured Add Symbol hotkey when the IPC action is unavailable or rejected.

The optional Footprint field is a KiCad footprint-library reference, not a footprint generator. A symbol can be created without a footprint, but it will need a suitable footprint before PCB layout. Always inspect the symbol/footprint pair and verify pin numbers, electrical types, package dimensions and ratings against the manufacturer datasheet.

## Catalogue data, cache and privacy

- The plugin stores component catalogue records in a SQLite cache beneath the KiCad plugin settings directory returned by its IPC API.
- Live detail data is preferred. A community-search result or cached snapshot may be used as a fallback and is labelled accordingly.
- The overview and entry dialogs perform catalogue lookups outside the Tk UI thread so network waits do not freeze the window.
- Product images are downloaded only from HTTPS image URLs and are limited to 5 MiB.
- Signed image URLs are retained in memory only; they are stripped from persistent catalogue cache records because they expire.
- The plugin contains no JLCPCB or EasyEDA credentials. No private partner API is configured. The EasyEDA CAD fallback uses public website endpoints, paces requests by at least four seconds, and may fail or be rate-limited.
- Retrieved CAD files and previews are cached beneath the plugin settings directory. Signed media is not required for the CAD lookup. Source authorship and licence notes returned by the converter are stored in the CAD cache's `CREDITS.txt` and copied into a per-LCSC project sidecar when the corresponding CAD asset is accepted.
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
- Add mode generates a project-local copy of a standalone KiCad stock symbol. Common resistor/capacitor value searches offer up to five exact candidates with Basic parts first and automatically select the generic stock symbol, reference prefix and mapped footprint when available. Basic-first ordering also applies to general keyword results; the **Basic parts only** checkbox can hide Extended and unclassified catalogue results. Exact LCSC-code lookups are not filtered. Other recognized categories receive cautious symbol suggestions; ambiguous categories require manual symbol selection. Placement uses KiCad's native Add Symbol chooser; click to place the cursor-attached symbol or press Esc to cancel.
- The generator does not infer manufacturer-specific pin maps, electrical pin types or IC symbol geometry. It copies the pin definitions of the symbol selected by the user.
- EasyEDA CAD retrieval uses undocumented public website endpoints and only works when a public CAD record exists for the LCSC part. Some parts have no symbol, no footprint, incomplete geometry or unavailable previews. The conversion path is best-effort and preserves upstream attribution/licence notes.
- Downloaded symbols, footprints and STEP models are unverified candidates. When EasyEDA attaches a 3D model, the review panel attempts a native KiCad 3D preview; the model is copied into the project only when the associated footprint is accepted. Some parts have no upstream model or no renderable preview. No 3D geometry is generated and no PCB-rule validation is performed. Inspect pin numbers, electrical types, pad dimensions, drills, model alignment and ratings against the datasheet before manufacturing.
- Catalogue and CAD endpoints are unofficial website endpoints and may change or rate-limit requests without notice. No credentials are required.
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
