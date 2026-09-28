"""One-off: BOM-free UTF-8 commit, then push.  Delete after use."""

from __future__ import annotations

import pathlib
import subprocess
import sys

GIT = r"C:\Program Files\Git\cmd\git.exe"
MESSAGE = """Log in to ghcr.io before building, so the push stops getting a 403

The v0.1.0 run built both architectures fine and then failed at the push:

  failed to fetch anonymous token: GET https://ghcr.io/token?scope=
  repository:jermeyhu/jev-gateway:pull,push ... 403 Forbidden

'Anonymous' is the tell: buildx asked ghcr.io for a token with no
credentials at all, because nothing had logged in.  The packages: write
permission only widens what GITHUB_TOKEN is allowed to do, it does not
hand it to the registry.  docker/login-action writes the credentials into
the docker config that the buildx from setup-buildx-action reads, so it has
to run after that step.
"""


def main() -> int:
    message = pathlib.Path(".git/JEV_COMMIT_MSG")
    message.write_bytes(MESSAGE.encode("utf-8"))
    try:
        result = subprocess.run(
            [GIT, "commit", "-q", "-F", str(message)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        message.unlink(missing_ok=True)
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    if result.returncode != 0:
        return result.returncode

    log = subprocess.run(
        [GIT, "log", "-1", "--pretty=%h %s"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(log.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
