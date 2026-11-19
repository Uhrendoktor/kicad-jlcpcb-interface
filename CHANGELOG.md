# Changelog

## Unreleased

- Place editable KiCad properties and proposal metadata in a left column beside supplier details and tabbed CAD previews on the right; synchronize the Footprint field to the visible CAD candidate unless the user explicitly overrides it.
- Show field-specific validation below its source input, block generation on missing required fields, and classify review notes with explicit Error, Warning or Info labels, readable icons and theme-aware severity colors.
- Export downloaded schematic symbols, footprints and optional STEP models to user-selected KiCad libraries; register the libraries in the active project and refuse conflicting user-owned content.
- Add a confirmed Sync with library action that updates writable project/user symbol definitions and every schematic instance using the same library symbol, with rollback attempts on failed schematic write-back.

- Discover public EasyEDA CAD by LCSC number when local KiCad symbol or footprint mappings are missing. No credentials are required; symbol and footprint assets are independent, cached, previewed with SVG rendering and proposed in editable Add/Update fields.
- Convert EasyEDA 2D CAD through the independent MIT-licensed `euc2k` package; use the shared `resvg_py` renderer for 2D CAD previews. Preserve upstream source/author/licence notes in the CAD cache.
- Fetch optional upstream EasyEDA STEP models, attach portable project-relative references to accepted footprints, and render a 3D review thumbnail with KiCad CLI when available. Model files and previews are independently cached and integrity checked.
- Register reviewed imported footprints in project-local `.pretty` libraries and `fp-lib-table` only during Add/Update apply. Copy converter source/author/licence notes into a per-LCSC project sidecar, refusing to overwrite user-owned content. Protect unowned, symlinked and manually modified library content.
- Keep Add/Update proposals editable and ignore stale asynchronous CAD results. Update mode previews candidates and may suggest a footprint, but never replaces the selected schematic symbol or pin mapping.
- Merge Properties, Add and Update around a shared component workspace and catalogue preview. The Add/Update page uses a scrollable content area, a persistent action footer, concise property rows, conditional candidate results and mode-specific KiCad inputs; Add mode is cursor-only with no coordinate controls or direct-coordinate insertion. Use low-contrast surfaces and clear supplier links rather than nested bordered cards.
- Share image retrieval and catalogue preview widgets across Properties and Add/Update. Try multiple supplier image renditions with bounded HTTPS requests, validate redirects and content types, and show an explicit unavailable state only after all candidates fail.
- Generalize the unified workflow as **JLCPCB Add or Update Component**. Updating accepts any single editable selected schematic symbol and preserves its library identity, graphics, pins, units and electrical pin types; add mode creates/reuses a project-local symbol copy. Multiple selections and non-symbol selections are rejected.
- Add catalogue search by LCSC code, manufacturer part number and free-text keyword. Exact resistor/capacitor value searches show a five-item Basic-first shortlist and automatically configure the stock symbol, reference prefix, mapped footprint and supplier fields for the selected passive.
- Generalize project-local symbol generation to copy standalone stock KiCad symbol definitions with their original pin numbers, electrical types, units and graphics. IC pin maps are never inferred from catalogue text. Footprints are optional and can be selected/edited manually when no reviewed mapping exists.
- Make Value and Footprint editable during both update and add review. Preserve uncertain/unknown fields rather than fabricating symbol identities, footprints or pin assignments. Add tests for non-passive classification, arbitrary symbol metadata updates, and multi-pin IC symbol copies.
- Use supported KiCad schematic IPC to insert generated symbols at the sheet center and select them for subsequent movement. Keep manual coordinate entry and direct-coordinate controls out of Add mode.
- Scan LCSC-linked symbols across the complete schematic hierarchy.
- Navigate to a symbol's sheet, zoom to it and select it through KiCad's `FocusOnItems` IPC command.
- Use the configured cache TTL to distinguish recent offline cache results from stale catalogue data.
- Recursively remove expiring signed URLs from persisted catalogue records.
- Capture and verify the live Properties controls and both modes of the unified Add or Update Component dialog in the visual CI fixture.
- Organize the README around the user workflows, with small, contextual screenshots that explain why each step matters.
- Add a complete user guide and visual-evidence documentation for the three schematic actions.
- Align the README and guides around the single selection-aware Add or Update Component action; document inference confidence, manual symbol choice and the scope of the passive-only live screenshot fixtures.
- Follow the desktop light/dark appearance across ttk and classic Tk widgets, with a `KICAD_JLCPCB_THEME=system|light|dark` override; capture both component-action modes in both palettes.

## 0.1.0 — 2026-11-12

- Establish the KiCad 11 Schematic Editor IPC plugin scaffold.
- Add local discovery of symbols carrying a configurable LCSC/JLCPCB custom field.
- Add local search, part-ID copying, datasheet opening and schematic symbol selection.
- Add a validated atomic installer and headless unit/package tests.
- Add JLCPCB parts-page and community catalogue adapters with SQLite cache/offline fallback.
- Add asynchronous product-image loading, part details and schematic selection actions.
- Pin the official KiCad Python IPC binding wheels to the kicad-blocks published build.
