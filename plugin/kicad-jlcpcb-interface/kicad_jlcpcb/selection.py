"""Schematic selection and viewport-focus helpers built on KiCad IPC."""

from __future__ import annotations

from typing import Any


def focus_schematic_items(
    schematic: Any,
    symbols: list[Any],
    focus_command_type: Any,
    empty_response_type: Any,
) -> Any:
    """Navigate to the sheet holding these instances and focus their bounding box."""
    if not symbols:
        return schematic
    sheet_path = getattr(symbols[0], "path", None)
    if sheet_path is None or not callable(getattr(schematic, "for_sheet", None)):
        return schematic

    target_schematic = schematic.for_sheet(sheet_path)
    path_proto = getattr(sheet_path, "proto", None)
    if path_proto is None:
        raise RuntimeError(
            "The KiCad Python binding did not expose the target sheet path "
            "protobuf required by FocusOnItems."
        )

    command = focus_command_type()
    command.document.CopyFrom(target_schematic.document)
    command.document.sheet_path.CopyFrom(path_proto)
    for symbol in symbols:
        raw_id = getattr(getattr(symbol, "id", None), "value", None)
        if not raw_id:
            raw_id = str(getattr(symbol, "id", ""))
        command.items.add().value = str(raw_id)
    target_schematic.client.send(command, empty_response_type)
    return target_schematic


def focus_schematic_items_in_editor(schematic: Any, symbols: list[Any]) -> Any:
    """Use Eeschema's FocusOnItems IPC command for the supplied symbol instances."""
    from google.protobuf.empty_pb2 import Empty
    from kipy.proto.common.commands import editor_commands_pb2

    return focus_schematic_items(
        schematic,
        symbols,
        editor_commands_pb2.FocusOnItems,
        Empty,
    )
