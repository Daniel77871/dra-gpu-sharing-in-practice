#!/usr/bin/env python3
"""Convert streamed nvidia-smi CSV into the original evidence keys."""
import csv
import datetime as dt
import json
import sys

KEYS = ('gpu_uuid', 'gpu_name', 'total_mib', 'used_mib', 'free_mib', 'utilization_pct', 'compute_mode')


def convert(values, utc=None):
    if len(values) != len(KEYS):
        raise ValueError('expected seven nvidia-smi fields')
    values = [value.strip() for value in values]
    for value in values[2:6]:
        if float(value) < 0:
            raise ValueError('negative GPU measurement')
    return {'utc': utc or dt.datetime.now(dt.timezone.utc).isoformat(), **dict(zip(KEYS, values))}


if __name__ == '__main__':
    for values in csv.reader(sys.stdin):
        if not values:
            continue
        print(json.dumps(convert(values)), flush=True)
