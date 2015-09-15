#!/usr/bin/env python
"""
For further documentation, see: https://github.com/jlevy/instaclone
"""

from __future__ import print_function

import logging as log
import argparse
import sys

NAME = "instaclone"
VERSION = "0.2.0"
DESCRIPTION = "instaclone: Fast, cached installations of versioned files"
LONG_DESCRIPTION = __doc__

LOG_STREAM = sys.stderr


def log_setup(level):
  if level == log.DEBUG:
    log.basicConfig(format="%(levelname).1s %(filename)16s:%(lineno)-4d  %(message)s", level=level,
                    stream=LOG_STREAM)
  else:
    log.basicConfig(format="%(message)s", level=level, stream=LOG_STREAM)

    def brief_excepthook(exctype, value, traceback):
      print("error: %s" % value, file=sys.stderr)
      print("(run with --debug for traceback info)", file=sys.stderr)
      sys.exit(2)

    sys.excepthook = brief_excepthook


def main():
  import instaclone

  parser = argparse.ArgumentParser(description=DESCRIPTION, version=VERSION, epilog="\n" + __doc__,
                                   formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument("command", help="%s command" % NAME, choices=instaclone._command_list)
  parser.add_argument("--config", help="config YAML or JSON file to override usual search path")
  parser.add_argument("-f", "--force",
                      help="force operation, clobbering any existing cached or local targets (use with care)",
                      action="store_true")
  parser.add_argument("--debug", help="enable debugging output", action="store_true")
  args = parser.parse_args()

  log_setup(log.DEBUG if args.debug else log.INFO)

  instaclone.run_command(instaclone.Command[args.command], override_path=args.config, force=args.force)


if __name__ == '__main__':
  main()
