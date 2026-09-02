#!/usr/bin/env python3
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "src"))
import sqlreport.server as _src
_sys.modules[__name__] = _src
if __name__ == "__main__":
    _src.main()
