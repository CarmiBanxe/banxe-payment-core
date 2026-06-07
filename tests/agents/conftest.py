"""Test hygiene for the agents package.

The agents tests are async (pytest-asyncio ``auto`` mode). Under pytest-asyncio
1.x on Python 3.11, the plugin closes the per-test event loop and leaves *no*
current loop on the main thread after each async test. Because ``tests/agents/``
sorts before ``tests/test_adapters.py`` in collection, a sibling sync test there
that calls the (deprecated) ``asyncio.get_event_loop()`` would then raise
``RuntimeError: There is no current event loop``.

This conftest is scoped to ``tests/agents/`` only: it restores a fresh, open
event loop after each of *our* tests so this package never leaves global asyncio
state corrupted for sibling test modules. It touches no other test.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    # Run after all finalizers (incl. pytest-asyncio's loop teardown), then leave
    # a valid current event loop in place for whatever test runs next.
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())
