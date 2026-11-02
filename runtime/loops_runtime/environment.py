"""Environment passed to local workflow commands, never the whole host environment."""

import os


def command_environment():
    # HOME supports normal local tool configuration; this is not a filesystem sandbox.
    names = ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE",
             "TZ", "SYSTEMROOT", "WINDIR", "PATHEXT")
    return {name: os.environ[name] for name in names if name in os.environ}
