# Copyright (C) 2026 Jabez Winston
# SPDX-License-Identifier: MIT
# Date : 17-June-2026
"""
Grouped event logging for the device examples - a console helper (like pcap is a
capture helper), not part of the USB/IP protocol.

A GroupedLog is a callable you wire to a device's on_event / on_command callback.
By default it collapses consecutive identical events into a single summary line
(e.g. "READ(10) ×42, 168 KiB"), logged through the stdlib logging module, so a
high-traffic device doesn't flood the terminal; verbose=True logs every event (at
DEBUG) instead. Status / one-off lines should be logged directly via
logging.getLogger(name) so they print immediately rather than being grouped.

    log  = logging.getLogger("msc")                 # status lines
    sink = usbip.GroupedLog("msc", verbose=args.verbose)
    dev.add(MSC(..., on_command=sink))          # grouped event stream
"""

from __future__ import annotations

import logging
import re

_BYTES = re.compile(r"\((\d+)B\)")  # a trailing "(NNNB)" byte count


class GroupedLog:
    """Collapse consecutive same-key device events into one summary line via logging.

    name    : logger name (also the "[name]" tag, via the example's log format).
    verbose : log every event at DEBUG instead of grouping.
    key     : map an event line to its grouping key (default: text up to the first
              " ("), so a "(NNNB)" suffix is summed across the run, not part of the key.
    skip    : optional predicate to drop noisy events entirely while grouping
              (e.g. status polls); ignored in verbose mode.
    """

    def __init__(self, name, verbose=False, key=None, skip=None):
        self.log = logging.getLogger(name)
        self.verbose = verbose
        self._key_fn = key or (lambda text: text.split(" (")[0])
        self._skip = skip
        self._key = self._first = None
        self._n = self._nbytes = 0

    def flush(self):
        """Emit the pending grouped summary (call before exit / when a stream stops)."""
        if not self._n:
            return
        if self._n == 1:
            self.log.info(self._first)
        else:
            extra = f", {self._nbytes // 1024} KiB" if self._nbytes else ""
            self.log.info(f"{self._key} ×{self._n}{extra}")
        self._key = self._first = None
        self._n = self._nbytes = 0

    def __call__(self, text):
        if self.verbose:
            self.log.debug(text)
            return
        if self._skip and self._skip(text):
            return
        key = self._key_fn(text)
        if key != self._key:
            self.flush()
            self._key, self._first = key, text
        self._n += 1
        match = _BYTES.search(text)
        if match:
            self._nbytes += int(match.group(1))
