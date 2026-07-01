"""Adapters — per-CLI Collector + Injector implementations.

Each adapter implements two roles:
  - Collector: produce a SessionProfile from the CLI's native event stream
               or transcript format.
  - Injector: route an OfferMessage into the CLI's prompt channel.

Core logic (SessionProfile, OfferGate, OfferMessage) is CLI-agnostic and
lives in lib/. Adapters here are thin glue: they translate native events
into SessionProfile JSON and feed it to `skill-profile`, then take the
emitted offer text and inject it via the CLI's native mechanism.
"""

from __future__ import annotations
