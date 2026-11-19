# UI polish and component-search TODOs

This is the persistent checklist for the UI, layout, component matching, and review-safety improvements requested for the KiCad JLCPCB Interface.

**Status:** All items are intentionally unchecked. This tracking change records the work; it does not claim that any of the fixes below have been implemented.

## TODOs

- [x] **Fix Add-mode layout when CAD previews are unavailable.** When Add does not show a footprint or other CAD preview, reclaim the unused preview space instead of leaving an empty panel/margin. At minimum, the Properties section should expand cleanly to the right edge without a conspicuous blank gap.

- [x] **Redesign the preview tab strip.** Remove the unattractive heavy tab borders, prevent the selected tab from shrinking, and give the active tab a clearer, more polished highlight.

- [ ] **Unify section-header styling.** Use one consistent visual treatment for headers across the different sections. Eliminate the current mix of small/bold blue headers and larger white headers.

- [ ] **Restore card/container grouping in Component Overview.** Put the Properties and Prices content back inside clearly defined boxes/cards; they currently appear outside any box.

- [ ] **Align Component Overview rows.** Make the left inset/padding consistent across all rows so that different rows do not start at different horizontal positions.

- [ ] **Broaden alternative matches for simple passive components.** For resistors, capacitors, and similar simple parts, do not rely exclusively on MPN search for the alternatives list. When MPN lookup returns fewer than five results, also search for other listings matching the component type, value, and footprint, and use those to fill the remaining results where available.

- [ ] **Keep part actions inside the Selected Part box.** Place the “Open in LCSC” action and part-ID copy affordance in the Selected Part box. The displayed part ID itself should be clickable and copy its ID to the clipboard, replacing the need for a separate “Copy Part Id” button. Keep the existing JLCPCB link/action in that box as well.

- [ ] **Correct the false footprint warning for standard KiCad library footprints.** Do not warn that a footprint is unavailable or external when the exact footprint is part of KiCad's standard footprint library. Recheck the warning shown in this preview: [populate-component-dark.png](https://raw.githubusercontent.com/Uhrendoktor/kicad-jlcpcb-interface/refs/heads/docs/previews/previews/populate-component-dark.png).

- [ ] **Preview and warn about compatibility-impacting changes in Update mode.** When an update would change the footprint, schematic symbol, or 3D model, show previews of the proposed change(s) and a clear warning that applying the update may break compatibility with the current schematic.

- [ ] **Make scrollbars less intrusive.** Reduce their visual bulk and prominence. Investigate an auto-hide behavior that keeps the scrollbar hidden while idle and reveals it during scrolling, while retaining usable scrolling controls.

## Progress

- **Add-mode empty CAD preview layout — complete.** The CAD pane remains collapsed while EasyEDA is loading and when returned assets have no renderable Symbol, Footprint or 3D preview image. When a rendered preview is available, the two-column layout is restored. Verified with regression tests and the successful live KiCad CI capture on commit `d8754913886bb14e4c98c09b95b8c4c1e85d39d2`.

- **Preview tab strip — complete.** Removed the heavy tab border treatment, retained identical selected/unselected tab padding, and added a stronger selected-state accent. Verified in the rendered dark-theme Add view and by the successful full CI/live KiCad visual workflow on commit `243acdfc9408daf1db54a7130fcd13fb8e59bc77`.

## Completion rule

Keep each item unchecked until its implementation is complete and has been validated. For visual changes, inspect the rendered UI in the affected views rather than relying on source-level review alone. Update this file as work progresses so the full request remains visible across incremental changes.
