# Changelog

## Unreleased

- Generate reusable project-local passive symbols from reviewed LCSC drafts by cloning KiCad's stock resistor/capacitor topology, preserving their two pins, writing native and hidden custom fields, registering the project library and placing linked instances. The first generated-library entry requires reopening the project and retrying once so Eeschema loads the new table row.
- Add direct placement of a new stock generic resistor/capacitor from a reviewed LCSC draft, with next free reference and explicit X/Y placement coordinates.
- Search for resistor/capacitor values as well as LCSC codes; filter JLCPCB keyword results to exact nominal-value matches, rank Basic listings first, and expose Extended candidates as alternatives.
- Place generated passives at the Eeschema cursor through KiCad's native Add Symbol chooser, retaining explicit X/Y placement as a fallback.
- Add review-first LCSC field population for selected generic resistor/capacitor symbols, with conservative value/footprint inference and KiCad IPC write-back.
- Scan LCSC-linked symbols across the complete schematic hierarchy.
- Navigate to a symbol's sheet, zoom to it and select it through KiCad's `FocusOnItems` IPC command.
- Use the configured cache TTL to distinguish recent offline cache results from stale catalogue data.
- Recursively remove expiring signed URLs from persisted catalogue records.
- Capture and verify the live Properties controls, Populate review/write-back dialog and Add Component review/library-placement dialog in the visual CI fixture.
- Add a complete user guide and a refreshed README with generated screenshots for all four schematic actions.
- Follow the desktop light/dark appearance across ttk and classic Tk widgets, with a `KICAD_JLCPCB_THEME=system|light|dark` override; capture all four windows in both palettes.

## 0.1.0 — 2026-11-12

- Establish the KiCad 11 Schematic Editor IPC plugin scaffold.
- Add local discovery of symbols carrying a configurable LCSC/JLCPCB custom field.
- Add local search, part-ID copying, datasheet opening and schematic symbol selection.
- Add a validated atomic installer and headless unit/package tests.
- Add JLCPCB parts-page and community catalogue adapters with SQLite cache/offline fallback.
- Add asynchronous product-image loading, part details and schematic selection actions.
- Pin the official KiCad Python IPC binding wheels to the kicad-blocks published build.
