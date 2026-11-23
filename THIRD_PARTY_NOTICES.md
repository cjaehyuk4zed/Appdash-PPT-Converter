# Third-party components

Dependencies are installed separately by pip/uv. Their complete license and copyright notices remain in their installed wheel distributions, including native-code notices. Keep those installed notices when distributing a container or runtime; do not strip `.dist-info/licenses` or PDFium's bundled license files.

| Component | License / notice source |
|---|---|
| Starlette, Uvicorn | BSD-3-Clause; installed package notices |
| python-multipart | Apache-2.0; installed package notices |
| Pillow | MIT-CMU / HPND and bundled codec notices |
| pypdfium2 | Apache-2.0 / BSD-3-Clause; PDFium BSD-style and bundled third-party notices |
| python-pptx | MIT; installed package notices |
| pywin32 (Windows only) | Python Software Foundation / bundled notices; installed package notices |
| olefile | BSD-2-Clause; installed package notices |
| pillow-heif | BSD-3-Clause; bundled libheif/codec licenses also apply |
| lxml, XlsxWriter, and other transitive dependencies | Installed distribution license and copyright notices |

Microsoft PowerPoint is a separately installed, licensed application; it is not bundled. LibreOffice (MPL-2.0 and its own third-party notices) is installed separately, including by the Dockerfile. Preserve its distribution notices and font licenses when distributing a runtime. Development-only dependencies include pytest and HTTPX; their licenses are included with their installed packages.
