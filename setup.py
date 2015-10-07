#!/usr/bin/env

from setuptools import setup, find_packages
from instaclone import main

setup(
  name="instaclone",
  version=main.VERSION,
  packages=find_packages(),
  author="Joshua Levy",
  license="Apache 2",
  url="https://github.com/vivlabs/instaclone",
  install_requires=["strif>=0.1.2", "enum34>=1.0.4", "PyYAML>=3.11", "subprocess32>=3.2.6", "functools32>=3.2.3"],
  description=main.DESCRIPTION,
  long_description=main.LONG_DESCRIPTION,
  classifiers=[
    'Development Status :: 4 - Beta',
    'Environment :: Console',
    'Intended Audience :: End Users/Desktop',
    'Intended Audience :: System Administrators',
    'Intended Audience :: Developers',
    'License :: OSI Approved :: Apache Software License',
    'Operating System :: MacOS :: MacOS X',
    'Operating System :: POSIX',
    'Operating System :: Unix',
    'Programming Language :: Python :: 2.7',
    'Topic :: Utilities',
    'Topic :: Software Development'
  ],
  entry_points={
    "console_scripts": [
      "instaclone = instaclone.main:main",
    ],
  },
)
