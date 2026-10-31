"""公网 BYOK 入口：用户自带 API Key，仅用于当前会话。"""

import os
import runpy
from pathlib import Path

os.environ["GEO_PUBLIC_BYOK"] = "1"
os.environ["GEO_ALLOW_ENV_EDIT"] = "0"

runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
