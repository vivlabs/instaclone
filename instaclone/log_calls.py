"""
A simple utility to log calls to functions, for debugging.
"""

__author__ = 'jlevy'

import functools
import logging
from logging import log


def log_calls_with(severity):
  """Create a decorator to log calls and return values of any function, for debugging."""

  def decorator(fn):
    @functools.wraps(fn)
    def wrap(*params, **kwargs):
      call_str = "%s(%s)" % (
        fn.__name__, ", ".join([repr(p) for p in params] + ["%s=%s" % (k, repr(v)) for (k, v) in kwargs.items()]))
      # TODO: Extract line number from caller and use that in logging.
      log(severity, ">> %s", call_str)
      ret = fn(*params, **kwargs)
      # TODO: Add a way to make return short or omitted.
      log(severity, "<< %s: %s", call_str, repr(ret))
      return ret

    return wrap

  return decorator

# Convenience decorators for logging.
log_calls_info = log_calls_with(logging.INFO)
log_calls = log_calls_with(logging.DEBUG)
