#!/usr/bin/env python3
"""Run each mod test in a separate LuaJIT process (Lupa's LuaJIT on Windows)."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'mod' / 'tests'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tests', nargs='*')
    parser.add_argument('--one', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.one:
        from lupa.luajit21 import LuaRuntime
        lua = LuaRuntime()
        lua.globals().arg = lua.table_from({0: args.one})
        lua.execute('dofile(arg[0])')
        return 0
    luajit = shutil.which('luajit')
    if not luajit:
        try:
            import lupa.luajit21  # noqa: F401
        except ImportError:
            parser.error('install luajit, or python -m pip install lupa')
    files = [TESTS / n for n in args.tests] if args.tests else sorted(TESTS.glob('test_*.lua'))
    failed = []
    for path in files:
        command = [luajit, path.as_posix()] if luajit else [sys.executable, __file__, '--one', path.as_posix()]
        try:
            result = subprocess.run(command, cwd=TESTS, timeout=120)
            if result.returncode:
                failed.append(path.name)
        except subprocess.TimeoutExpired:
            failed.append(path.name)
    print(f'{len(files) - len(failed)}/{len(files)} Lua modules passed', flush=True)
    if failed:
        print('Failed: ' + ', '.join(failed))
    return int(bool(failed))


if __name__ == '__main__':
    sys.exit(main())
