#!/usr/bin/env python
"""
For further documentation, see: https://github.com/jlevy/instaclone
"""

from __future__ import print_function

import logging as log
import argparse
import sys

NAME = "instaclone"
VERSION = "0.2.2"
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
  import configs

  config_docs = "Put configuration settings into an instaclone.{yml,json} file. Setting file keys:\n\n%s\n%s" % (
    "\n".join(["  %s: %s" % (k, v) for (k, v) in configs.CONFIG_DESCRIPTIONS.iteritems()]),
    "\nSettings may be overridden with corresponding command-line args.\n")

  parser = argparse.ArgumentParser(description=DESCRIPTION, version=VERSION, epilog="\n" + config_docs + __doc__,
                                   formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument("command", help="%s command" % NAME, choices=instaclone._command_list)
  parser.add_argument("items", help="optional subset of local paths to install", nargs="*")
  parser.add_argument("--config", help="YAML or JSON file to use (overrides usual search path)")
  parser.add_argument("-f", "--force",
                      help="force operation, clobbering any existing cached or local targets (use with care)",
                      action="store_true")
  parser.add_argument("--copy",
                      help="override: use install_method=fastcopy for all items",
                      action="store_true")
  parser.add_argument("--debug", help="enable debugging output", action="store_true")

  # XXX Unfortunately the setting "version" conflicts with argparse's --version.
  for (key, desc) in configs.CONFIG_DESCRIPTIONS.iteritems():
    if key == "version":
      parser.add_argument("--version-string", metavar="S", help="setting override (single item)")
    else:
      parser.add_argument("--" + key.replace("_", "-"), metavar="S", help="setting override (single item)")

  args = parser.parse_args()

  overrides = {}
  for key in configs.CONFIG_OVERRIDABLE:
    if key == "version":
      continue
    value = args.__dict__.get(key)
    if value is not None:
      if len(args.items) == 1:
        overrides[key.replace("-", "_")] = value
      else:
        raise ValueError("Must specify just one item when using override '%s'" % key)

  # These overrides can be applied to _all_ items.
  if args.copy:
    overrides["install_method"] = "fastcopy"

  log_setup(log.DEBUG if args.debug else log.INFO)

  log.debug("command-line overrides: %r", overrides)

  instaclone.run_command(instaclone.Command[args.command], override_path=args.config, overrides=overrides,
                         force=args.force, items=args.items)


if __name__ == '__main__':
  main()
