# -*- coding: utf-8 -*-
"""pytest 路径引导: 让测试可以直接 import app/ 与 scripts/。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("app", "scripts", os.path.join("..", "scripts")):
    p = os.path.join(ROOT, sub)
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)
