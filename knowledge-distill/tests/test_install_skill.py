import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import install_skill
from test_validate_output import PACK_ENTRY, VALID_SKILL


class InstallSkillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skill installer ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source skills" / "sample-skill"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_bytes(VALID_SKILL.encode("utf-8"))
        self.target_parent = self.root / "installed skills"
        self.target_parent.mkdir()
        self.target = self.target_parent / "sample-skill"
        self.backup_root = self.root / "backups"

    def backups(self):
        if not self.backup_root.exists():
            return []
        return sorted(self.backup_root.glob("sample-skill.bak-*"))

    def run_install(self, **kwargs):
        kwargs.setdefault("backup_root", self.backup_root)
        output = io.StringIO()
        with redirect_stdout(output):
            result = install_skill.install_skill(self.source, self.target, **kwargs)
        return result, output.getvalue()

    def make_pack_source(self):
        (self.source / "SKILL.md").write_text(PACK_ENTRY, encoding="utf-8")
        references = self.source / "references"
        references.mkdir()
        (references / "cap.md").write_text(VALID_SKILL, encoding="utf-8")

    def test_dry_run_does_not_create_target(self):
        result, output = self.run_install(dry_run=True)
        self.assertEqual(result, 0)
        self.assertFalse(self.target.exists())
        self.assertIn("DRY-RUN 操作: 新建安装", output)

    def test_install_copies_skill_to_path_with_spaces(self):
        result, output = self.run_install()
        self.assertEqual(result, 0)
        self.assertTrue((self.target / "SKILL.md").is_file())
        self.assertIn("INSTALLED", output)
        self.assertEqual(list(self.target_parent.glob(".sample-skill.install-*")), [])

    def test_pack_dry_run_uses_pack_contract(self):
        self.make_pack_source()
        result, output = self.run_install(dry_run=True, pack=True)
        self.assertEqual(result, 0)
        self.assertIn("DRY-RUN 操作: 新建安装", output)
        self.assertFalse(self.target.exists())

    def test_pack_install_copies_entry_and_members(self):
        self.make_pack_source()
        result, output = self.run_install(pack=True)
        self.assertEqual(result, 0)
        self.assertIn("INSTALLED", output)
        self.assertTrue((self.target / "SKILL.md").is_file())
        self.assertTrue((self.target / "references" / "cap.md").is_file())
        self.assertIn("PASS", install_skill.validate_skill(self.target, pack=True))

    def test_pack_flag_is_forwarded_by_cli(self):
        with mock.patch.object(
            sys,
            "argv",
            ["install_skill.py", str(self.source), str(self.target), "--pack"],
        ), mock.patch.object(install_skill, "install_skill", return_value=0) as installer:
            self.assertEqual(install_skill.main(), 0)
        self.assertTrue(installer.call_args.kwargs["pack"])

    def test_existing_target_is_not_overwritten_by_default(self):
        self.target.mkdir()
        marker = self.target / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(install_skill.ConflictError):
            self.run_install()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.target / "SKILL.md").exists())

    def test_replace_backs_up_existing_target(self):
        self.target.mkdir()
        marker = self.target / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        result, output = self.run_install(replace=True)
        self.assertEqual(result, 0)
        self.assertTrue((self.target / "SKILL.md").is_file())
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "old.txt").read_text(encoding="utf-8"), "keep")
        self.assertIn("BACKUP", output)
        # 备份必须落在扫描目录之外，否则会被当成第二个技能加载
        self.assertEqual(list(self.target_parent.glob("sample-skill.bak-*")), [])

    def test_invalid_source_does_not_touch_existing_target(self):
        (self.source / "SKILL.md").write_text("# 缺少结构\n", encoding="utf-8")
        self.target.mkdir()
        marker = self.target / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(install_skill.InstallError):
            self.run_install(replace=True)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self.backups(), [])

    def test_target_name_mismatch_leaves_no_partial_install(self):
        self.target = self.target_parent / "different-name"
        with self.assertRaises(install_skill.InstallError):
            self.run_install()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target_parent.glob(".different-name.install-*")), [])

    def test_failure_after_backup_restores_previous_target(self):
        self.target.mkdir()
        marker = self.target / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        real_replace = os.replace

        def fail_install_move(source, destination):
            if Path(destination) == self.target and Path(source).name == self.target.name:
                raise OSError("simulated install failure")
            return real_replace(source, destination)

        with mock.patch.object(install_skill.os, "replace", side_effect=fail_install_move):
            with self.assertRaises(OSError):
                self.run_install(replace=True)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.target / "SKILL.md").exists())
        self.assertEqual(self.backups(), [])

    def test_interrupt_after_backup_restores_previous_target(self):
        self.target.mkdir()
        marker = self.target / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        real_replace = os.replace

        def interrupt_install(source, destination):
            if Path(destination) == self.target and Path(source).name == self.target.name:
                raise KeyboardInterrupt
            return real_replace(source, destination)

        with mock.patch.object(install_skill.os, "replace", side_effect=interrupt_install):
            with self.assertRaises(KeyboardInterrupt):
                self.run_install(replace=True)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.target / "SKILL.md").exists())
        self.assertEqual(self.backups(), [])

    def test_interrupt_during_backup_keeps_original_target(self):
        self.target.mkdir()
        marker = self.target / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        original_copy = install_skill.copy_tree

        def interrupt_backup(source, destination):
            if Path(destination).name.startswith("sample-skill.bak-"):
                raise KeyboardInterrupt
            return original_copy(source, destination)

        with mock.patch.object(install_skill, "copy_tree", side_effect=interrupt_backup):
            with self.assertRaises(KeyboardInterrupt):
                self.run_install(replace=True)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self.backups(), [])

    def test_target_created_during_staging_is_not_overwritten(self):
        original_validate = install_skill.validate_skill
        calls = 0

        def racing_validate(skill_dir, pack=False):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.target.mkdir()
                (self.target / "racer.txt").write_text("keep", encoding="utf-8")
            return original_validate(skill_dir, pack=pack)

        with mock.patch.object(install_skill, "validate_skill", side_effect=racing_validate):
            with self.assertRaises(install_skill.ConflictError):
                self.run_install()
        self.assertEqual((self.target / "racer.txt").read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.target / "SKILL.md").exists())
        self.assertEqual(self.backups(), [])

    def test_replace_uses_copy_verified_backup_without_renaming_target(self):
        self.target.mkdir()
        (self.target / "old.txt").write_text("keep", encoding="utf-8")
        real_copy = install_skill.copy_tree
        copy_calls = []

        def track_copy(source, destination):
            copy_calls.append((Path(source), Path(destination)))
            return real_copy(source, destination)

        with mock.patch.object(
            install_skill, "copy_tree", side_effect=track_copy
        ), mock.patch.object(install_skill.os, "replace", wraps=os.replace) as replace:
            result, output = self.run_install(replace=True)
        backups = self.backups()
        self.assertEqual(result, 0, output)
        self.assertEqual(len(backups), 1)
        self.assertIn((self.target, backups[0]), copy_calls)
        self.assertEqual((backups[0] / "old.txt").read_text(encoding="utf-8"), "keep")
        self.assertFalse(any(Path(call.args[0]) == self.target for call in replace.call_args_list))
        self.assertIn("BACKUP", output)

    def test_backup_root_inside_target_is_rejected(self):
        self.target.mkdir()
        marker = self.target / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(install_skill.InstallError):
            self.run_install(replace=True, backup_root=self.target / "backups")
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_lock_key_normalizes_case(self):
        with mock.patch.object(
            install_skill.os.path, "normcase", side_effect=lambda value: value.lower()
        ):
            upper = install_skill.installation_lock_key(Path("C:/Temp/Sample-Skill"))
            lower = install_skill.installation_lock_key(Path("c:/temp/sample-skill"))
        self.assertEqual(upper, lower)

    def test_pack_install_rejects_child_privacy(self):
        self.make_pack_source()
        (self.source / "references" / "cap.md").write_text(
            VALID_SKILL.replace("## 未知项\n无。", "## 未知项\n联系人 13800138000"),
            encoding="utf-8",
        )
        with self.assertRaises(install_skill.InstallError):
            self.run_install(pack=True)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.backups(), [])

    def test_pack_replace_backs_up_entry_and_members(self):
        self.make_pack_source()
        self.target.mkdir()
        (self.target / "old.txt").write_text("keep", encoding="utf-8")
        result, output = self.run_install(replace=True, pack=True)
        self.assertEqual(result, 0, output)
        self.assertTrue((self.target / "references" / "cap.md").is_file())
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "old.txt").read_text(encoding="utf-8"), "keep")
        self.assertIn("BACKUP", output)

    def test_pack_replace_failure_restores_previous_target(self):
        self.make_pack_source()
        self.target.mkdir()
        marker = self.target / "old.txt"
        marker.write_text("keep", encoding="utf-8")
        real_replace = os.replace

        def fail_install_move(source, destination):
            if Path(destination) == self.target and Path(source).name == self.target.name:
                raise OSError("simulated pack install failure")
            return real_replace(source, destination)

        with mock.patch.object(install_skill.os, "replace", side_effect=fail_install_move):
            with self.assertRaises(OSError):
                self.run_install(replace=True, pack=True)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.target / "SKILL.md").exists())
        self.assertEqual(self.backups(), [])

    def test_concurrent_cli_installs_do_not_overwrite_each_other(self):
        script = Path(install_skill.__file__).resolve()
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(script),
                    str(self.source),
                    str(self.target),
                    "--backup-root",
                    str(self.backup_root),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for _ in range(6)
        ]
        outputs = [process.communicate(timeout=60)[0] for process in processes]
        returncodes = [process.returncode for process in processes]
        self.assertEqual(returncodes.count(0), 1, outputs)
        self.assertEqual(returncodes.count(2), len(processes) - 1, outputs)
        self.assertTrue((self.target / "SKILL.md").is_file())
        self.assertEqual(list(self.target_parent.glob(".sample-skill.install-*")), [])

    def test_nested_target_is_rejected(self):
        self.target = self.source / "nested" / "sample-skill"
        with self.assertRaises(install_skill.InstallError):
            self.run_install()
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
