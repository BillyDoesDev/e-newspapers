#!/usr/bin/env python3
__all__ = ["render_the_hindu", "render_the_shillong_times", "render_the_times_of_india"]

from .the_hindu.main import build_edition as render_the_hindu
from .the_shillong_times.main import render as render_the_shillong_times
