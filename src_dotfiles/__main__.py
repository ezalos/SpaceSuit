#!/usr/bin/env python3

import fire
import os
from pathlib import Path
from src_dotfiles.database import Dependencies
from src_dotfiles.config import config, resolve_main_path
from src_dotfiles.models import DeployedDotFile, DotFileModel
from src_dotfiles.DotFile import DotFile
from ezpy_logs.LoggerFactory import LoggerFactory
from typing import List, Optional

LoggerFactory.setup_LoggerFactory()
logger = LoggerFactory.getLogger(__name__)

def path_security_check(path: str) -> bool:
    if not path.startswith(config.home):
        logger.warning(f"/!\\ CAREFULL -> Path {path} do not start by {config.home}")
        logger.warning("\tCurrent path resolution might cause a lot of problems")
        if 'y' != input("If you are sure to continue: enter 'y':"):
            logger.info("Exiting...")
            return True
    return False

def _check_skills_integrity(skills_dir: Optional[Path] = None) -> List[str]:
    """Dangling symlinks under ~/.claude/skills/ rot silently; return them so deploy can hard-error."""
    skills_dir = skills_dir or Path.home() / ".claude" / "skills"
    if not skills_dir.is_dir():
        return []
    return [
        str(entry)
        for entry in sorted(skills_dir.iterdir())
        if entry.is_symlink() and not entry.exists()
    ]

class ManageDotfiles:
    def __init__(self):
        self.db = Dependencies()

    def add(self, path: str, alias: Optional[str] = None, force: bool = False, only_device: Optional[str] = None) -> Optional[str]:
        """Add a new dotfile to be managed by the system.

        Args:
            path (str): Path to the dotfile to add
            alias (Optional[str]): Custom alias for the dotfile. If not provided, will be generated from filename.
            force (bool): Whether to force add if alias already exists.
            only_device (Optional[str]): If set, restrict this dotfile to the given device identifier.

        Returns:
            Optional[str]: Alias of the added dotfile if successful, None if failed

        Raises:
            NotImplementedError: If force=True and trying to add different path for existing alias
        """
        logger.debug(f"{path = } {alias = } {force = } {only_device = }")

        new_dot_file = self.db.create_dotfile(path, alias, only_device=only_device)

        current_dot_file = self.db.select_by_alias(new_dot_file.data.alias)
        if not current_dot_file:
            logger.info(f"Alias {new_dot_file.data.alias} does not exist in the system")
            new_dot_file.add_file()
            self.db.data.append(new_dot_file)
            self.db.save_all()
            return new_dot_file.data.alias
        else:       
            if current_dot_file and not force:
                logger.warning(f"Alias {new_dot_file.data.alias} already exists in the system, and force is not set")
                return None
            logger.info(f"Alias {current_dot_file.data.alias} already exists in the system, and force is set")
            logger.debug(f"{current_dot_file.data = }")
            logger.debug(f"{new_dot_file.data = }")
            if current_dot_file.data.deploy[new_dot_file.identifier].deploy_path == new_dot_file.data.deploy[new_dot_file.identifier].deploy_path:
                logger.debug("Argument path is the same as the one in the system")
                # Path resolution is way more complex than this :/
                new_dot_file.backup()
                new_dot_file.deploy()
                self.db.data.append(new_dot_file)
                self.db.save_all()
                return new_dot_file.data.alias
            else:
                logger.error("Argument path is different from the one in the system")
                logger.error(f"{current_dot_file.data.deploy[current_dot_file.identifier].deploy_path} != {new_dot_file.data.deploy[new_dot_file.identifier].deploy_path}")
                raise NotImplementedError

    def register(
        self,
        alias: str,
        deploy_path: str,
        main: Optional[str] = None,
        only_device: Optional[str] = None,
        force: bool = False,
        fanout: bool = False,
    ) -> Optional[str]:
        """Register an existing-in-place dotfile and deploy it.

        Use when the source already lives at `~/42/SpaceSuit/<main>` (e.g. a config
        authored directly into `dotfiles/`) and only the registry entry +
        deploy symlink need to be created. Unlike `add`, this does NOT back up
        or copy the source — it is already where it belongs; only the symlink
        at `deploy_path` is created. Owned Claude skills are the exception:
        they live in `skills/` and deploy via the single `skills` fan-out
        entry (`--fanout`), not a per-skill register.

        Args:
            alias (str): Registry alias (must not collide unless force=True).
            deploy_path (str): Absolute path where the symlink should land on
                the current device.
            main (Optional[str]): Path inside ~/42/SpaceSuit/ to the source. Default:
                `dotfiles/<alias>` (matches the registry convention).
            only_device (Optional[str]): If set, restrict deploy to this device
                identifier. For device-scoped skills, pass the current device.
            force (bool): Allow overwriting an existing entry with the same alias.
            fanout (bool): If set, `main` is treated as a container directory
                whose immediate children each deploy as their own symlink
                under `deploy_path`, instead of `deploy_path` itself being a
                single symlink to `main`.

        Returns:
            Optional[str]: The alias on success, None on collision (force=False)
                or missing source.
        """
        logger.debug(f"{alias = } {deploy_path = } {main = } {only_device = } {force = }")

        if main is None:
            main = Path(config.dotfiles_dir).joinpath(alias).as_posix()

        source_abs = Path(resolve_main_path(main))
        if not source_abs.exists():
            logger.error(f"register: source {source_abs} does not exist; nothing to register")
            return None

        if not os.path.isabs(deploy_path):
            logger.error(f"register: deploy_path {deploy_path!r} must be absolute")
            return None
        if not deploy_path.startswith(config.home):
            logger.warning(
                f"register: deploy_path {deploy_path!r} is not under home {config.home!r}; "
                "proceeding anyway"
            )

        identifier = config.identifier
        existing = self.db.metadata.dotfiles.get(alias)
        if existing is not None and not force:
            logger.warning(f"register: alias {alias!r} already exists and force is not set")
            return None

        if existing is not None:
            logger.info(f"register: alias {alias!r} exists and force is set; updating deploy entry for {identifier}")
            existing.deploy[identifier] = DeployedDotFile(deploy_path=deploy_path, backups=[])
            existing.fanout = fanout
            if only_device and existing.only_devices is not None and identifier not in existing.only_devices:
                existing.only_devices.append(identifier)
            model = existing
        else:
            model = DotFileModel(
                alias=alias,
                main=main,
                deploy={identifier: DeployedDotFile(deploy_path=deploy_path, backups=[])},
                only_devices=[only_device] if only_device else None,
                fanout=fanout,
            )

        dot_file = DotFile(model, identifier)
        dot_file.deploy()  # symlink only — no backup/copy, source is already in place
        self.db.metadata.dotfiles[alias] = model
        self.db.data.append(dot_file)
        self.db.save_all()
        logger.info(f"register: {alias} -> {deploy_path} (main={main})")
        return alias

    def set_main(self, alias: str, new_main: str, device: Optional[str] = None) -> Optional[str]:
        """Repoint an existing dotfile's `main` (or a per-device variant) to a
        new source path.

        For moving a dotfile's real copy out of ~/42/SpaceSuit (e.g. into a private
        repo) without losing its registry history: the alias, deploy paths,
        and backups are untouched, only the source path field changes. Does
        NOT move or copy the underlying file -- it must already exist at
        `new_main` (absolute paths are used as-is; relative paths join with
        ~/42/SpaceSuit, same as any other `main`). Refuses to repoint to a source
        that doesn't exist, so the registry can never point at nothing.

        Re-deploys the symlink for the current device immediately after
        updating the field, so the live deploy_path repoints in the same
        step (never hand-edit dotfiles.json and redeploy separately).

        Args:
            alias (str): Existing alias to repoint (must already exist).
            new_main (str): New value for `main` (or the variant, if `device`
                is given).
            device (Optional[str]): If set, repoints this device's entry in
                `variants` instead of the alias-wide `main`. The device must
                already have a variant entry (set_main does not create new
                variants).

        Returns:
            Optional[str]: alias on success, None if alias is unknown, the
                requested device has no variant entry, or new_main does not
                exist on disk.
        """
        model = self.db.metadata.dotfiles.get(alias)
        if model is None:
            logger.error(f"set-main: no entry for alias {alias!r}")
            return None

        source_abs = Path(resolve_main_path(new_main))
        if not source_abs.exists():
            logger.error(f"set-main: new main {source_abs} does not exist; refusing to repoint to a missing source")
            return None

        if device is not None:
            if not model.variants or device not in model.variants:
                logger.error(f"set-main: {alias!r} has no variant entry for device {device!r}")
                return None
            old_value = model.variants[device]
            model.variants[device] = new_main
        else:
            old_value = model.main
            model.main = new_main

        self.db.metadata.dotfiles[alias] = model

        # Only touch the in-memory working set (and attempt a local redeploy) if
        # this alias actually has a deploy entry for the current device -- e.g.
        # a per-host config like nginx.conf that only deploys on a different
        # box has no entry for us here, and there is nothing local to redeploy.
        if config.identifier in model.deploy:
            self.db.data = [d for d in self.db.data if d.data.alias != alias]
            dot_file = DotFile(model, config.identifier)
            self.db.data.append(dot_file)
            if device is None or device == config.identifier:
                dot_file.deploy()

        self.db.save_all()
        print(f"{alias}: {old_value} -> {new_main}")
        logger.info(f"set-main: {alias}: {old_value} -> {new_main}")
        return alias

    def rename_device(self, old_identifier: str, new_identifier: str, force: bool = False) -> Optional[str]:
        """Rename a device identifier everywhere it appears in the registry.

        Generic registry-migration primitive (not tied to any specific
        identifier scheme): updates `devices`, and for every dotfile updates
        `deploy` keys, `only_devices` entries, and `variants` keys. Never
        hand-edit dotfiles.json -- this is the sanctioned mechanism for a
        bulk identifier rename (e.g. dropping a stale suffix from a device's
        name).

        Args:
            old_identifier (str): Existing device identifier (must be present
                in `devices`).
            new_identifier (str): Replacement identifier.
            force (bool): Allow overwriting if new_identifier already exists
                in `devices` (default False: refuse on collision).

        Returns:
            Optional[str]: new_identifier on success, None if old_identifier
                is unknown or new_identifier collides without force.
        """
        if old_identifier not in self.db.metadata.devices:
            logger.error(f"rename-device: no device {old_identifier!r} in registry")
            return None
        if new_identifier in self.db.metadata.devices and not force:
            logger.error(f"rename-device: {new_identifier!r} already exists; pass force=True to overwrite")
            return None

        dev = self.db.metadata.devices.pop(old_identifier)
        dev.identifier = new_identifier
        self.db.metadata.devices[new_identifier] = dev

        for model in self.db.metadata.dotfiles.values():
            if old_identifier in model.deploy:
                model.deploy[new_identifier] = model.deploy.pop(old_identifier)
            if model.only_devices is not None:
                model.only_devices = [
                    new_identifier if d == old_identifier else d for d in model.only_devices
                ]
            if model.variants is not None and old_identifier in model.variants:
                model.variants[new_identifier] = model.variants.pop(old_identifier)

        self.db.save_all()
        logger.info(f"rename-device: {old_identifier} -> {new_identifier}")
        return new_identifier

    def scrub_path_substring(self, old_substring: str, new_substring: str) -> int:
        """Replace a literal substring in every path-like string in the registry.

        Walks `devices[*].home_path` and, for every dotfile, its `main`, every
        `variants[*]` path, each device's `deploy_path` and every
        `backups[*].backup_path`. Does NOT touch dict keys (device
        identifiers, aliases) -- use `rename_device` for those.
        Generic registry-migration primitive: never hand-edit dotfiles.json.

        Args:
            old_substring (str): Literal substring to find (not a regex).
            new_substring (str): Replacement.

        Returns:
            int: number of string fields changed.
        """
        changed = 0

        for dev in self.db.metadata.devices.values():
            if old_substring in dev.home_path:
                dev.home_path = dev.home_path.replace(old_substring, new_substring)
                changed += 1

        for model in self.db.metadata.dotfiles.values():
            if old_substring in model.main:
                model.main = model.main.replace(old_substring, new_substring)
                changed += 1
            if model.variants:
                for dev_id, variant_path in model.variants.items():
                    if old_substring in variant_path:
                        model.variants[dev_id] = variant_path.replace(old_substring, new_substring)
                        changed += 1
            for deployed in model.deploy.values():
                if old_substring in deployed.deploy_path:
                    deployed.deploy_path = deployed.deploy_path.replace(old_substring, new_substring)
                    changed += 1
                for backup in deployed.backups:
                    if old_substring in backup.backup_path:
                        backup.backup_path = backup.backup_path.replace(old_substring, new_substring)
                        changed += 1

        self.db.save_all()
        logger.info(f"scrub-path-substring: {changed} field(s) changed ({old_substring!r} -> {new_substring!r})")
        return changed

    def extend_to(self, alias: str, device: str, deploy_path: Optional[str] = None) -> None:
        """Extend an existing dotfile to a new device.

        Adds `device` to the dotfile's `deploy` map and, if `only_devices` is set,
        appends `device` to that list too. Does NOT copy files to the target
        device — run `deploy` on the target host afterwards (typically via
        setup_sync_down + `python -m src_dotfiles deploy`).

        Args:
            alias (str): Alias of the dotfile to extend (must already exist).
            device (str): Device identifier to add (e.g. "TinyButMighty.ezalos").
            deploy_path (Optional[str]): Where the dotfile should land on the
                target device. If omitted, reuses the deploy_path of an existing
                deploy entry — fine when the home_path is identical on both
                devices, wrong otherwise.
        """
        model = self.db.metadata.dotfiles.get(alias)
        if model is None:
            logger.error(f"No dotfile with alias {alias!r} in registry")
            return

        if deploy_path is None:
            if not model.deploy:
                logger.error(f"{alias} has no existing deploy entries; --deploy-path is required")
                return
            sample_device, sample_entry = next(iter(model.deploy.items()))
            deploy_path = sample_entry.deploy_path
            logger.info(f"Defaulting deploy_path to {deploy_path!r} (copied from {sample_device})")

        if device in model.deploy:
            existing = model.deploy[device].deploy_path
            if existing == deploy_path:
                logger.info(f"{alias} already deploys to {device} at {deploy_path}; no change to deploy map")
            else:
                logger.error(
                    f"{alias} already has a deploy entry for {device} at {existing!r} "
                    f"which differs from requested {deploy_path!r}; refusing to overwrite"
                )
                return
        else:
            model.deploy[device] = DeployedDotFile(deploy_path=deploy_path, backups=[])
            logger.info(f"Added deploy entry for {device}: {deploy_path}")

        if model.only_devices is not None and device not in model.only_devices:
            model.only_devices.append(device)
            logger.info(f"Appended {device} to only_devices")
        elif model.only_devices is not None:
            logger.info(f"{device} already in only_devices")

        self.db.metadata.dotfiles[alias] = model
        self.db.save_all()
        logger.info(f"Saved. Now run `python -m src_dotfiles deploy --alias {alias}` on {device}.")

    def set_global(self, alias: str) -> None:
        """Mark a dotfile as eligible for deployment on every device.

        Clears `only_devices` (sets it to None), so any device running
        `deploy` will create the symlink — deploy paths are translated
        per-device from existing entries using each device's home_path.

        Args:
            alias (str): Existing alias to make global.
        """
        model = self.db.metadata.dotfiles.get(alias)
        if model is None:
            logger.error(f"No dotfile with alias {alias!r} in registry")
            return
        if model.only_devices is None:
            logger.info(f"{alias} already global (only_devices=None); no change")
            return
        previous = list(model.only_devices)
        model.only_devices = None
        self.db.metadata.dotfiles[alias] = model
        self.db.save_all()
        logger.info(f"{alias}: cleared only_devices (was {previous})")

    def set_only_devices(self, alias: str, devices: str) -> Optional[str]:
        """Restrict an existing dotfile to an explicit set of devices.

        Sets `only_devices` on an existing entry directly, gating a
        currently-global (`only_devices=None`) or differently-scoped dotfile
        down to the given device identifiers in one call -- e.g. a registry
        entry that authors Louis's personal identity (CLAUDE.md, Claude
        settings) but was mistakenly left un-gated. Unlike `extend_to`
        (append one device, only if already gated) or `set_global` (clear to
        None), this replaces the whole list unconditionally. Never hand-edit
        dotfiles.json -- this is the sanctioned mechanism for a direct gate.

        Args:
            alias (str): Existing alias to gate (must already exist).
            devices (str): Comma-separated device identifiers, e.g.
                "TheBeast.ezalos,TinyButMighty.ezalos". Whitespace around
                each entry is stripped; empty entries are dropped.

        Returns:
            Optional[str]: alias on success, None if alias is unknown or the
                resulting device list would be empty.
        """
        model = self.db.metadata.dotfiles.get(alias)
        if model is None:
            logger.error(f"set-only-devices: no entry for alias {alias!r}")
            return None

        device_list = [d.strip() for d in devices.split(",") if d.strip()]
        if not device_list:
            logger.error("set-only-devices: devices list is empty")
            return None

        previous = model.only_devices
        model.only_devices = device_list
        self.db.metadata.dotfiles[alias] = model
        self.db.save_all()
        logger.info(f"set-only-devices: {alias}: only_devices {previous} -> {device_list}")
        return alias

    def deregister(self, alias: str, remove_link: bool = True) -> Optional[str]:
        """Remove a dotfile entry from the registry (and its stale symlink).

        Removes `alias` from dotfiles.json. When remove_link is True and the
        deploy_path for the current device is a symlink pointing into the repo,
        unlinks that symlink too. Never rmtree's a real directory. Use to retire
        entries superseded by another (e.g. individual claude_skill_* entries
        replaced by a single fan-out `skills` entry).

        Args:
            alias (str): Alias to remove (must exist).
            remove_link (bool): Also unlink the repo-pointing symlink at the
                current device's deploy_path. Default True.

        Returns:
            Optional[str]: The alias on success, None if the alias is unknown.
        """
        model = self.db.metadata.dotfiles.get(alias)
        if model is None:
            logger.warning(f"deregister: no entry for alias {alias!r}")
            return None

        identifier = config.identifier
        if remove_link and identifier in model.deploy:
            dp = model.deploy[identifier].deploy_path
            if os.path.islink(dp):
                raw_target = os.readlink(dp)
                repo = os.path.realpath(config.project_path)
                resolved = os.path.realpath(dp)  # canonical absolute target, handles relative links
                if resolved == repo or resolved.startswith(repo + os.sep):
                    os.unlink(dp)
                    logger.info(f"deregister: unlinked {dp} -> {raw_target}")
                else:
                    logger.warning(f"deregister: {dp} -> {raw_target} points outside repo; leaving it")
            elif os.path.exists(dp):
                logger.warning(f"deregister: {dp} is not a symlink; leaving it in place")

        # Remove from both metadata and the in-memory working set so save_all
        # (which reconciles self.data into metadata) does not resurrect it.
        self.db.metadata.dotfiles.pop(alias, None)
        self.db.data = [d for d in self.db.data if d.data.alias != alias]
        self.db.save_all()
        logger.info(f"deregister: removed {alias}")
        return alias

    def deploy(self, alias: Optional[str] = None, skills_dir: Optional[Path] = None) -> None:
        """Deploy dotfiles to the system.

        Idempotent: already-correct symlinks are skipped silently. In all-mode,
        per-file failures are caught and reported in a summary instead of
        aborting the run.

        Args:
            alias (Optional[str]):  Alias of the dotfile to deploy.
                                    If not provided, will deploy all dotfiles.
            skills_dir (Optional[Path]): Override for the skills integrity check
                                    (used by tests to avoid depending on the real
                                    ~/.claude/skills state). Defaults to
                                    ~/.claude/skills when not provided.
        """
        if alias is None:
            logger.info("Deploying all dotfiles")
            created, skipped, failed = [], [], []
            for dot_file in self.db.data:
                a = dot_file.data.alias
                try:
                    dot_file.backup()
                    if dot_file.deploy():
                        created.append(a)
                    else:
                        skipped.append(a)
                except Exception as e:
                    logger.error(f"deploy failed for {a}: {e}")
                    failed.append((a, str(e)))
            logger.info(
                f"deploy summary: {len(created)} created, "
                f"{len(skipped)} already correct, {len(failed)} failed"
            )
            for a, reason in failed:
                logger.warning(f"  FAILED {a}: {reason}")
        else:
            dot_file = self.db.select_by_alias(alias)
            if dot_file is None:
                logger.warning(f"There is no match in database for {alias}")
                return None
            logger.info(f"Deploying {alias}")
            dot_file.backup()
            dot_file.deploy()
        self.db.save_all()

        dangling = _check_skills_integrity(skills_dir)
        if dangling:
            for entry in dangling:
                logger.error(f"dangling skill symlink: {entry}")
            raise RuntimeError(
                f"{len(dangling)} dangling skill symlink(s) under ~/.claude/skills; delete or fix them"
            )

if __name__ == "__main__":
    fire.Fire(ManageDotfiles())