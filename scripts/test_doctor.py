"""Inventory acceptance tests with synthetic metadata; no VM or server proof."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import doctor


class DoctorTests(unittest.TestCase):
    def linux_report(self, metadata=None, architecture="x86_64"):
        files = {
            "/etc/os-release": 'ID=ubuntu\nVERSION_ID="24.04"\n',
            "/proc/meminfo": "MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n",
            "/proc/cpuinfo": "model name : Synthetic AMD CPU\nflags : fpu svm\n",
        }
        if metadata is not None:
            files.update(metadata)
        with patch.object(doctor.platform, "system", return_value="Linux"), \
             patch.object(doctor.platform, "machine", return_value=architecture), \
             patch.object(doctor, "read_system_file", side_effect=files.get) as read, \
             patch.object(doctor, "kvm_inventory", return_value={
                 "present": True, "read_write_access_for_current_user": False}):
            report = doctor.collect_report()
        self.assertEqual({call.args[0] for call in read.call_args_list}, set(files))
        return report

    def test_memory_conversion(self):
        self.assertEqual(doctor.parse_memory_gib(str(32 * doctor.GIB)), 32.0)
        for bad in (None, "", "unknown", "0", "-1"):
            self.assertIsNone(doctor.parse_memory_gib(bad))

    def test_ubuntu_inventory_never_claims_kvm_or_dedication(self):
        report = self.linux_report()
        self.assertTrue(report["host_is_target_platform"])
        self.assertEqual(report["os_version"], "24.04")
        self.assertEqual(report["physical_memory_gib"], 32.0)
        self.assertEqual(report["available_memory_gib"], 16.0)
        self.assertTrue(report["cpu"]["svm_flag_on_all_reported_cpus"])
        self.assertIn("Actual KVM guest boot", report["not_tested"])
        self.assertIn("Server identity, bare metal and host dedication", report["not_tested"])
        self.assertFalse(report["system_changes_performed"])

    def test_mac_does_not_read_linux_files_or_check_kvm(self):
        with patch.object(doctor.platform, "system", return_value="Darwin"), \
             patch.object(doctor.platform, "machine", return_value="arm64"), \
             patch.object(doctor, "read_system_file") as read, \
             patch.object(doctor, "kvm_inventory") as kvm:
            report = doctor.collect_report()
        read.assert_not_called()
        kvm.assert_not_called()
        self.assertFalse(report["host_is_target_platform"])
        self.assertIsNone(report["physical_memory_gib"])

    def test_wrong_distribution_or_architecture_is_not_target(self):
        self.assertFalse(self.linux_report({"/etc/os-release": "ID=debian\n"})["host_is_target_platform"])
        self.assertFalse(self.linux_report(architecture="aarch64")["host_is_target_platform"])

    def test_missing_system_metadata_is_unknown(self):
        report = self.linux_report({path: None for path in (
            "/etc/os-release", "/proc/meminfo", "/proc/cpuinfo")})
        self.assertFalse(report["host_is_target_platform"])
        self.assertIsNone(report["physical_memory_gib"])
        self.assertIsNone(report["cpu"]["svm_flag_on_all_reported_cpus"])
        self.assertTrue(report["warnings"])

    def test_malformed_memory_and_zero_available(self):
        self.assertEqual(doctor.parse_meminfo("MemTotal: bad kB\nMemAvailable: -2 kB"),
                         {"physical": None, "available": None})
        self.assertEqual(doctor.parse_meminfo("MemAvailable: 0 kB")["available"], 0.0)
        self.assertIsNone(doctor.parse_meminfo("MemTotal: 32000 MB")["physical"])
        self.assertIsNone(doctor.parse_meminfo("MemTotal: 0 kB")["physical"])

    def test_svm_absent_or_inconsistent_is_not_confirmed(self):
        self.assertFalse(doctor.parse_cpuinfo("flags: fpu\n")["svm_flag_on_all_reported_cpus"])
        self.assertFalse(doctor.parse_cpuinfo("flags: svm\nflags: fpu\n")["svm_flag_on_all_reported_cpus"])
        self.assertIsNone(doctor.parse_cpuinfo(None)["svm_flag_on_all_reported_cpus"])

    def test_kvm_missing_denied_and_present_are_distinct(self):
        with patch.object(doctor.Path, "stat", side_effect=FileNotFoundError):
            self.assertFalse(doctor.kvm_inventory()["present"])
        with patch.object(doctor.Path, "stat", side_effect=PermissionError):
            self.assertIsNone(doctor.kvm_inventory()["present"])
        with patch.object(doctor.Path, "stat"), patch.object(doctor.os, "access", return_value=False):
            report = doctor.kvm_inventory()
            self.assertTrue(report["present"])
            self.assertFalse(report["read_write_access_for_current_user"])

    def test_unreadable_metadata_returns_unknown(self):
        with patch.object(doctor.Path, "read_text", side_effect=PermissionError):
            self.assertIsNone(doctor.read_system_file("/proc/meminfo"))

    def test_disk_failure_and_low_disk_are_reported(self):
        with patch.object(doctor.shutil, "disk_usage", side_effect=OSError):
            report = self.linux_report()
            self.assertIsNone(report["free_disk_gib_on_current_volume"])
        with patch.object(doctor.shutil, "disk_usage") as usage:
            usage.return_value.free = 10 * doctor.GIB
            report = self.linux_report()
            self.assertEqual(report["free_disk_gib_on_current_volume"], 10)
            self.assertTrue(any("diskbudget" in x for x in report["warnings"]))

    def test_report_excludes_private_metadata_and_tool_paths(self):
        report = self.linux_report({"/etc/os-release": 'ID=ubuntu\nPRIVATE=secret\n',
                                    "/proc/cpuinfo": "model name: Synthetic CPU\nSerial: private\n"})
        for field in ("environment", "username", "hostname", "serial", "paths"):
            self.assertNotIn(field, report)
        self.assertNotIn("secret", str(report))
        self.assertNotIn("private", str(report))
        self.assertTrue(all(isinstance(v, bool) for v in report["tools_on_path"].values()))


if __name__ == "__main__":
    unittest.main()
