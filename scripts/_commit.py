"""One-off: BOM-free UTF-8 commit and push.  Delete after use."""

from __future__ import annotations

import pathlib
import subprocess
import sys

GIT = r"C:\Program Files\Git\cmd\git.exe"
MESSAGE = """Pass multi-line and operator-supplied values through env, not the script

The v0.1.0 publish worked: the image built, pushed, and passed the /healthz
smoke test.  The run was still red because the last step, which only writes
the job summary, failed.

It interpolated steps.meta.outputs.tags straight into a run: block.  That
output is one tag per line, and a ${{ }} expansion is pasted into the shell
verbatim with no escaping, so every line after the first was executed as its
own command.  Both values now travel through the environment, where they stay
single inert strings.

The smoke test had the same shape, with inputs.image-tag in it.  That one
happens to be a single line today, but it is operator-supplied and pasted
unescaped, so it moves to env as well rather than waiting for someone to put
a space or a quote in that field.
"""


def main() -> int:
    message = pathlib.Path(".git/JEV_COMMIT_MSG")
    message.write_bytes(MESSAGE.encode("utf-8"))
    try:
        result = subprocess.run(
            [GIT, "commit", "-q", "-F", str(message)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    finally:
        message.unlink(missing_ok=True)
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    if result.returncode != 0:
        return result.returncode

    for args in (["log", "-1", "--pretty=%h %s"], ["push", "origin", "main"]):
        out = subprocess.run(
            [GIT, *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        tail = (out.stdout or out.stderr or "").strip().splitlines()
        print(tail[-1] if tail else "")
        if out.returncode != 0:
            return out.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
