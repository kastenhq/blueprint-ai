#!/usr/bin/env python3
"""Build blueprint concept decks.

    python3 ppt-tools/build.py                    # every blueprint that has a deck
    python3 ppt-tools/build.py cnpg-example       # just one
    python3 ppt-tools/build.py --check            # build all, exit non-zero on warnings

A deck is any `<blueprint-dir>/ppt-tools/deck.py`. Each is executed in a fresh
subprocess so one broken deck cannot take the others down, and so module-level
state in the toolkit never leaks between decks.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def deck_scripts(names=None):
    found = sorted(ROOT.glob('*-example/ppt-tools/deck.py'))
    if names:
        wanted = {n.rstrip('/') for n in names}
        found = [p for p in found if p.parents[1].name in wanted]
        missing = wanted - {p.parents[1].name for p in found}
        for m in sorted(missing):
            print(f"!! no ppt-tools/deck.py in {m}", file=sys.stderr)
    return found


def main(argv):
    check = '--check' in argv
    names = [a for a in argv if not a.startswith('-')]
    scripts = deck_scripts(names or None)
    if not scripts:
        print("no decks found", file=sys.stderr)
        return 1
    failed, warned = [], []
    for script in scripts:
        blueprint = script.parents[1].name
        res = subprocess.run([sys.executable, str(script)], capture_output=True,
                             text=True, cwd=str(ROOT))
        out = (res.stdout + res.stderr).strip()
        if res.returncode != 0:
            failed.append(blueprint)
            print(f"FAILED  {blueprint}\n{out}\n")
        else:
            print(out)
            if ' ! ' in out or 'warning' in out:
                warned.append(blueprint)
    print(f"\n{len(scripts) - len(failed)}/{len(scripts)} decks built"
          + (f", {len(warned)} with warnings" if warned else ""))
    if failed:
        print(f"failed: {', '.join(failed)}")
        return 1
    return 1 if (check and warned) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
