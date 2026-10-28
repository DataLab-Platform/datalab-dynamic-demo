# Copyright (c) DataLab Platform Developers, BSD 3-Clause license, see LICENSE file.

"""Launch DataLab with the dynamic mode demo plugin.

Run it from the root of the DataLab repository, so that its development
environment is used (see the README of the project)::

    python scripts/run_with_env.py python <project>/scripts/launch_demo.py [--lang fr]

The project folder is added to the ``DATALAB_PLUGINS`` environment variable for
this session only: the user configuration is not modified.
"""

from __future__ import annotations

import argparse
import os
import os.path as osp

PROJECT_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))


def main() -> None:
    """Add the project to the plugin folders, then run DataLab."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--lang",
        help="language of DataLab, e.g. 'fr' (default: the one of the environment)",
    )
    args = parser.parse_args()
    if args.lang:
        # Takes precedence over LANG, which DataLab's .env file sets
        os.environ["LANGUAGE"] = args.lang
    paths = os.environ.get("DATALAB_PLUGINS", "").split(os.pathsep)
    paths = [path for path in paths if path]
    if PROJECT_ROOT not in paths:
        paths.insert(0, PROJECT_ROOT)
    # DataLab reads this variable when its configuration is imported
    os.environ["DATALAB_PLUGINS"] = os.pathsep.join(paths)
    from datalab.app import run  # pylint: disable=import-outside-toplevel

    run()


if __name__ == "__main__":
    main()
