#!/usr/bin/env python3
"""事务式安装 knowledge-distill 产物。

用法:
    python install_skill.py <源目录> <目标目录> [--dry-run] [--replace] [--pack] [--backup-root <目录>]

预期:
    默认拒绝覆盖已有目标；--dry-run 只打印计划；--replace 先复制并校验备份，再切换目录。
    --pack 按路由入口契约校验源目录与暂存目录；异常或中断自动回滚。
    备份使用复制流程，支持备份目录与目标目录跨卷。
    成功输出 INSTALLED，dry-run 输出 DRY-RUN，冲突退出码为 2。
    备份默认写到系统临时目录下的 knowledge-distill-backups/，不放在 skill 扫描目录里。
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class InstallError(Exception):
    pass


class ConflictError(InstallError):
    pass


def validate_skill(skill_dir: Path, pack: bool = False):
    validator = Path(__file__).resolve().with_name("validate_output.py")
    command = [sys.executable, str(validator), str(skill_dir)]
    if pack:
        command.append("--pack")
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise InstallError(f"校验未通过：\n{result.stdout.strip()}")
    return result.stdout.strip()


DEFAULT_BACKUP_ROOT = Path(tempfile.gettempdir()) / "knowledge-distill-backups"


def resolve_backup_root(backup_root=None):
    return Path(backup_root).expanduser().resolve() if backup_root else DEFAULT_BACKUP_ROOT


def backup_path(target: Path, backup_root=None):
    root = resolve_backup_root(backup_root)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    candidate = root / f"{target.name}.bak-{stamp}"
    counter = 1
    while candidate.exists():
        candidate = root / f"{target.name}.bak-{stamp}-{counter}"
        counter += 1
    return candidate


def installation_lock_key(target_dir: Path):
    normalized = os.path.normcase(str(target_dir))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@contextmanager
def installation_lock(target_dir: Path):
    lock_root = Path(tempfile.gettempdir()) / "knowledge-distill-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_file = lock_root / f"{installation_lock_key(target_dir)}.lock"
    with lock_file.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def file_digest(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_signature(root: Path):
    signature = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            signature.append((relative, "link", os.readlink(path)))
        elif path.is_dir():
            signature.append((relative, "dir", ""))
        elif path.is_file():
            signature.append((relative, "file", str(path.stat().st_size), file_digest(path)))
        else:
            signature.append((relative, "other", ""))
    return signature


def copy_tree(source: Path, target: Path):
    shutil.copytree(source, target, symlinks=True)


def create_backup(target: Path, backup: Path):
    backup.parent.mkdir(parents=True, exist_ok=True)
    try:
        copy_tree(target, backup)
        if tree_signature(target) != tree_signature(backup):
            raise InstallError("备份内容与目标目录不一致")
    except BaseException:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        raise


def restore_backup(backup: Path, target: Path):
    if target.exists():
        shutil.rmtree(target)
    copy_tree(backup, target)
    if tree_signature(backup) != tree_signature(target):
        raise InstallError("回滚内容与备份不一致")
    shutil.rmtree(backup)


def install_skill(
    source_dir: Path,
    target_dir: Path,
    dry_run: bool = False,
    replace: bool = False,
    backup_root=None,
    pack: bool = False,
):
    source_dir = source_dir.resolve()
    if not source_dir.is_dir() or not (source_dir / "SKILL.md").is_file():
        raise InstallError(f"源目录缺少 SKILL.md: {source_dir}")
    if target_dir.is_symlink():
        raise ConflictError(f"目标目录是符号链接，拒绝安装: {target_dir}")
    target_dir = target_dir.resolve()
    if not target_dir.parent.is_dir():
        raise InstallError(f"目标父目录不存在: {target_dir.parent}")
    if (
        target_dir == source_dir
        or target_dir.is_relative_to(source_dir)
        or source_dir.is_relative_to(target_dir)
    ):
        raise InstallError("源目录与目标目录不能相同或互相嵌套")
    resolved_backup_root = resolve_backup_root(backup_root)
    if resolved_backup_root == target_dir or resolved_backup_root.is_relative_to(target_dir):
        raise InstallError("备份目录不能位于目标目录内部")
    for path in source_dir.rglob("*"):
        if path.is_symlink():
            raise InstallError(f"源目录包含符号链接，拒绝安装: {path}")

    validation = validate_skill(source_dir, pack=pack)
    planned_backup = (
        backup_path(target_dir, resolved_backup_root)
        if target_dir.exists() and replace
        else None
    )
    if dry_run:
        print(f"DRY-RUN 源目录: {source_dir}")
        print(f"DRY-RUN 目标目录: {target_dir}")
        print("DRY-RUN 操作: 备份后替换" if planned_backup else "DRY-RUN 操作: 新建安装")
        if planned_backup:
            print(f"DRY-RUN 备份目录: {planned_backup}")
        print(f"DRY-RUN 校验: {validation}")
        return 0

    with installation_lock(target_dir):
        if target_dir.exists() and not target_dir.is_dir():
            raise ConflictError(f"目标路径已存在且不是目录: {target_dir}")
        if target_dir.exists() and not replace:
            raise ConflictError(f"目标目录已存在，默认拒绝覆盖: {target_dir}")

        staging_root = None
        backup = None
        backup_ready = False
        installed = False
        install_started = False
        try:
            staging_root = Path(
                tempfile.mkdtemp(prefix=f".{target_dir.name}.install-", dir=target_dir.parent)
            )
            staged_skill = staging_root / target_dir.name
            shutil.copytree(
                source_dir,
                staged_skill,
                symlinks=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
            validate_skill(staged_skill, pack=pack)
            if target_dir.exists() and not replace:
                raise ConflictError(f"目标目录已存在，默认拒绝覆盖: {target_dir}")
            if target_dir.exists():
                backup = backup_path(target_dir, resolved_backup_root)
                create_backup(target_dir, backup)
                backup_ready = True
                shutil.rmtree(target_dir)
            install_started = True
            os.replace(staged_skill, target_dir)
            installed = True
        except BaseException as error:
            rollback_errors = []
            installed_target = installed or (
                install_started and not staged_skill.exists() and target_dir.exists()
            )
            if installed_target and target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            if backup_ready and backup is not None and backup.exists():
                try:
                    restore_backup(backup, target_dir)
                except (OSError, InstallError) as rollback_error:
                    rollback_errors.append(str(rollback_error))
            elif backup is not None and backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            if rollback_errors:
                raise InstallError(
                    f"{error}；回滚失败: {'; '.join(rollback_errors)}；备份保留在 {backup}"
                ) from error
            raise
        finally:
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)

    print(f"INSTALLED {target_dir}")
    if backup is not None:
        print(f"BACKUP {backup}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="事务式安装 knowledge-distill 产物")
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("target_dir", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只打印安装计划")
    parser.add_argument("--replace", action="store_true", help="备份并替换已有目标目录")
    parser.add_argument("--pack", action="store_true", help="按 pack 路由入口契约校验")
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=None,
        help="备份写入目录（默认系统临时目录下 knowledge-distill-backups）",
    )
    arguments = parser.parse_args()
    try:
        return install_skill(
            arguments.source_dir,
            arguments.target_dir,
            dry_run=arguments.dry_run,
            replace=arguments.replace,
            backup_root=arguments.backup_root,
            pack=arguments.pack,
        )
    except ConflictError as error:
        print(f"CONFLICT {error}")
        return 2
    except (InstallError, OSError) as error:
        print(f"ERROR {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
