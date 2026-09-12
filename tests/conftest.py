import os
import sys

# allow running tests without installing the package
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
CATALOG = os.path.join(ROOT, "catalog")
