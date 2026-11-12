# KiCad JLCPCB Interface

A KiCad 11 Schematic Editor IPC add-on for browsing components already placed in a schematic and navigating back to their symbol instances.

**Status: initial scaffold / early prototype.** The first milestone discovers symbols that have a configurable custom field (default: `LCSC`), provides local search, displays the linked identifier and symbol fields, and selects symbols in Eeschema. Live JLCPCB metadata, product photographs, stock/pricing, footprint previews and continuous editor synchronization are planned follow-on work; they are not claimed to work yet.

## Intended workflow

1. Add an `LCSC` custom field to a symbol and set it to an LCSC code (for example `C123456`) or a URL containing that code.
2. Launch **JLCPCB Component Browser** from the Schematic Editor toolbar or Plugins menu.
3. Search the project component list by reference, value, LCSC code or footprint.
4. Select the schematic symbol from the browser, or select matching instances of the same part on the same sheet.
5. Copy the part identifier or open the symbol's datasheet.

The part-link field name is editable in the browser, so projects using another property name (for example `JLCPCB` or `LCSC Part Number`) do not need to rename their fields.

## Architecture

- `plugin/kicad-jlcpcb-interface/plugin.json`: KiCad IPC plugin manifest and schematic action registration.
- `plugin/kicad-jlcpcb-interface/main.py`: thin IPC action entrypoint.
- `plugin/kicad-jlcpcb-interface/kicad_jlcpcb/`: schematic scanning, component model and browser UI.
- `plugin/kicad-jlcpcb-interface/requirements.txt`: ABI-specific binary wheels for the official `kicad-python` IPC bindings.
- `scripts/install_plugin.py`: validates and atomically installs the package into a KiCad version's user plugin directory.
- `tests/`: unit and package/installer tests that do not require a running KiCad process.

The IPC bootstrap follows the working pattern in [kicad-blocks](https://github.com/Uhrendoktor/kicad-blocks): KiCad launches the action as an external Python process, reads `plugin.json`, and manages the plugin's isolated Python environment. This add-on uses its own reverse-DNS ID: `org.uhrendoktor.kicadjlcpcb`.

## Current target

The dependency wheels are pinned to the KiCad Python bindings commit recorded in `plugin/kicad-jlcpcb-interface/kicad-python.lock`. For the initial Linux x86_64 target, the requirements reuse the immutable CPython 3.12/3.13/3.14 wheels published by the kicad-blocks project. The plugin manager installs binary wheels; it will not build the GitLab source dependency itself.

The initial installer supports:
- KiCad 10.99 development nightly (the KiCad 11 pre-release directory), or KiCad 11.0+.
- Linux x86_64.
- CPython 3.12, 3.13 or 3.14, with Tkinter available.

Other operating systems and architectures need matching `kicad-python` wheels and installation/CI validation before they can be advertised as supported.

## Install

1. In KiCad, enable the IPC API in **Preferences → Plugins** and configure the Python interpreter to a supported CPython 3.12–3.14 executable.
2. Close KiCad.
3. From the repository root, run the installer for your KiCad version directory, for example:

   ```bash
   python3.14 scripts/install_plugin.py --version 10.99
   ```

   The installer defaults to `KICAD_DOCUMENTS_HOME`, or `~/.local/share` on Linux. Override the root with `--documents-home PATH`. Use `--dry-run` to validate the package and print the destination without installing it.
4. Restart KiCad and launch **JLCPCB Component Browser** from Eeschema.

The plugin directory is installed to:

```
<KiCad user-data root>/kicad/<version>/plugins/org.uhrendoktor.kicadjlcpcb/
```

KiCad creates the managed environment and installs the dependencies on first discovery. Do not separately pip-install the plugin requirements into that environment. To recreate a failed environment, right-click the add-on action in Preferences → Plugins and choose **Recreate Plugin Environment**.

## Development

Run the checks from the repository root:

```bash
python -m unittest discover -s tests -v
python -m compileall -q plugin tests scripts
ruff check plugin tests scripts
```

Unit tests use fake schematic objects. A live integration test against the targeted KiCad nightly is still required before calling the UI/selection behavior release-ready.

## Planned milestones

- [x] IPC plugin scaffold, action manifest and installer.
- [x] Scan symbol custom fields and list linked project components.
- [x] Filter/search and select schematic symbols.
- [ ] JLCPCB/LCSC data-provider abstraction and cached metadata.
- [ ] Product-image card, manufacturer/package/specification summary.
- [ ] Footprint and 3D model previews.
- [ ] Inventory and pricing, clearly marked with source and retrieval time.
- [ ] Selection synchronization and robust cross-sheet navigation.
- [ ] Live KiCad nightly GUI integration test and reproducible release package.

## License

MIT. See [LICENSE](LICENSE).
