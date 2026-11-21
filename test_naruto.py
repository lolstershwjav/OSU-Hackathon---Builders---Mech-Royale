#!/usr/bin/env python3
"""
Test script for Naruto validation.

Run with: python3 test_naruto.py
Or in Docker: docker run --rm naruto-improved python3 test_naruto.py
"""

import sys
import os
import json

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

def test_imports():
    """Test that all modules import correctly."""
    print("Testing imports...")

    try:
        from bob_mix_improved import Naruto
        print("  ✓ bob_mix_improved.Naruto")
    except Exception as e:
        print(f"  ✗ bob_mix_improved.Naruto: {e}")
        return False

    try:
        from tsunade import Tsunade
        print("  ✓ tsunade.Tsunade")
    except Exception as e:
        print(f"  ✗ tsunade.Tsunade: {e}")
        return False

    try:
        import navigation
        print("  ✓ navigation")
    except Exception as e:
        print(f"  ✗ navigation: {e}")
        return False

    try:
        import policy
        print("  ✓ policy")
    except Exception as e:
        print(f"  ✗ policy: {e}")
        return False

    try:
        import mechfight
        print("  ✓ mechfight")
    except Exception as e:
        print(f"  ✗ mechfight: {e}")
        return False

    return True


def test_naruto_instantiation():
    """Test Naruto can be instantiated."""
    print("\nTesting Naruto instantiation...")
    try:
        from bob_mix_improved import Naruto
        naruto = Naruto()
        print("  ✓ Naruto() created")
        print(f"    Giddy process: {naruto.giddy_process is not None}")
        print(f"    Tsunade ready: {naruto.tsunade_initialized}")
        return True
    except Exception as e:
        print(f"  ✗ Naruto instantiation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_tsunade_instantiation():
    """Test Tsunade can be instantiated."""
    print("\nTesting Tsunade instantiation...")
    try:
        from tsunade import Tsunade
        tsunade = Tsunade()
        print("  ✓ Tsunade() created")
        print(f"    Mode: {tsunade.mode}")
        print(f"    Upgrade priority: {tsunade.upgrade_priority[:3]}...")
        return True
    except Exception as e:
        print(f"  ✗ Tsunade instantiation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_mock_observation():
    """Test with a mock observation."""
    print("\nTesting with mock observation...")

    mock_obs = {
        "tick": 100,
        "mech_id": 0,
        "mechs": [
            {
                "id": 0,
                "x": 500, "y": 500,
                "vx": 0, "vy": 0,
                "health": 100, "max_health": 100,
                "heading": 0, "gun_heading": 0,
                "weapon": "gun", "secondary": "none",
                "charge": 0
            },
            {
                "id": 1,
                "x": 600, "y": 500,
                "vx": 0, "vy": 0,
                "health": 100, "max_health": 100,
                "heading": 3.14, "gun_heading": 3.14,
                "weapon": "gun", "secondary": "none",
                "charge": 0
            }
        ],
        "zone": {
            "x": 500, "y": 500,
            "radius": 800,
            "next_x": 500, "next_y": 500,
            "next_radius": 800,
            "time_remaining": 300
        },
        "events": [],
        "loot": [],
        "upgrades": [],
        "shop": []
    }

    try:
        from bob_mix_improved import Naruto
        naruto = Naruto()
        action = naruto.act(mock_obs)
        print("  ✓ Naruto.act() returned action")
        print(f"    Action keys: {list(action.keys())}")
        print(f"    Move: {action.get('move')}")
        print(f"    Fire: {action.get('fire')}")
        print(f"    Debug: {naruto.get_debug_info()}")
    except Exception as e:
        print(f"  ✗ Naruto mock test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    try:
        from tsunade import Tsunade
        tsunade = Tsunade()
        action = tsunade.act(mock_obs)
        print("  ✓ Tsunade.act() returned action")
        print(f"    Action keys: {list(action.keys())}")
        print(f"    Move: {action.get('move')}")
        print(f"    Fire: {action.get('fire')}")
        print(f"    Mode: {tsunade.mode}")
    except Exception as e:
        print(f"  ✗ Tsunade mock test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


def test_handoff_logic():
    """Test FFA handoff detection logic."""
    print("\nTesting handoff logic...")

    from bob_mix_improved import Naruto, DeathEvent

    naruto = Naruto()

    # Simulate ticks with deaths
    base_obs = {
        "tick": 100,
        "mech_id": 0,
        "mechs": [
            {"id": 0, "x": 500, "y": 500, "vx": 0, "vy": 0, "health": 100, "max_health": 100,
             "heading": 0, "gun_heading": 0, "weapon": "gun", "secondary": "none", "charge": 0}
        ],
        "zone": {"x": 500, "y": 500, "radius": 800, "next_x": 500, "next_y": 500, "next_radius": 800, "time_remaining": 300},
        "events": [],
        "loot": [], "upgrades": [], "shop": []
    }

    # Add 2 enemies that will "die"
    for i in range(1, 3):
        base_obs["mechs"].append({
            "id": i, "x": 600, "y": 500, "vx": 0, "vy": 0, "health": 100, "max_health": 100,
            "heading": 3.14, "gun_heading": 3.14, "weapon": "gun", "secondary": "none", "charge": 0
        })

    # Tick 100: 3 alive
    naruto.act(base_obs)
    print(f"  Tick 100: alive=3, deaths=0, switched={naruto.switched}")

    # Tick 110: 1 dies (unexplained)
    base_obs["tick"] = 110
    base_obs["mechs"][1]["health"] = 0
    base_obs["events"] = [{"type": "sound", "weapon": "gun", "x": 600, "y": 500}]
    naruto.act(base_obs)
    print(f"  Tick 110: alive=2, deaths=1, switched={naruto.switched}")

    # Tick 120: another dies
    base_obs["tick"] = 120
    base_obs["mechs"][2]["health"] = 0
    base_obs["events"] = [{"type": "sound", "weapon": "gun", "x": 610, "y": 510}]
    naruto.act(base_obs)
    print(f"  Tick 120: alive=1, deaths=2, switched={naruto.switched}")

    # Tick 130: third dies -> should switch (3 deaths, clustered, sounds)
    base_obs["tick"] = 130
    # Add a 4th mech that dies
    base_obs["mechs"].append({
        "id": 3, "x": 605, "y": 505, "vx": 0, "vy": 0, "health": 0, "max_health": 100,
        "heading": 3.14, "gun_heading": 3.14, "weapon": "gun", "secondary": "none", "charge": 0
    })
    base_obs["events"] = [{"type": "sound", "weapon": "laser", "x": 605, "y": 505}]
    naruto.act(base_obs)
    print(f"  Tick 130: alive=1, deaths=3, switched={naruto.switched}, reason={naruto.switch_reason}")

    if naruto.switched:
        print("  ✓ Handoff triggered correctly")
        return True
    else:
        print("  ✗ Handoff did not trigger (may need more deaths)")
        return False


def main():
    print("=" * 50)
    print("Naruto Validation Tests")
    print("=" * 50)

    all_passed = True

    all_passed &= test_imports()
    all_passed &= test_naruto_instantiation()
    all_passed &= test_tsunade_instantiation()
    all_passed &= test_mock_observation()
    all_passed &= test_handoff_logic()

    print("\n" + "=" * 50)
    if all_passed:
        print("ALL TESTS PASSED ✓")
    else:
        print("SOME TESTS FAILED ✗")
    print("=" * 50)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())