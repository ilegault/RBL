"""Bench check: which 6482 SCPI commands the RBL driver uses are actually accepted.

Sends each command, then reads :SYSTem:ERRor? so silent -113 errors show up.
Throwaway diagnostic; not part of the app.
"""
import pyvisa

RES = "GPIB0::2::INSTR"
inst = pyvisa.ResourceManager().open_resource(RES)
inst.timeout = 3000
inst.read_termination = "\n"
inst.write_termination = "\n"

def err():
    return inst.query(":SYSTem:ERRor?").strip()

print("IDN :", inst.query("*IDN?").strip())
inst.write("*CLS")

writes = [
    ":SOURce1:STATe OFF",                       # what the driver sends now
    ":SOURce2:STATe OFF",
    ":OUTPut1:STATe OFF",                       # what the 6482 manual documents
    ":OUTPut2:STATe OFF",
    ":SENSe1:FUNCtion 'CURRent:DC'",
    ":SENSe1:CURRent:DC:RANGe:AUTO ON",
    ":SENSe1:CURRent:DC:NPLCycles 1",
    ":SENSe1:MEDian:STATe OFF",
    ":SENSe1:AVERage:STATe OFF",
    ":FORMat:ELEMents READing,TIME,STATus",     # driver's element list
    ":FORMat:ELEMents CURRent1,TIME,STATus",    # manual's element names
]
print("\n--- writes (then error queue) ---")
for cmd in writes:
    inst.write(cmd)
    print(f"{cmd:45s} -> {err()}")

queries = [":OUTPut1?", ":OUTPut2?", ":SOURce1:STATe?", ":FORMat:ELEMents?", ":READ?"]
print("\n--- queries ---")
for q in queries:
    try:
        print(f"{q:45s} <- {inst.query(q).strip()!r}")
    except Exception as exc:
        print(f"{q:45s} !! {type(exc).__name__}: {exc}")
        try:
            inst.clear()
        except Exception:
            pass
    print(f"{'':45s}    err: {err()}")

inst.close()
