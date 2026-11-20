# UI polish and component-search TODOs

This is the persistent checklist for the UI, layout, component matching, and review-safety improvements requested for the KiCad JLCPCB Interface.

**Status:** Checked items are implemented and validated. Unchecked items remain open and will be completed incrementally.

## TODOs

- [x] **Fix Add-mode layout when CAD previews are unavailable.** When Add does not show a footprint or other CAD preview, reclaim the unused preview space instead of leaving an empty panel/margin. At minimum, the Properties section should expand cleanly to the right edge without a conspicuous blank gap.

- [x] **Redesign the preview tab strip.** Remove the unattractive heavy tab borders, prevent the selected tab from shrinking, and give the active tab a clearer, more polished highlight.

- [x] **Unify section-header styling.** Use one consistent visual treatment for headers across the different sections. Eliminate the current mix of small/bold blue headers and larger white headers.

- [x] **Restore card/container grouping in Component Overview.** Put the Properties and Prices content back inside clearly defined boxes/cards; they currently appear outside any box.

- [x] **Align Component Overview rows.** Make the left inset/padding consistent across all rows so that different rows do not start at different horizontal positions.

- [x] **Broaden alternative matches for simple passive components.** For resistors, capacitors, and similar simple parts, do not rely exclusively on MPN search for the alternatives list. When MPN lookup returns fewer than five results, also search for other listings matching the component type, value, and footprint, and use those to fill the remaining results where available.

- [x] **Keep part actions inside the Selected Part box.** Place the “Open in LCSC” action and part-ID copy affordance in the Selected Part box. The displayed part ID itself should be clickable and copy its ID to the clipboard, replacing the need for a separate “Copy Part Id” button. Keep the existing JLCPCB link/action in that box as well.

- [x] **Correct the false footprint warning for standard KiCad library footprints.** Do not warn that a footprint is unavailable or external when the exact footprint is part of KiCad's standard footprint library. Recheck the warning shown in this preview: [populate-component-dark.png](https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-dark.png).

- [x] **Preview and warn about compatibility-impacting changes in Update mode.** When an update would change the footprint, schematic symbol, or 3D model, show previews of the proposed change(s) and a clear warning that applying the update may break compatibility with the current schematic.

- [ ] **Make scrollbars less intrusive.** Reduce their visual bulk and prominence. Investigate an auto-hide behavior that keeps the scrollbar hidden while idle and reveals it during scrolling, while retaining usable scrolling controls.

## Progress

- **Add-mode empty CAD preview layout — complete.** The CAD pane remains collapsed while EasyEDA is loading and when returned assets have no renderable Symbol, Footprint or 3D preview image. When a rendered preview is available, the two-column layout is restored. Verified with regression tests and the successful live KiCad CI capture on commit `d8754913886bb14e4c98c09b95b8c4c1e85d39d2`.

- **Preview tab strip — complete.** Removed the heavy tab border treatment, retained identical selected/unselected tab padding, and added a stronger selected-state accent. Verified in the rendered dark-theme Add view and by the successful full CI/live KiCad visual workflow on commit `243acdfc9408daf1db54a7130fcd13fb8e59bc77`.

- **Section-header styling — complete.** Replaced the mixed eyebrow/card/label-frame treatments with shared heading typography across the overview, properties, and Add/Update views. Page and card surfaces retain their own backgrounds; metadata labels remain compact. The live visual workflow asserts equal heading fonts and foregrounds across the styles, and the latest rendered captures were inspected.

- **Component Overview cards — complete.** Put catalogue details (including price breaks) and schematic properties inside shared card containers with consistent padding, heading styles, and card-surface content. The live visual workflow asserts both containers use `Card.TFrame`; the rendered overview capture was inspected.

- **Component Overview row alignment — complete.** The catalogue price/attribute rows and schematic-property labels use the same card padding and are measured in the live capture; the workflow asserts their rendered left edges are identical. The latest dark-theme overview screenshot was inspected.

- **Passive-component alternatives — complete.** When fewer than five MPN candidates are available, resistor and capacitor listings are supplemented by exact value/type/footprint matches from keyword search. MPN-ranked rows remain first, duplicates are removed, and the list is capped at five. Regression tests cover sparse resistor results, capacitor searches without an MPN, mismatched package/type exclusion, deduplication, and the five-result cutoff. Python 3.12–3.14, 210 unit tests, Ruff, and live KiCad visual CI passed on commit `8fa2817e6a4f4edcc66af718897de23f84ace69f`.

- **Selected Part actions — complete.** The normalized LCSC ID is shown as an underlined mouse/keyboard-clickable copy control; “Open in LCSC” is inside the Selected Part card beside the existing JLCPCB link. The separate Copy part ID and Search LCSC buttons were removed. Live capture asserts card containment, active LCSC action and copy bindings; the latest dark-theme screenshot was inspected.

- **Standard KiCad footprint warning — complete.** The live Update capture now resolves `Resistor_SMD:R_0402_1005Metric` and asserts the inline availability warning remains hidden. The root cause in CI was an omitted `kicad-nightly-footprints` package, so the live workflow and reproduction instructions now install it. The resolver also handles KiCad IPC's numeric-key path map for stock global-table URIs and remains fail-closed for project/custom URI overrides. Python 3.12–3.14, 212 unit tests, Ruff and the live screenshot job all passed on commit `6aa03486e4a0d756cbbbb19f9055ddd09ff8a626`.

- **Update-mode compatibility previews — complete.** The original footprint is tracked separately from the proposal. When the proposal changes, the EasyEDA Symbol/Footprint/3D comparison is requested even if the target stock footprint already exists, without silently replacing a valid local mapping. A warning identifies the old and proposed footprint and explains that the selected schematic symbol, library identity, graphics and pin map are preserved, while pad count/numbering, pad positions, courtyard and 3D fit can change. The live fixture resets R1 to 0603 after data preload, selects a 0402 catalogue proposal and asserts the warning plus the preview request; the final Update screenshot shows the warning indicator and CAD/3D tabs. Python 3.12–3.14, 217 unit tests, Ruff and live KiCad screenshot CI passed on commit `c8e3177b56c5a003b711aadf7660089e8cbde535`.

## Completion rule

Keep each item unchecked until its implementation is complete and has been validated. For visual changes, inspect the rendered UI in the affected views rather than relying on source-level review alone. Update this file as work progresses so the full request remains visible across incremental changes.
