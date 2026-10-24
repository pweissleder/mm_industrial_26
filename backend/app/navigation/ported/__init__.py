"""Modules ported from the tested prototype (prototypes/unified-nav-voice, branch
noah/unified-nav-voice-poc): geometry, precision router, footprint validation, diagnostics,
official challenge router and the nine benchmark operations. Algorithms are unchanged; each module
docstring lists its port changes. They work on plain building dicts (kit format) and raise
`app.navigation.errors.NavigationError` (alias ModelError). Only `NavigationService` calls them.
"""
