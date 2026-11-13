# KiCad JLCPCB Interface

[![CI](https://github.com/Uhrendoktor/kicad-jlcpcb-interface/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Uhrendoktor/kicad-jlcpcb-interface/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![KiCad](https://img.shields.io/badge/KiCad-11%20IPC-3670A0.svg)](https://www.kicad.org/)

**Bring JLCPCB/LCSC catalogue information into KiCad's Schematic Editor.**

Link schematic symbols to LCSC part numbers, check catalogue availability against your design, and add or update supported passive components without losing circuit context.

[Install](#install) · [Quick start](#quick-start) · [User guide](docs/user-guide.md)

> **Supported scope:** automatic passive handling is limited to two-pin resistors and capacitors whose value can be identified confidently and whose footprint matches a reviewed mapping. The add-on does not generate arbitrary IC symbols, pin maps, PCB footprints or 3D models.

## What it helps you do

| Eeschema action | Why you would use it |
| --- | --- |
| **JLCPCB Component Overview** | Check which schematic symbols are linked to LCSC parts, inspect catalogue details, and estimate cost and stock coverage across the design. |
| **JLCPCB Component Properties** | Inspect the supplier record, pricing, MPN matches and schematic fields for one selected linked symbol. |
| **JLCPCB Add or Update Passive** | Use one review-first workflow to populate a selected generic resistor/capacitor, or add a new project-local passive when nothing is selected. |

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

When a catalogue row needs closer inspection, **Select in schematic** takes you back to the corresponding symbol. The animation shows that transition from a linked resistor in Eeschema to its JLCPCB properties, so you can verify which circuit component the supplier record belongs to.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-workflow.gif" alt="Animation showing a linked resistor being selected in Eeschema and its JLCPCB catalogue-properties window opening." width="440">
</p>

### 3. Add or update a passive through one review

A generic passive already on the schematic and a new part from the catalogue need the same safeguard: review the proposed component data before changing the design. **JLCPCB Add or Update Passive** combines both paths in one Eeschema action. Its mode follows the current selection:

- **Update an existing placeholder:** select exactly one stock `Device:R` or `Device:C`. Enter its LCSC part number, review the proposed fields and warnings, then confirm. Only supported fields are updated; symbol graphics, pins and electrical pin mapping are preserved.
- **Add a new passive:** save the project and clear the selection. Search by LCSC code or a value such as `10k`, `4k7`, `100nF` or `4.7uF`. Review the catalogue matches and placement, then confirm to create or reuse a project-local symbol and place it.

The two review screens below show the outcomes of that same action: enriching an existing placeholder versus creating a new reusable symbol.

<table>
  <tr>
    <td valign="top" width="50%">
      <strong>Update a selected placeholder</strong>
      <p>Use this when the schematic already contains a generic resistor or capacitor and you want to add its LCSC, manufacturer, footprint and other reviewed metadata without changing its electrical symbol.</p>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-dark.png">
        <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-light.png" alt="Screenshot showing the proposed catalogue fields for updating an existing generic resistor." width="320">
      </picture>
    </td>
    <td valign="top" width="50%">
      <strong>Add a new project-local symbol</strong>
      <p>Use this when the part is not yet in the schematic. Search by part number or passive value, compare exact matches, and review where the generated symbol will be placed.</p>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-dark.png">
        <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-light.png" alt="Screenshot showing catalogue matches before placing a reusable passive at the Eeschema cursor." width="320">
      </picture>
    </td>
  </tr>
</table>

With no selection, the action adds a reusable passive through KiCad's native Add Symbol chooser and places it at the mouse cursor. Add mode requires launching from the focused Eeschema window on an X11 desktop with `xdotool` installed; there is no coordinate-placement fallback. Multiple selections and unsupported selected symbols are rejected.

> **First use in a project:** when the generated-library table entry does not yet exist, the open Eeschema session may not recognize the new library immediately. The library and table entry are saved, but no instance is placed. Close and reopen the project, then repeat the same add-mode search; the existing generated symbol is reused.

For detailed instructions, field behavior and troubleshooting, see the **[User guide](docs/user-guide.md)**.

## Data, accuracy and limitations

- **Conservative symbol handling.** Update mode accepts stock generic `Device:R` and `Device:C` placeholders. Add mode generates symbols from KiCad's stock resistor/capacitor graphics and two-pin electrical topology; arbitrary IC pin maps and electrical pin types are not inferred.
- **Value-based search.** Add mode accepts nominal values such as `10k`, `4k7`, `100nF` and `4.7uF`. Results are filtered to exact electrical-value matches with a supported footprint. Basic listings are ranked first; Extended listings remain available as alternatives.
- **Placement.** Add mode is cursor-only: KiCad's native Add Symbol chooser is driven through `xdotool` on X11 when launched from a focused Eeschema window. Coordinate placement is not supported.
- **No PCB asset generation.** The add-on does not create copper footprints, pad mappings or 3D models. Verify the selected footprint and electrical suitability before board use or ordering.
- **Supplier data.** Catalogue detail and search use JLCPCB website endpoints that are not a supported public components API and may change. Source/freshness labels distinguish direct JLCPCB results, community fallbacks and cached snapshots.
- **Indicative availability.** Stock and unit prices are snapshots, not a quote, reservation or purchase guarantee. Related MPN-search results are comparison candidates, not guaranteed drop-in replacements.
- **Separate properties window.** Component Properties is a separate add-on window; the KiCad IPC plugin API does not provide extension tabs inside KiCad's built-in Symbol Properties dialog.

## Development and validation

Run local checks from the repository root:

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests scripts
ruff check plugin tests scripts
```

CI checks Python 3.12–3.14 and runs a live visual job with KiCad nightly, Eeschema IPC, Tk and KiCad CLI. The visual job checks the overview and properties views, both modes of Add or Update Passive, live selection, field write-back and generated-library placement. Interactive plugin-manager discovery and full GUI navigation through a multi-sheet project remain separate release-validation items.

## Documentation

- **[User guide](docs/user-guide.md)** — all three actions, linking fields, catalogue data, workflows and troubleshooting.
- **[Visual evidence](docs/visual-evidence.md)** — screenshot inventory, real-GUI capture design, provenance and reproduction.
- **[Changelog](CHANGELOG.md)** — implementation history.
- **[License](LICENSE)** — MIT.
