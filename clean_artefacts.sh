#!/usr/bin/env bash
fd --case-sensitive --no-ignore --exclude failsafe.pdf "output_dir|__pycache__|.*pdf|.*epub|.*png|.*jpg|.*jpeg" . -0 | xargs -0 rm -rf --

