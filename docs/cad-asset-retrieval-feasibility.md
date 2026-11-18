# Feasibility: automatic KiCad symbol and footprint retrieval

**Research date:** 2026-11-17  
**Scope:** discover, preview, download and use KiCad schematic symbols and PCB footprints for JLCPCB/LCSC catalogue parts when the existing local mappings do not produce a suitable library entry.

## Executive summary

**Feasible as a best-effort, review-first feature; not feasible as a guaranteed match for every catalogue item.**

The strongest external discovery route is EasyEDA, queried by the part's LCSC number. Its web backend exposes component CAD data and SVG preview endpoints, and the open-source `easyeda2kicad` converter demonstrates that the returned data can be converted into KiCad symbol and footprint files. The existing project already has the LCSC identity, asynchronous network work, catalogue candidate selection, a shared preview panel, editable Symbol and Footprint inputs in Add mode, and project-local symbol-library generation.

The work is therefore an extension of the current architecture, not a redesign. The largest decisions are:
1. how to convert/import CAD definitions without accidentally taking on an incompatible licence;
2. how to register a project-local footprint library safely (the current library writer handles symbols only);
3. how to make it explicit that downloaded CAD is a candidate, not proof of electrical or mechanical suitability.

The recommended design is to **search local KiCad libraries first, then query an external CAD provider when no suitable local mapping is found**. Download candidates and previews in the background, populate editable proposals, and defer changing a project library or schematic until the user generates/imports the reviewed component.

## Implementation status (2026-11-18)

The initial credential-free implementation is now present in the plugin:

- Existing KiCad mappings are checked first across installed stock directories, user-global `sym-lib-table` / `fp-lib-table` entries, and the active project's library tables. A public EasyEDA lookup by normalized LCSC code runs only when Add mode has no usable local symbol or a footprint field remains unresolved; Update mode looks up CAD only for an unresolved footprint without changing the selected symbol identity.
- The EasyEDA `/api/products/{LCSC}/components` endpoint supplies component geometry and a public package-detail fallback; `/api/products/{LCSC}/svgs` supplies previews. The preview adapter handles explicit `docType` metadata or the common order of symbol-unit SVGs followed by the footprint SVG. No API key, login, browser cookie or user credential is used.
- Conversion uses the independent MIT-licensed `euc2k` package rather than vendoring the AGPL `easyeda2kicad` converter. SVG preview rasterization uses `resvg_py.svg_to_bytes`, the same renderer API used by the companion `kicad-blocks` repository.
- CAD retrieval and rendering run in a worker. Results are scoped to the selected LCSC code, partial symbol-only results are retained, and dirty-field tracking prevents late responses overwriting manual edits.
- Add mode proposes external `Library:Symbol` and `Library:Footprint` values in the existing editable inputs. External symbols are copied through the existing project-local symbol generator; retrieved footprints are registered in a per-LCSC project-local `.pretty` library and `fp-lib-table` only when the user runs Generate.
- Update mode can preview both candidate assets and prefill a missing footprint, but never replaces the selected schematic symbol or pin mapping.
- The CAD cache is size-bounded, HTTPS-host restricted, redirect-checked, SVG-sanitized and content-hash checked. Imported footprint libraries use ownership manifests and refuse to overwrite foreign, symlinked or modified content. Converter attribution/licence notes are kept in the CAD cache's `CREDITS.txt` and copied beside the project as `JLCPCB_<LCSC>_CREDITS.txt` when imported geometry is accepted.

Coverage remains best-effort: some LCSC parts have no public CAD model, may have only one of symbol/footprint, or may return geometry that the converter rejects. In such cases the manual editable workflow remains available. The initial implementation imports 2D geometry only; it does not infer pin maps or retrieve 3D models.

## What the repository already provides

The following existing components are natural integration points:

| Existing code | Current responsibility | Relevance |
| --- | --- | --- |
| `kicad_jlcpcb/provider.py` | `JlcPartDetails`, LCSC lookup, keyword/MPN search, supplier metadata, SQLite cache and image URLs | Supplies the exact LCSC identifier used for CAD lookup and the background provider pattern. |
| `kicad_jlcpcb/draft.py` | `ComponentDraft`, component classification, suggested `Library:Symbol`, reviewed package-to-footprint mappings | Holds the current proposal values. Unknown/unmapped candidates currently leave Symbol and/or Footprint for manual entry. |
| `kicad_jlcpcb/library.py` | Reads stock `.kicad_sym` files and creates a project-local symbol library; owns safe table updates and atomic writes | Can inform the new importer, but currently only supports `sym-lib-table` and symbols. It does not import footprints or edit `fp-lib-table`. |
| `kicad_jlcpcb/catalogue_widgets.py` | Shared supplier image and catalogue-data preview | Good location/pattern for a CAD preview section, but it currently renders raster supplier images only. |
| `kicad_jlcpcb/populator.py` | Add/Update UI, asynchronous lookups, selection, editable Symbol/Value/Footprint fields and result polling | Main integration point for CAD discovery and Add/Update-specific behavior. |

The current product image path already keeps network work outside the Tk event thread and limits supplier images. CAD fetching should follow the same queued-result pattern, but use separate limits and validation appropriate to CAD files.

## Sources and technical feasibility

### 1. Local KiCad libraries — preferred first pass

KiCad's official symbol and footprint libraries are maintained in the [KiCad libraries repository](https://gitlab.com/kicad/libraries), and are distributed with the KiCad installation. The repository already knows how to locate installed symbol libraries via `find_symbol_library()`.

A local-first resolver can:
- keep the current reviewed mappings as the highest-confidence results;
- optionally search installed stock library names, aliases, keywords and descriptions for additional candidates;
- confirm that a proposed library nickname and item actually exist before presenting it as usable;
- avoid network calls where a valid local entry is already available.

Local search should return candidates rather than silently substituting semantically similar symbols. A text/name match is not evidence that an IC, connector or protection device has the correct pinout.

### 2. EasyEDA CAD data — strongest automatic fallback

The EasyEDA web backend used by `easyeda2kicad` exposes these LCSC-keyed endpoints:

- Component data: `https://easyeda.com/api/products/{lcsc_id}/components`
- SVG previews: `https://easyeda.com/api/products/{lcsc_id}/svgs`

The SVG response can contain symbol-unit previews and a footprint preview. The converter uses the component response to build KiCad symbol and footprint assets. This is a practical match for the current app because catalogue records already carry a normalized LCSC code such as `C123456`.

**Important qualification:** these are website endpoints, not a stable, contracted public CAD API. They may change, throttle or return no model. Not every LCSC part has complete EasyEDA CAD data; a part can have only a symbol, only a footprint, incomplete previews, or no CAD record at all. Search/download must therefore be optional and failures must leave the existing workflow usable.

The [`easyeda2kicad` project](https://github.com/uPesy/easyeda2kicad.py) demonstrates conversion of EasyEDA data into `.kicad_sym`, `.kicad_mod` and optional 3D-model files. Its current distribution is **AGPL-3.0**, whereas this repository is MIT-licensed. Do not copy/vendor its converter code or bundle it as an in-process dependency without an explicit licence decision. A subprocess boundary is an architectural option, but should not be assumed to remove all licence obligations; obtain a review before distributing a bundled converter. A clean-room implementation or a separately licensed conversion component is the alternative.

### 3. Contracted CAD APIs — possible secondary provider

[SnapMagic's API](https://www.snapeda.com/api/) advertises component symbols, footprints and 3D models for KiCad and other EDA tools. Access is application/request based, with free and paid access tiers; this is not equivalent to an anonymous drop-in API. It could be added behind the same provider interface if the project obtains credentials and approves the applicable terms.

### 4. Existing plugin precedent

The third-party [KiCad LCSC Manager](https://github.com/hulryung/kicad-lcsc-manager) demonstrates the intended user experience: LCSC/EasyEDA lookup, symbol and footprint SVG previews, selection of downloadable CAD and import into KiCad libraries. It is useful as a product/UX precedent, not as code to copy. Check its complete dependency and licence chain before reusing any implementation.

## Recommended behavior

### Discovery and matching order

1. **Keep current reviewed mappings first.** Existing resistor/capacitor and other explicit mappings remain authoritative.
2. **Search installed KiCad libraries locally** when classification or package mapping does not resolve to a usable entry. Present any fuzzy result as a candidate with its exact `Library:Symbol` or `Library:Footprint` identifier.
3. **Use the LCSC number for external CAD discovery** when a catalogue part is selected and local resolution did not produce a usable candidate.
4. **Keep symbol and footprint outcomes independent.** One may be available when the other is missing.
5. **Do not invent a result.** On no match, timeout, schema change or invalid asset, leave the existing inputs editable and show a concise unavailable state.

A candidate should carry at least: LCSC ID, source/provider, upstream model ID, library/item names, available asset types, preview availability, download timestamp, content hashes, conversion status and warnings. The source and exact upstream identity should remain visible in the review UI.

### Preview UI

Extend the shared catalogue preview workspace with a CAD-assets section containing two separate preview areas:

- **Schematic symbol:** SVG or rendered image of the symbol, including multiple units when present.
- **PCB footprint:** top-view preview of pads, outline and silkscreen, with the pad count/package identifier where available.

Show each preview only when that asset is available. Include source attribution and a status such as “local KiCad library”, “EasyEDA candidate”, “preview unavailable” or “download/conversion failed”. Do not reuse the supplier product photo as a CAD preview.

The EasyEDA endpoint returns SVG, while the Tk preview path uses Pillow-backed raster image bytes. The implementation uses `resvg_py` (as in `kicad-blocks`) to rasterize sanitized SVG in the CAD worker, then updates Tk widgets on the main thread. When the renderer is unavailable or the SVG cannot be validated, the CAD panels show an explicit unavailable state and the manual workflow remains usable.

### Add mode

- If the local resolver already found a reviewed stock entry, keep that entry selected and skip external discovery unless the user explicitly asks for alternatives.
- If the existing mapping is blank or low confidence, search for CAD in the background using the selected LCSC candidate.
- When a usable symbol and/or footprint is obtained, automatically set the existing editable `KiCad Library:Symbol` and `Footprint` inputs to the proposed imported library identifiers.
- Keep Symbol, Value, Footprint and Reference prefix editable. Manual edits must not be overwritten by late async responses after the user has started editing; track whether each field is still automatic/untouched.
- Download to a cache/staging location for preview. Import/register assets into the project only when the user completes the existing Generate action (or uses a clearly labelled Import action). This avoids mutating project files merely because a search result was highlighted.
- If only one asset is available, fill only that field and leave the other untouched/editable.

### Update mode and Properties views

**Do not replace an already-selected schematic symbol automatically.** The current Update contract explicitly preserves its library identity, graphics, pins, units and electrical pin types. Automatically assigning a different symbol to that selected instance could change the circuit's connectivity and is a substantially different operation from adding catalogue fields.

For Update, CAD discovery may:
- show symbol and footprint candidates in the preview section;
- prefill the editable Footprint proposal when the selected part has a reliable candidate and the user has not manually changed the field;
- offer an explicit “Add as new project symbol” or separate replacement workflow in a later iteration.

Do not replace the selected symbol's `Library:Symbol` identity in the ordinary Update action. Any future symbol replacement must be explicit and require the user to inspect pin numbers, electrical types, units and mapping before committing. The Component Properties view can show read-only previews without changing the schematic.

## Import and project integration

The symbol writer in `library.py` provides ownership checks, safe parsing, atomic file replacement and project-relative paths. The importer now has a matching footprint-registration path:

- Symbol: import/merge a selected definition into an owned project-local `.kicad_sym` library and register it in `sym-lib-table`.
- Footprint: write the `.kicad_mod` into an owned project-local `.pretty/` directory and register it in `fp-lib-table`.
- Set the fields to the exact registered `Library:Item` names, for example `ImportedSymbols:PartName` and `ImportedFootprints:PackageName`.
- Do not overwrite unrelated project libraries, symlinks, user-edited imported definitions or table entries. Use deterministic names, conflict detection, content hashes, backups and atomic writes, following the current symbol-library safeguards.
- Keep source files, metadata and a manifest in a user cache so repeated searches can reuse them and support revalidation. Avoid persisting expiring signed URLs.
- Treat 3D model downloading as a separate optional phase. It is not required to deliver the requested symbol/footprint behavior.

The import process should validate archive/response type, KiCad S-expression roots, required nodes and file-size limits before writing. It should reject path traversal, absolute paths, symlinks and filenames outside the expected library folder. Never blindly extract a supplier ZIP into the project.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| CAD endpoint/schema changes or rate limiting | Provider adapter, short timeouts, bounded retries, source labels, cache and graceful no-result behavior. |
| Missing or partial CAD models | Symbol and footprint treated as independent optional assets; retain manual editable fields. |
| Incorrect or electrically unsafe symbol | Preview and explicit review; never infer an IC pin map from MPN/description; keep Update symbol identity unchanged. Verify against manufacturer datasheet. |
| Footprint/package mismatch | Show package and pad count; do not equate supplier package text with proven footprint compatibility; verify dimensions, pad numbers and thermal/mechanical constraints. |
| AGPL dependency mixed into MIT project | No vendoring/reuse of `easyeda2kicad` code until a licensing decision; investigate independent converter or approved separate distribution. |
| Untrusted downloads / SVG / archive content | HTTPS allowlist, redirect checks, byte and time limits, content-type/signature checks, SVG sanitization, path validation and atomic writes. |
| Existing project library damage | Owned project-local libraries, conflict detection, non-destructive merge, backups and tests for symlink/foreign-file cases. |
| UI freezes or manual edits overwritten | Separate worker, queue results to Tk main thread, cancellation/stale-result guards, field dirty tracking. |

## Suggested implementation stages

### Stage 1 — provider spike

- Verify the EasyEDA component and SVG response shape for a known supported part, a part with only one CAD asset, and a part with no CAD data.
- Save representative sanitized fixtures for tests; do not make tests depend on live supplier availability.
- Decide the converter/licence approach before choosing a runtime dependency.
- Define provider-neutral symbol/footprint candidate models and a cache policy.

### Stage 2 — discovery and previews

- Implement local-first lookup, then the EasyEDA provider adapter keyed by LCSC.
- Download and validate CAD data in the background; cache by upstream identity/content hash.
- Render independent symbol and footprint preview cards in the shared Add/Update preview section.
- Ensure stale async results cannot replace the currently selected part's previews or user-edited inputs.

### Stage 3 — project import

- Implement separate project-local symbol and footprint library writers and table registration.
- Import assets atomically during the reviewed Add/Generate action.
- Populate Symbol and Footprint with the registered identifiers and allow manual override before completion.
- Keep Update mode non-destructive; preview-only by default.

### Stage 4 — integration validation

- Unit tests for no-result, partial-result, malformed-response, large-file, invalid-SVG, path-traversal, library-name collision, symlink and pre-existing user-library cases.
- Tests that prove manual edits survive asynchronous asset responses.
- KiCad CLI validation of imported symbol/footprint libraries, plus Eeschema and PCB Editor integration checks where available.
- Visual CI captures both CAD previews, unavailable states, Add mode and Update mode in light and dark themes.
- Manually verify at least one multi-unit symbol, an IC symbol with many pins, and representative two-pad, diode and multi-pad footprints against the datasheet.

## Recommendation

Proceed with a **small proof of concept**, not a blanket auto-import of every catalogue result. Start with EasyEDA retrieval for exact LCSC IDs, but settle the AGPL conversion question before implementation is distributed. Keep local KiCad matches preferred; treat external CAD as a reviewable fallback; make Add mode prefill editable identifiers; and keep Update mode from changing a selected symbol's identity.

The initial release should target symbol and footprint assets only. 3D models, pin-map inference, automatic electrical validation and guaranteed drop-in-equivalence claims should remain out of scope.
