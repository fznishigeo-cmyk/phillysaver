"""Task 298: browser search interception without launching browsers or networking."""
from tests.test_search_amazon import run_node


def test_robot_search_harness():
    result = run_node("robot_search_logic.cjs")
    assert result.returncode == 0, result.stdout + result.stderr
