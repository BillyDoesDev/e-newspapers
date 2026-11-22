#!/usr/bin/env bash
fd --case-sensitive --no-ignore "output_dir|__pycache__|.*pdf|.*epub|.*png|.*jpg|.*jpeg" . | xargs rm -rf
