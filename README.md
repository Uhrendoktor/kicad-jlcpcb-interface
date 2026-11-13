# KiCad JLCPCB Interface

[![CI](https://github.com/Uhrendoktor/kicad-jlcpcb-interface/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Uhrendoktor/kicad-jlcpcb-interface/actions/workflows/ci.yml)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![KiCad](https://img.shields.io/badge/KiCad-11%20IPC-3670A0.svg)](https://www.kicad.org/)

**Find, inspect and place JLCPCB/LCSC passive components from KiCad's Schematic Editor.**

KiCad JLCPCB Interface connects schematic symbols to JLCPCB/LCSC catalogue records. Search by LCSC part number or passive value, review supplier information, and then populate a compatible placeholder or add a reusable project-local symbol.

[Install the add-on](#install) · [Follow the quick start](#quick-start) · [Read the user guide](docs/user-guide.md)

> **Scope:** automatic populate and create workflows are deliberately limited to supported two-pin resistors and capacitors with a confidently identified value and reviewed footprint mapping. This is not a general-purpose IC symbol generator or a PCB footprint/3D-model generator.

## What you can do

| Eeschema action | Use it to |
| --- | --- |
| **JLCPCB Component Overview** | Find LCSC-linked symbols across the schematic hierarchy, filter the list, inspect catalogue details, and estimate design cost and stock coverage. |
| **JLCPCB Component Properties** | Open a separate window with catalogue details for one selected linked symbol, including supplier image, price breaks and MPN search results. |
| **JLCPCB Populate Selected Symbol** | Review catalogue-derived fields, then update one compatible `Device:R` or `Device:C` placeholder after confirmation. Existing symbol graphics and pin mapping stay intact. |
| **JLCPCB Add Component from LCSC** | Search by LCSC code or resistor/capacitor value, review matching parts, and create a reusable project-local symbol for mouse placement through KiCad's native Add Symbol chooser. |

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
4. Restart KiCad and launch one of the four **JLCPCB** actions from Eeschema.

The installed directory is:

```text
<KiCad user-data root>/kicad/<version>/plugins/org.uhrendoktor.kicadjlcpcb/
```

KiCad creates the managed plugin environment and installs the pinned dependencies when it discovers the add-on. Do not separately pip-install requirements into that managed environment. To repair a failed environment, open **Preferences → Plugins**, right-click the add-on and choose **Recreate Plugin Environment**.

## Quick start

### Inspect an existing design

Add a custom field named `LCSC` to each schematic symbol you want to link, and enter an LCSC code such as `C60489` (a URL containing the code is also accepted). Open **JLCPCB Component Overview** and select a row to load its catalogue details. The part-link field name can be changed in the browser.

### Populate a generic resistor or capacitor

Select one compatible `Device:R` or `Device:C` placeholder and open **JLCPCB Populate Selected Symbol**. Enter an LCSC code, review the proposed fields and warnings, then confirm the update. The action changes fields only; it does not replace symbol graphics, pins or electrical pin mapping.

### Add a reusable passive symbol

Save the project, clear the Eeschema selection, and open **JLCPCB Add Component from LCSC**. Search by LCSC code or a value such as `10k`, `4k7`, `100nF` or `4.7uF`. Review the proposed part, then confirm. KiCad opens its native Add Symbol chooser and attaches the generated component to the mouse cursor; click in the schematic to place it or press Escape to cancel. The add-on creates or reuses a symbol in the project-local `JLCPCB_Generated.kicad_sym` library.

> **First use in a project:** if the project does not yet have the generated-library table entry, the already-open Eeschema session may not recognize the new library. The library and table entry are saved, but no instance is placed. Close and reopen the project, then repeat the same Add Component search; the existing generated symbol is reused.

For the complete action-by-action instructions, catalogue field behavior and troubleshooting, see the **[User guide](docs/user-guide.md)**.

## A look at the interface

The short animation shows a linked schematic symbol being selected and its catalogue details opened. The screenshots show the four actions without taking over the page.

<p align="center">
  <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-workflow.gif" alt="Selecting an LCSC-linked resistor in Eeschema and opening its JLCPCB properties" width="480">
</p>

<table>
  <tr>
    <td align="center" width="50%">
      <strong>Browse linked parts</strong><br>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-dark.png">
        <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-browser-light.png" alt="Component overview with linked parts, design summary and catalogue details" width="320">
      </picture>
    </td>
    <td align="center" width="50%">
      <strong>Inspect a selected part</strong><br>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-properties-dark.png">
        <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/component-properties-light.png" alt="Separate catalogue properties window for a selected LCSC-linked resistor" width="320">
      </picture>
    </td>
  </tr>
  <tr>
    <td align="center">
      <strong>Review fields to populate</strong><br>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-dark.png">
        <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-light.png" alt="Reviewing catalogue fields before populating a generic passive symbol" width="320">
      </picture>
    </td>
    <td align="center">
      <strong>Review a new component</strong><br>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-dark.png">
        <img src="https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/add-component-light.png" alt="Reviewing a new passive symbol before native mouse placement in Eeschema" width="320">
      </picture>
    </td>
  </tr>
</table>

The windows follow the desktop light/dark appearance. Set `KICAD_JLCPCB_THEME=system|light|dark` before launching KiCad to override it. All previews are generated by CI from a real KiCad Eeschema process; catalogue details, stock, price breaks, MPN search results and product imagery are fetched from JLCPCB during capture. See [visual evidence and preview provenance](docs/visual-evidence.md).

## Data, accuracy and limitations

- **Conservative symbol handling.** Populate and create support reviewed resistor/capacitor records only. New symbols clone KiCad's stock `Device:R` or `Device:C` graphics and two-pin electrical topology; arbitrary IC pin maps and electrical pin types are not inferred.
- **Value-based search.** Add Component accepts nominal values such as `10k`, `4k7`, `100nF` and `4.7uF`. Results are filtered to exact electrical-value matches with a supported footprint. Basic listings are ranked first; Extended listings remain available as alternatives.
- **Placement.** Add Component uses only KiCad's native Add Symbol chooser through `xdotool` on X11 when launched from a focused Eeschema window. The add-on has no manual X/Y coordinate-placement mode.
- **No PCB asset generation.** The add-on does not create copper footprints, pad mappings or 3D models. Verify the selected footprint and electrical suitability before board use or ordering.
- **Supplier data.** Catalogue detail and search use JLCPCB website endpoints that are not a supported public components API and may change. Source/freshness labels distinguish direct JLCPCB results, community fallbacks and cached snapshots.
- **Indicative availability.** Stock and unit prices are snapshots, not a quote, reservation or purchase guarantee. Related MPN-search results are comparison candidates, not guaranteed drop-in replacements.
- **Project-local library setup.** The first generated-library registration in an already-open project may require a close/reopen and retry, as described in the quick start.

## Development and validation

Run local checks from the repository root:

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests scripts
ruff check plugin tests scripts
```

CI checks Python 3.12–3.14 and runs a live visual job with KiCad nightly, Eeschema IPC, Tk and KiCad CLI. The visual job captures the overview, selected-properties view, populate proposal/result and add-component proposal/result; it also checks live selection, copy/link actions, field write-back and generated-library placement. Interactive plugin-manager discovery and full GUI navigation through a multi-sheet project remain separate release-validation items.

## Documentation

- **[User guide](docs/user-guide.md)** — all four actions, linking fields, catalogue data, workflows and troubleshooting.
- **[Visual evidence](docs/visual-evidence.md)** — screenshot inventory, real-GUI capture design, provenance and reproduction.
- **[Changelog](CHANGELOG.md)** — implementation history.
- **[License](LICENSE)** — MIT.
