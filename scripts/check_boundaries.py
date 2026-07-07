#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Furi Labs
#
# Authors:
# Bardia Moshiri <bardia@furilabs.com>
# Alaraajavamma <aki@urheiluaki.fi>

"""Fail the build when an import crosses the process boundary.

client/ and daemon/ are separate processes and must never import each
other; shared/ serves both and may import neither; cli/ speaks D-Bus
only and gets the same rule. Runs from the repository root.
"""

import ast
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "src", "telephony")
FORBIDDEN = {
    "client": ("daemon",),
    "daemon": ("client",),
    "shared": ("client", "daemon"),
    "cli": ("client", "daemon"),
}


def main():
    violations = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        if "__pycache__" in dirpath:
            continue
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, os.path.dirname(ROOT))
            parts = rel.replace(os.sep, ".")[:-3].split(".")
            side = parts[1] if len(parts) > 1 else ""
            banned = FORBIDDEN.get(side, ())
            if not banned:
                continue
            tree = ast.parse(open(path).read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    target = node.module.split(".")
                elif isinstance(node, ast.Import):
                    target = node.names[0].name.split(".")
                else:
                    continue
                if len(target) > 1 and target[0] == "telephony" and target[1] in banned:
                    violations.append(f"{rel}:{node.lineno}: {side} imports {".".join(target)}")

    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print(f"{len(violations)} boundary violation(s)", file=sys.stderr)
        return 1
    print("boundaries clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
