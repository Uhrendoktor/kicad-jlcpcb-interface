# KiCad JLCPCB Interface

[![CI](https://github.com/Uhrendoktor/kicad-jlcpcb-interface/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Uhrendoktor/kicad-jlcpcb-interface/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![KiCad](https://img.shields.io/badge/KiCad-11%20IPC-3670A0.svg)](https://www.kicad.org/)

**Bring JLCPCB/LCSC catalogue information into KiCad's Schematic Editor.**

Link schematic symbols to LCSC part numbers, check catalogue availability, and add or update components with a review-first workflow that preserves symbol pin definitions.

[Install](#install) · [Quick start](#quick-start) · [User guide](docs/user-guide.md)

> **Supported scope:** a selected editable schematic symbol of any component type can receive reviewed catalogue fields without changing its library symbol or pin mapping. For new parts, the tool suggests stock symbols and footprints where mappings are known; otherwise users can enter a KiCad Library:Symbol, Value and optional Footprint. Project-local copies preserve the chosen stock symbol's pins and graphics. The tool does not infer manufacturer-specific pin maps or generate PCB copper/3D assets.

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

### 3. Add or update any component through one review

JLCPCB Add or Update Component chooses its mode from the Eeschema selection:

- **Update:** select exactly one editable schematic symbol, including an IC, connector, sensor or passive. Look up its LCSC part, review the proposed Value, Footprint, datasheet and metadata, edit the Value/Footprint inputs when needed, then confirm. The existing symbol library identity, graphics, pins and electrical pin types are preserved.
- **Add:** clear the selection, then search by LCSC code, manufacturer part number, component name or another keyword. Passive values such as 10k and 100nF still use exact-value matching. Choose a suggested KiCad symbol or enter a stock Library:Symbol manually, then review/edit Value, optional Footprint and reference prefix before placing a project-local copy.

The same review screen handles both tasks. Uncertain symbol and footprint choices stay editable instead of being guessed silently.

<table>
  <tr>
    <td valign="top" width="50%">
      <strong>Update a selected component</strong>
      <p>Update catalogue properties on one existing symbol without replacing its graphics or pin mapping. A manually entered footprint can fill gaps in catalogue package mapping.</p>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-dark.png">
        <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-light.png" alt="Catalogue properties proposed for a selected schematic component." width="320">
      </picture>
    </td>
    <td valign="top" width="50%">
      <strong>Add a project-local symbol</strong>
      <p>Search by LCSC code or free text, accept a suggested stock symbol or enter a KiCad Library:Symbol, and review the value and optional footprint before placement.</p>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-dark.png">
        <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-light.png" alt="Catalogue candidates and project-local symbol placement options." width="320">
      </picture>
    </td>
  </tr>
</table>

When no catalogue-to-symbol mapping is available, enter the stock KiCad identifier (for example, Amplifier_Operational:LM358). The selected symbol definition is copied into the project-local library with its original pins, units and graphics. This does not validate that the chosen symbol matches the actual orderable part: check pin numbers and electrical types against the manufacturer datasheet. Symbols that extend another library symbol must be replaced with a standalone KiCad symbol choice.

For add mode, placement can use KiCad's native Add Symbol chooser at the cursor on X11 when xdotool is available, or explicit X/Y coordinates in millimetres. The generated project-local symbol can be reused on subsequent runs. Multiple selections are rejected; clear the selection to add a new component.

> **First use in a project:** when the generated-library table entry does not yet exist, the open Eeschema session may not recognize the new library immediately. The library and table entry are saved, but no instance is placed. Close and reopen the project, then repeat the same add-mode search; the existing generated symbol is reused.

For detailed field behavior and troubleshooting, see the **[User guide](docs/user-guide.md)**.
## Data, accuracy and limitations

- **Conservative symbol handling.** Update mode works on one editable selected symbol of any component family and never replaces its graphics, library identity or pin mapping. Add mode suggests common generic symbols (resistor, capacitor, inductor, diode, LED, fuse and crystal) and can clone a manually selected standalone KiCad symbol. A suggestion is not a verified manufacturer pinout.
- **Reviewable footprints.** Reviewed mappings cover common SMD resistor/capacitor/inductor sizes, selected LED sizes and diode packages. Other packages remain blank until entered by the user. Footprints are optional for symbol generation, but required before normal PCB layout.
- **Search behavior.** Add mode supports LCSC-code lookup, keyword search by MPN/device name, and exact nominal-value matching for supported resistor/capacitor values. Other keyword matches are candidates to review, not guaranteed electrical equivalents.
- **Placement.** Cursor placement uses KiCad's native Add Symbol chooser through xdotool on X11 when launched from a focused Eeschema window. Explicit X/Y placement is available as a fallback.
- **No pin-map or PCB asset generation.** The add-on does not infer IC pin assignments/electrical types, create copper footprints or generate 3D models. It copies the symbol pin definitions the user selected; verify symbol and footprint compatibility against the datasheet before board use or ordering.
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

CI checks Python 3.12–3.14 and runs a live visual job with KiCad nightly, Eeschema IPC, Tk and KiCad CLI. The visual job checks the overview and properties views, both modes of Add or Update Component, live selection, field write-back and generated-library placement. Interactive plugin-manager discovery and full GUI navigation through a multi-sheet project remain separate release-validation items.

## Documentation

- **[User guide](docs/user-guide.md)** — all three actions, linking fields, catalogue data, workflows and troubleshooting.
- **[Visual evidence](docs/visual-evidence.md)** — screenshot inventory, real-GUI capture design, provenance and reproduction.
- **[Changelog](CHANGELOG.md)** — implementation history.
- **[License](LICENSE)** — MIT.
