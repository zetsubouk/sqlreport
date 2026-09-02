import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "src"))
import sqlreport.analytics as _src
_sys.modules[__name__] = _src
