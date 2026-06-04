"""Alarm Clock CLI — a small, dependency-free command-line alarm clock.

Layered design (see spec.md):
    cli -> core -> store
            \\-> model
The watch-loop (runner) is a thin consumer of core + store.
"""

__version__ = "1.0.0"
