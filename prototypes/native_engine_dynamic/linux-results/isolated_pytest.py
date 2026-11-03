"""Start pytest without site hooks; retain a real entry point for multiprocessing.

Invoke from the repository with its venv Python using -I -S. Site packages are
added as a plain path, without processing .pth files or sitecustomize. This uses
the same startup isolation as managed execution children; it changes no policy.
"""

from pathlib import Path
import sys
import sysconfig


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    sys.path[:0] = [sysconfig.get_path("purelib"), str(root / "src")]
    assert sys.flags.isolated and sys.flags.no_site and "sitecustomize" not in sys.modules
    import pytest

    raise SystemExit(pytest.main(sys.argv[1:]))
