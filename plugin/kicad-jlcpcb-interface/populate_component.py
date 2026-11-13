"""KiCad IPC action entrypoint for populating one selected symbol from LCSC."""

from kicad_jlcpcb.populator import run_component_populator

if __name__ == "__main__":
    run_component_populator()
