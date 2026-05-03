#!/usr/bin/env bash

THRESHOLD=80
USAGE=$(df / | awk 'NR==2 {gsub("%",""); print $5}')

if [ "$USAGE" -ge "$THRESHOLD" ]; then
  echo "WARNING: Disk usage is ${USAGE}%"
  exit 1
else
  echo "OK: Disk usage is ${USAGE}%"
fi

