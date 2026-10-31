"""公网隐私保护版入口：由部署平台执行，本地启动仍使用 app.py。"""

import os
import runpy
from pathlib import Path

os.environ["GEO_PUBLIC_DEMO"] = "1"
os.environ["GEO_ALLOW_ENV_EDIT"] = "0"

runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
