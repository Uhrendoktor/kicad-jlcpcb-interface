"""KiCad IPC action entrypoint for adding a new passive from an LCSC number."""

from kicad_jlcpcb.populator import run_component_creator

if __name__ == "__main__":
    run_component_creator()
