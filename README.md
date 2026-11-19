# KiCad JLCPCB Interface

[![CI](https://github.com/Uhrendoktor/kicad-jlcpcb-interface/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Uhrendoktor/kicad-jlcpcb-interface/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![KiCad](https://img.shields.io/badge/KiCad-11%20IPC-3670A0.svg)](https://www.kicad.org/)

**Bring JLCPCB/LCSC catalogue information into KiCad's Schematic Editor.**

Link schematic symbols to LCSC part numbers, check catalogue availability, and add or update components with a review-first workflow that preserves symbol pin definitions.

[Install](#install) · [Quick start](#quick-start) · [User guide](docs/user-guide.md)

> **Supported scope:** a selected editable schematic symbol of any component type can receive reviewed catalogue fields without changing its library symbol or pin mapping. For new parts, the tool prefers reviewed/local KiCad mappings, then offers credential-free EasyEDA CAD discovery by LCSC number when a symbol or footprint is unresolved. Retrieved symbol and footprint candidates are previewed and proposed in editable fields before project-local import. When EasyEDA supplies a STEP model, the tool downloads it, shows a KiCad-rendered 3D preview and imports it with the accepted footprint. It does not infer manufacturer-specific pin maps or create missing 3D geometry.

## What it helps you do

| Eeschema action | Why you would use it |
| --- | --- |
| **JLCPCB Component Overview** | Check which schematic symbols are linked to LCSC parts, inspect catalogue details, and estimate cost and stock coverage across the design. |
| **JLCPCB Component Properties** | Inspect the supplier record, pricing, MPN matches and schematic fields for one selected linked symbol. |
| **JLCPCB Add or Update Component** | Update catalogue fields on one selected symbol without changing its pinout, or add a project-local copy of a suggested/manually selected KiCad symbol when nothing is selected. |

## Install

### Supported environment

The currently validated target is:

- KiCad 10.99 development nightly (the KiCad 11 pre-release directory) or KiCad 11.0+.
- Linux x86_64.
- CPython 3.12, 3.13 or 3.14, with Tkinter available.

Other operating systems and CPU architectures are not currently advertised as supported; they need matching `kicad-python` wheels and installation/CI validation.

### Steps

1. In KiCad, open **Preferences → Plugins**, enable the IPC API and select a supported CPython 3.12–3.14 interpreter.
2. Close KiCad.
3. From the repository root, run the installer for your KiCad version directory. For the 10.99 nightly directory:

   ```bash
   python3.14 scripts/install_plugin.py --version 10.99
   ```

   For KiCad 11, use the installed version directory, for example `--version 11.0`. Run the command with a compatible Python interpreter. The installer defaults to `KICAD_DOCUMENTS_HOME`, or `~/.local/share` on Linux. Use `--documents-home PATH` to override the user-data root, or `--dry-run` to validate the package and print the destination without installing.
4. Restart KiCad and launch one of the three **JLCPCB** actions from Eeschema.

The installed directory is:

```text
<KiCad user-data root>/kicad/<version>/plugins/org.uhrendoktor.kicadjlcpcb/
```

KiCad creates the managed plugin environment and installs the pinned dependencies when it discovers the add-on. Do not separately pip-install requirements into that managed environment. To repair a failed environment, open **Preferences → Plugins**, right-click the add-on and choose **Recreate Plugin Environment**.

## Quick start

### 1. Link schematic symbols to LCSC records

Add a custom field named `LCSC` to each symbol you want to include. Enter an LCSC code such as `C60489`; a URL containing that code is also accepted. The browser can use a different part-link field name when a project already uses one.

### 2. Check catalogue coverage before ordering

A part link is useful only when you can see how it affects the whole design. **JLCPCB Component Overview** scans linked symbols across the schematic hierarchy, shows repeated instances and catalogue details, and estimates whether returned stock covers the required quantities. This helps you spot missing links or potential shortages before preparing an order.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-dark.png">
    <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-light.png" alt="Screenshot showing linked schematic references, catalogue details, and the project's estimated cost and stock coverage." width="440">
  </picture>
</p>

When a catalogue row needs closer inspection, **Select in schematic** takes you back to the corresponding symbol. The screenshot shows the linked resistor selected in Eeschema beside its JLCPCB properties, so you can verify which circuit component the supplier record belongs to.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/schematic-properties-opened.png" alt="Linked resistor selected in Eeschema with its JLCPCB Component Properties window open." width="440">
</p>

### 3. Use the unified Add or Update Component action

**JLCPCB Add or Update Component is one Eeschema action, not two separate tools.** It chooses what to do from the current schematic selection:

| Selection when the action starts | Mode | What happens |
| --- | --- | --- |
| Exactly one editable schematic symbol | **Update** | Look up its LCSC part and review catalogue fields for that symbol. Its library identity, graphics, pins, units and electrical pin types are preserved. This works for ICs, connectors, sensors, passives and other symbol families. |
| Nothing selected | **Add** | Search the catalogue, review the suggested symbol and fields, then create and place a project-local copy of a KiCad stock symbol. |
| Multiple items or a non-symbol object | — | The action stops and asks you to select one symbol or clear the selection. |

Both contexts use the same review-first dialog, which shows inference confidence, catalogue source/freshness and warnings. Its heading and available controls adapt to the selection: update mode edits catalogue fields on the selected symbol; add mode exposes symbol overrides only when automatic mapping is uncertain. Routine resistor/capacitor value searches select the stock symbol and reference prefix automatically. Value and Footprint remain editable; Footprint is optional during symbol creation.

The screenshots below are full-height captures of the production Add/Update window.
They include the complete scrollable workspace in one image, rather than separate
screenshots for the top and lower scroll positions. Open an image at full size to read
the fields and review notes.

### One symbol selected · update its catalogue fields

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-dark.png">
    <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-light.png" alt="Full-height Update Component window showing the complete review workspace and fixed action footer." width="100%">
  </picture>
</p>

### No symbol selected · add a project-local copy

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-dark.png">
    <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-light.png" alt="Full-height Add Component window showing catalogue details, CAD preview tabs with the 3D model selected, KiCad fields and the fixed action footer." width="100%">
  </picture>
</p>

The EasyEDA previews share one compact tab strip: **Symbol**, **Footprint** and
**3D model**. When a STEP model is available and KiCad can render it, the 3D tab is
selected automatically; otherwise the best available 2D tab is selected. The preview
hides the footprint reference designator so it does not appear as silkscreen over the
model. This is a preview-only change: the imported footprint retains its original
reference field and silkscreen settings.

For exact resistor/capacitor values such as `10k`, `4k7` or `100nF`, Add mode shows at most five exact matches. Basic listings rank first across passive-value and general keyword searches. The **Basic parts only** checkbox hides Extended and unclassified results; leaving it unchecked keeps those alternatives available behind Basic parts. A direct LCSC-number lookup always returns the exact requested record. Selecting a passive automatically chooses `Device:R` or `Device:C`, its reference prefix, a reviewed package-to-footprint mapping when available, and the catalogue fields. You can still edit Value and Footprint. For other part searches, the suggested generic stock symbol remains editable; enter a KiCad identifier manually (for example, `Amplifier_Operational:LM358`) whenever the mapping is uncertain. The project-local library copies the chosen standalone symbol's original pins, units and graphics; it does not infer a manufacturer-specific pinout.

Clicking **Generate & select component** immediately uses KiCad schematic IPC to insert the generated project-local symbol at the current sheet center, bring it into view and select it—without a second confirmation window. Use KiCad's Move tool to position it where needed. No X/Y placement fields or coordinate entry are exposed.

> **First use in a project:** when the generated-library table entry does not yet exist, the open Eeschema session may not recognize the new library immediately. The library and table entry are saved, but no instance is placed. Close and reopen the project, then repeat the same add-mode search; the existing generated symbol is reused.

For detailed field behavior and troubleshooting, see the **[User guide](docs/user-guide.md)**.
## Data, accuracy and limitations

- **Conservative symbol handling.** Update mode works on one editable selected symbol of any component family and never replaces its graphics, library identity or pin mapping. Add mode suggests common generic symbols (resistor, capacitor, inductor, diode, LED, fuse and crystal) and can clone a manually selected standalone KiCad symbol. A suggestion is not a verified manufacturer pinout.
- **Reviewable footprints.** Reviewed mappings cover common SMD resistor/capacitor/inductor sizes, selected LED sizes and diode packages. When no mapping is available, a best-effort public EasyEDA lookup can supply a footprint candidate and SVG preview. The Footprint field remains editable, and downloaded geometry is registered in a project-local `.pretty` library only when the user accepts the candidate through Add or Update. Footprints are optional for symbol generation, but required before normal PCB layout.
- **Search behavior.** Add and Update modes support LCSC-code and keyword searches. The primary search field accepts a passive value, part number or keyword. Basic listings are ranked first for both passive-value and general keyword searches. The **Basic parts only** checkbox can exclude Extended and unclassified listings; it is off by default so alternatives remain available. Exact LCSC-code lookups remain exact. Optional package/KiCad footprint and tolerance filters live in a collapsed, one-row Advanced filters strip. Its two editable dropdowns use built-in common-value suggestions, not live catalogue options; custom values can be typed. Active constraints are summarized beside the funnel icon. Choosing a preset does not search automatically: Search or Enter sends the main query to JLCPCB and applies the package/tolerance constraints to the returned records. Up to five matching passive candidates are shown; Update alternatives populate the proposal without replacing the selected symbol. Other keyword matches are candidates to review, not guaranteed electrical equivalents.
- **Placement.** Add mode inserts the generated symbol at the schematic page center through IPC, focuses it in Eeschema and leaves it selected. Move it to its final position with KiCad's normal Move tool; no coordinates are requested.
- **Reviewable 3D assets, not generated models.** When the public EasyEDA package metadata contains a 3D-model UUID, the add-on fetches the STEP file, attaches a project-relative model reference to the candidate footprint and attempts a native KiCad 3D render for review. Parts without an upstream STEP model remain 2D-only. No 3D geometry is synthesized, and model alignment/material details are not guaranteed. External symbols, footprints and model attribution must be checked against the manufacturer datasheet before board use or ordering.
- **Supplier and CAD data.** JLCPCB catalogue detail/search and EasyEDA CAD discovery use public website endpoints, not contracted public component APIs; they may change or rate-limit requests. EasyEDA CAD lookup requires no credentials. Source/freshness and CAD preview labels distinguish supplier catalogue data from external CAD candidates.
- **Indicative availability.** Stock and unit prices are snapshots, not a quote, reservation or purchase guarantee. Related MPN-search results are comparison candidates, not guaranteed drop-in replacements.
- **Separate properties window.** Component Properties is a separate add-on window; the KiCad IPC plugin API does not provide extension tabs inside KiCad's built-in Symbol Properties dialog.

## Development and validation

Run local checks from the repository root:

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests scripts
ruff check plugin tests scripts
```

CI checks Python 3.12–3.14 and runs a live visual job with KiCad nightly, Eeschema IPC, Tk and KiCad CLI. The visual job checks the overview and properties views, both modes of Add or Update Component, live selection, field write-back and generated-library placement. Interactive plugin-manager discovery and full GUI navigation through a multi-sheet project remain separate release-validation items.

## Documentation

- **[User guide](docs/user-guide.md)** — all three actions, linking fields, catalogue data, workflows and troubleshooting.
- **[Visual evidence](docs/visual-evidence.md)** — screenshot inventory, real-GUI capture design, provenance and reproduction.
- **[CAD asset retrieval](docs/cad-asset-retrieval-feasibility.md)** — provider behavior, credential-free access, licensing, preview UI and safe project-library import design.
- **[Changelog](CHANGELOG.md)** — implementation history.
- **[License](LICENSE)** — MIT.
