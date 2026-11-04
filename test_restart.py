"""回归测试：旧实例结束不了时要报错且不启动新进程，不能误报成功。"""
import sys
from unittest import mock

import restart


def run_main(instances):
    with mock.patch.object(restart, "list_instances", side_effect=instances), \
            mock.patch.object(restart, "kill", return_value=False), \
            mock.patch.object(restart, "spawn") as spawn, \
            mock.patch.object(restart.time, "sleep"):
        return restart.main(), spawn.called


def main() -> int:
    cases = [
        ("old instance survives", [[111], [111]], 1, False),
        ("old instance ends", [[111], [], [222]], 0, True),
        ("nothing running", [[], [222]], 0, True),
        ("new instance missing", [[], []], 1, True),
        ("query fails", [RuntimeError("no powershell")], 1, False),
    ]
    failed = 0
    for name, instances, want_rc, want_spawn in cases:
        got = run_main(instances)
        ok = got == (want_rc, want_spawn)
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: rc,spawned={got}")

    print("FAILED" if failed else "PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
