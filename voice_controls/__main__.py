"""Responsibility: Module launcher for `python -m voice_controls`."""

from .app import main  # Delegate module execution to the shared app entrypoint.


if __name__ == "__main__":
    raise SystemExit(main())
