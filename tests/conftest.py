"""pytest 公共配置：确保项目根在 sys.path（src 为包根）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
