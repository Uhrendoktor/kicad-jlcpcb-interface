# Changelog

## Unreleased

- Generalize the unified workflow as **JLCPCB Add or Update Component**. Updating accepts any single editable selected schematic symbol and preserves its library identity, graphics, pins, units and electrical pin types; add mode creates/reuses a project-local symbol copy. Multiple selections and non-symbol selections are rejected.
- Add catalogue search by LCSC code, manufacturer part number and free-text keyword while retaining exact-value filtering for supported resistor/capacitor value queries. Common R/C/L, diode, LED, fuse and crystal categories receive cautious symbol suggestions; ambiguous parts require a manual KiCad `Library:Symbol` choice.
- Generalize project-local symbol generation to copy standalone stock KiCad symbol definitions with their original pin numbers, electrical types, units and graphics. IC pin maps are never inferred from catalogue text. Footprints are optional and can be selected/edited manually when no reviewed mapping exists.
- Make Value and Footprint editable during both update and add review. Preserve uncertain/unknown fields rather than fabricating symbol identities, footprints or pin assignments. Add tests for non-passive classification, arbitrary symbol metadata updates, and multi-pin IC symbol copies.
- Retain Eeschema cursor placement through KiCad's native Add Symbol chooser, with explicit X/Y placement as a fallback.
- Scan LCSC-linked symbols across the complete schematic hierarchy.
- Navigate to a symbol's sheet, zoom to it and select it through KiCad's `FocusOnItems` IPC command.
- Use the configured cache TTL to distinguish recent offline cache results from stale catalogue data.
- Recursively remove expiring signed URLs from persisted catalogue records.
- Capture and verify the live Properties controls and both modes of the unified Add or Update Component dialog in the visual CI fixture.
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
