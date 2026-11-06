"""Synthetic checks of inventory privacy, failure handling and command scope."""
import subprocess
import unittest
from unittest.mock import patch

import m0_inventory as inventory


class InventoryTests(unittest.TestCase):
    def test_simulation_redacts_repository_and_detects_upgrade_and_removal(self):
        result = inventory.parse_simulation(0, (
            "Inst qemu-utils [old] (1:10.2-1 private-repository.example [amd64])\n"
            "Remv old-package [1]\n"
            "1 upgraded, 0 newly installed, 1 to remove and 4 not upgraded.\n"
        ))
        self.assertTrue(result["parse_complete"])
        self.assertEqual(result["counts"]["upgraded"], 1)
        self.assertEqual(result["counts"]["removed"], 1)
        self.assertEqual(result["counts"]["not_upgraded"], 4)
        self.assertNotIn("held", result["counts"])
        self.assertNotIn("private-repository", str(result))

    def test_failed_partial_or_unrecognized_simulation_is_not_complete(self):
        for code, output in (
            (100, ""), (None, ""), (0, "translated or changed format"),
            (0, "0 upgraded, 2 newly installed, 0 to remove and 0 not upgraded."),
            (0, "Inst bad-format\n0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded."),
        ):
            with self.subTest(code=code, output=output):
                self.assertFalse(inventory.parse_simulation(code, output)["parse_complete"])

    def test_fixed_read_only_commands_and_no_raw_output_leak(self):
        calls = []
        def fake(argv):
            calls.append(argv)
            return 0, "SECRET private-user /private/path token=SECRET"
        with patch.object(inventory.doctor, "collect_report", return_value={"os": "Linux"}), \
             patch.object(inventory, "run_local", side_effect=fake):
            report = inventory.collect()
        self.assertNotIn("SECRET", str(report))
        self.assertEqual(report["virtualization_detection"]["kind"], "UNKNOWN")
        expected = [["systemd-detect-virt"]]
        for name in inventory.INSPECT_PACKAGES:
            expected.extend([
                ["dpkg-query", "-W", "-f=${db:Status-Status} ${Version}", name],
                ["apt-cache", "policy", name],
            ])
        expected.append(["apt-get", "--simulate", "--no-install-recommends", "install", *inventory.INSTALL_PACKAGES])
        self.assertEqual(calls, expected)

    def test_non_linux_never_runs_package_commands(self):
        with patch.object(inventory.doctor, "collect_report", return_value={"os": "Darwin"}), \
             patch.object(inventory, "run_local") as run:
            self.assertEqual(inventory.collect()["extended_inventory"]["status"], "NOT RUN")
        run.assert_not_called()

    def test_missing_command_and_timeout_return_no_exception_details(self):
        for error in (OSError("SECRET"), subprocess.TimeoutExpired("SECRET", 30)):
            with patch.object(inventory.subprocess, "run", side_effect=error):
                self.assertEqual(inventory.run_local(["systemd-detect-virt"]), (None, ""))


if __name__ == "__main__":
    unittest.main()
