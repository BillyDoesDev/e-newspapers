#!/usr/bin/env python3
__all__ = [
    "render_anandabazar_patrika",
    "render_the_hindu",
    "render_the_shillong_times",
    "render_the_times_of_india",
    "render_times_of_india",
    "render_uttarbangasambad",
]

from .anandabazar_patrika.main import render as render_anandabazar_patrika
from .the_hindu.main import build_edition as render_the_hindu
from .the_shillong_times.main import render as render_the_shillong_times
from .times_of_india.main import render as render_times_of_india
from .uttarbangasambad.main import render as render_uttarbangasambad
