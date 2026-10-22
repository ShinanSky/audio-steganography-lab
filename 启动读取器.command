#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
exec /bin/bash ./mac_launch.sh reader
