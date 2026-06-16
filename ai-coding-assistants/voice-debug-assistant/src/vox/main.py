"""Vox: voice pair debugger for AWS.

Run with: uv run vox

Opens a local server at http://localhost:7860/client where you connect
via browser for mic/speaker access over WebRTC.
"""

import os
import sys


def main():
    # Allow env overrides before importing anything that reads them
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--region" and i < len(sys.argv) - 1:
            os.environ["AWS_REGION"] = sys.argv[i + 1]
        elif arg == "--profile" and i < len(sys.argv) - 1:
            os.environ["AWS_PROFILE"] = sys.argv[i + 1]

    from pipecat.runner.run import main as runner_main

    runner_main()


if __name__ == "__main__":
    main()
