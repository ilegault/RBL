# vendor/zwo — ZWO's `ASICamera2.dll`

**What it is.** ZWO's x64 `ASICamera2.dll`, the C library behind every ZWO ASI camera,
from the *ASI Camera SDK for Windows*. RBL uses it through the `zwoasi` Python wrapper
(`zwoasi.init(path)`), always by explicit path. It is used **byte for byte**: never
renamed, repacked, or re-signed, so the `SHA-256:` below identifies exactly what shipped.

**Where it comes from.** ZWO's developer / SDK download page. In the Windows SDK archive
it is the `lib/x64` folder's `ASICamera2.dll`.

**Where to put it.** `vendor/zwo/ASICamera2.dll`. The file is gitignored
(`vendor/zwo/*.dll`) and is never committed; the developer copies it in before building.
`pyinstaller rbl.spec --clean` then places it next to `RBL.exe` in `dist/RBL/`. Without
it the build still succeeds and prints a warning; that build simply cannot open the ZWO
camera.

**In development.** Set the environment variable `RBL_ZWO_SDK_DLL` to the DLL's full
path to override the location.

SHA-256: to be recorded by bench ticket 80
