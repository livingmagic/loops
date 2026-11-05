"""Run an ordinary workflow script with a private model RPC channel."""
import os
import runpy
import signal
import sys
import threading
import loops_ai


def main():
    loops_ai._channel = os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8')
    loops_ai._replies = sys.stdin
    # Keep RPC on a non-inherited descriptor; print, os.write and child-process
    # stdout all remain ordinary logs rather than corrupting the RPC frames.
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    sys.argv = sys.argv[1:]
    parent = os.getppid()

    def supervise():
        while not threading.Event().wait(1):
            if os.getppid() != parent:
                os.killpg(os.getpgrp(), signal.SIGTERM)
                return
    threading.Thread(target=supervise, daemon=True).start()
    # Preserve ordinary Python script imports and argv, without inherited PYTHONPATH.
    sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
    runpy.run_path(sys.argv[0], run_name='__main__')


if __name__ == '__main__':
    main()
