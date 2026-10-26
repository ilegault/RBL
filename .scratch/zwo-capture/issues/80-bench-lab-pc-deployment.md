# 80: Bench B3: deploy the ZWO camera to the lab PC, and write the deployment doc

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** no

**Blocked by:** 79

**Spec:** `.scratch/zwo-capture/spec.md`, "Real hardware" → B3; user story 13
**Binding:** `docs/adr/0008-zwo-camera-owned-by-a-capture-process.md` (decision 2),
`docs/ANTIVIRUS_FALSE_POSITIVE.md`

## What to build

Done by the developer on the lab PC.

1. Install ZWO's native USB driver from its `.inf` files through Device Manager (Update driver
   → Browse → Have Disk). No installer `.exe`.
2. Copy the one-folder build (with `ASICamera2.dll` inside, from ticket 76) onto the USB drive
   and run it. Confirm the DLL is still present after the copy and its SHA-256 matches the
   source.
3. Open the ZWO camera from the Overview and record a short session.
4. Write `docs/hardware/zwo-camera-deployment.md`: driver version and where it came from, the
   Device Manager steps, how to confirm it worked (the device name in Device Manager, the
   Preview appearing, the serial shown), and the DLL's SHA-256. Fill the `SHA-256:` line in
   `vendor/zwo/README.md` with the same value.

## Acceptance criteria

- [ ] RBL from the USB drive opens the ZWO camera on the lab PC and records a session.
- [ ] The DLL survived the copy (hash recorded) and nothing was quarantined.
- [ ] `docs/hardware/zwo-camera-deployment.md` exists with the four items of step 4.
- [ ] `vendor/zwo/README.md` carries the SHA-256.

## Gate

None (bench work). If the security script or antivirus removes the DLL, set
`Status: blocked` and record exactly what was removed and when: ADR 0008 decision 2 would then
need revisiting.

## Comments
