# uv Windows launcher bases

Unmodified executable fixtures from uv 0.13.1, used as data only on non-Windows
hosts. Native Windows CI executes generated installed console and GUI launchers
through `studio/scripts/project-reconstruction.py`.

Sources:
- https://github.com/astral-sh/uv/blob/0.13.1/crates/uv-trampoline-builder/trampolines/uv-trampoline-x86_64-console.exe
- https://github.com/astral-sh/uv/blob/0.13.1/crates/uv-trampoline-builder/trampolines/uv-trampoline-x86_64-gui.exe

uv is Copyright Astral Software Inc., dual MIT/Apache-2.0 licensed. The MIT license
is retained alongside these fixtures. Tests append resources following the pinned
uv builder/editpe0.2.4 layout, retain executable bytes and the original unrelated
resource section, and distinguish parser evidence from native execution evidence.

SHA-256:

- console: `190471dd3909c6e1f0f8603318728df71477ec8f26c0c305757e94468b64ab8b`
- GUI: `a7d66f5862a23704493446fb509491984d5ceda180491cc9786d7edf1879669e`

uv's Cargo.lock pins editpe0.2.4. Its `set_resource_directory` appends a `.pedata`
section when the existing `.rsrc` cannot grow past `.reloc`, updates SizeOfImage
and the resource directory, and retains StandardHeader.SizeOfInitializedData.
The parser validates that unchanged field against the retained base sections and
keeps it exact; it does not treat it as arbitrary disposable metadata.
