"""Exercise the flow when Prefect starts outside the backend directory."""
import subprocess
import sys
from pathlib import Path


def test_flow_exposes_tasks_package_when_backend_is_not_on_python_path():
    root = Path(__file__).resolve().parents[2]
    script = """
import runpy
import sys
from unittest.mock import Mock

backend = str(root / 'backend')
sys.path.insert(0, backend)
namespace = runpy.run_path(str(root / 'backend/tasks/generate_daily_picks.py'))
sys.path.remove(backend)
sys.modules.pop('tasks', None)
flow_fn = namespace['generate_daily_picks_flow'].fn
def generate(**kwargs):
    from tasks import train_meta_label_models
    assert callable(train_meta_label_models.build_features_from_ohlc_panel)
    return Mock(model_dump=lambda **kwargs: {})
flow_fn.__globals__['generate_daily_picks'] = generate
flow_fn()
"""
    result = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path\nroot = Path(" + repr(str(root)) + ")\n" + script],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
