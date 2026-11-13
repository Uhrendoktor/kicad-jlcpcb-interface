# Changelog

## Unreleased

- Consolidate passive population and creation into one **JLCPCB Add or Update Passive** action. With one stock generic `Device:R` or `Device:C` selected it updates reviewed catalogue fields without changing graphics or pin mapping; with no selection it creates or reuses a project-local symbol and places it. Multiple and unsupported selections are rejected.
- Generate reusable project-local passive symbols from reviewed LCSC drafts by cloning KiCad's stock resistor/capacitor topology, preserving their two pins, writing native and hidden custom fields, registering the project library and placing linked instances. The first generated-library entry requires reopening the project and retrying once so Eeschema loads the new table row.
- Search for resistor/capacitor values as well as LCSC codes; filter JLCPCB keyword results to exact nominal-value matches, rank Basic listings first, and expose Extended candidates as alternatives.
- Make generated passive placement cursor-only through KiCad's native Add Symbol chooser; remove the manual X/Y coordinate-placement path.
- Scan LCSC-linked symbols across the complete schematic hierarchy.
- Navigate to a symbol's sheet, zoom to it and select it through KiCad's `FocusOnItems` IPC command.
- Use the configured cache TTL to distinguish recent offline cache results from stale catalogue data.
- Recursively remove expiring signed URLs from persisted catalogue records.
- Capture and verify the live Properties controls and both modes of the unified Add or Update Passive dialog in the visual CI fixture.
- Organize the README around the user workflows, with small, contextual screenshots that explain why each step matters.
- Add a complete user guide and visual-evidence documentation for the three schematic actions.
- Follow the desktop light/dark appearance across ttk and classic Tk widgets, with a `KICAD_JLCPCB_THEME=system|light|dark` override; capture both passive-action modes in both palettes.

## 0.1.0 — 2026-11-12

- Establish the KiCad 11 Schematic Editor IPC plugin scaffold.
- Add local discovery of symbols carrying a configurable LCSC/JLCPCB custom field.
- Add local search, part-ID copying, datasheet opening and schematic symbol selection.
- Add a validated atomic installer and headless unit/package tests.
- Add JLCPCB parts-page and community catalogue adapters with SQLite cache/offline fallback.
- Add asynchronous product-image loading, part details and schematic selection actions.
- Pin the official KiCad Python IPC binding wheels to the kicad-blocks published build.
