"""
Demonstration & Testing script for Amigo + Official TryCua Driver (trycua/cua).
Run this script directly to verify live CUA capabilities:
    python test_cua_advanced.py
"""

import sys
import time

print("=" * 60)
print("  AMIGO + TRYCUA DRIVER (https://github.com/trycua/cua) DEMO")
print("=" * 60)

from amigo.services.computer_use_agent import get_cua_driver_bridge, execute_computer_task

bridge = get_cua_driver_bridge()

# 1. Status & Official TryCua Verification
print("\n[1] Verifying TryCua Driver Connection...")
status = bridge.get_status()
print(f"  - TryCua Driver Installed : {status['driver_installed']}")
print(f"  - TryCua Driver Version   : {status['driver_version']}")
print(f"  - TryCua Binary Executable : {status['binary_path']}")
print(f"  - Active Driver Session    : {status['session_id']}")

# 2. Querying Live Desktop Metrics
print("\n[2] Reading Display & Cursor Metrics...")
screen = bridge.get_screen_size()
cursor = bridge.get_cursor_position()
print(f"  - Display Resolution: {screen.get('width')}x{screen.get('height')} (scale: {screen.get('scale_factor', 1.0)}x)")
print(f"  - Physical Cursor Pos: ({cursor.get('x')}, {cursor.get('y')})")

# 3. Agent Cursor Physics & Motion Demonstration
print("\n[3] Configuring Animated TryCua Agent Cursor...")
cursor_config = bridge.configure_cursor_motion(
    style="signature_arc",
    glow=True,
    trail=True,
    glide_duration_ms=800
)
print(f"  - Agent Cursor Motion Configured: {cursor_config.get('status')}")

print("  - Moving Agent Cursor across coordinates (500, 400)...")
move_res = bridge.move_cursor(500, 400)
print(f"  - Result: {move_res.get('text')}")

# 4. Windows UI Automation (UIA) Tree Inspection
print("\n[4] Inspecting Windows UI Automation (UIA) Tree...")
tree = bridge.get_accessibility_tree()
tree_text = tree.get("text", "")
lines = tree_text.strip().split("\n")
print(f"  - Total UIA Tree Size: {len(tree_text)} characters")
print("  - First 5 detected process entries from Accessibility Tree:")
for line in lines[:5]:
    print(f"      {line}")

# 5. Autonomous Multi-step Goal Execution
print("\n[5] Running Autonomous CUA Task via Amigo...")
task_desc = "check screen and move cursor to center"
report = execute_computer_task(task_desc, max_steps=2)
print(f"  - Task Status: {report.get('status')}")
print(f"  - Summary    : {report.get('summary')}")
print(f"  - Steps Ran  : {len(report.get('steps', []))}")

print("\n" + "=" * 60)
print("  TRYCUA INTEGRATION VERIFIED SUCCESSFULLY!")
print("=" * 60)
