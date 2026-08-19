# File operations
import os
import shutil
import subprocess
from datetime import datetime
from src_dotfiles.config import config, resolve_main_path
from pathlib import Path
from ezpy_logs.LoggerFactory import LoggerFactory
from src_dotfiles.models import DotFileModel, BackupMetadata, Identifier, DeployedDotFile, DevicesData

logger = LoggerFactory.getLogger(__name__)
DATETIME_FORMAT = "%Y-%m-%d_%H:%M:%S.%f"

def get_time() -> str:
    """Get current time in the format used for backups

    Returns:
        str: Current time in format YYYY-MM-DD_HH:MM:SS.NNNNNN
    """
    now = datetime.now()
    current_time = now.strftime(DATETIME_FORMAT)
    return current_time

class DotFile:
    """Operations wrapper for a dotfile in the system.
    
    This class handles the file system operations for a dotfile, such as:
    - Backing up the existing file before modifications
    - Deploying the dotfile as a symlink
    - Managing the main copy in the dotfiles directory

    The state is stored in the provided DotFileModel instance.
    """
    def __init__(self, data: DotFileModel, identifier: Identifier = config.identifier):
        """Initialize with a DotFileModel instance.

        Args:
            data (DotFileModel): The model containing the dotfile's state
            identifier (str): The identifier of the device where the dotfile is deployed
        """
        self.data = data
        self.identifier = identifier

    def add_file(self, use_as_main: bool = True, deploy: bool = True) -> None:
        """Add a new dotfile to be managed.
        
        Args:
            use_as_main (bool): Whether to copy current file as main version
            deploy (bool): Whether to deploy the symlink after adding
        """
        logger.info(f'Adding {self.data.alias} from {self.data.deploy[self.identifier].deploy_path}')

        if os.path.islink(self.data.deploy[self.identifier].deploy_path):
            logger.warning(f'Error: {self.data.alias} is already a symlink')
            return
        self.backup()
        if use_as_main:
            self.copy_as_main()
        if deploy:
            self.deploy()

    def deploy(self) -> bool:
        """Deploy the dotfile in the system.

        Creates a symlink from the system path to the main version.
        Idempotent: if the target is already the correct symlink, returns False
        without touching anything. Otherwise removes whatever is at the target
        path and creates the symlink, returning True.

        If the symlink lands somewhere the current user can't write (e.g.
        /etc/nginx/nginx.conf), falls back to `sudo ln -sfn` for just that
        one entry — keeps the rest of the run as the unprivileged user.
        """
        deploy_path = self.data.deploy[self.identifier].deploy_path

        # Resolve variant: use device-specific file if one exists, otherwise main
        source = self.data.main
        if self.data.variants and self.identifier in self.data.variants:
            source = self.data.variants[self.identifier]
            logger.info(f"Using variant for {self.identifier}: {source}")
        target = resolve_main_path(source)

        # Fan-out: main is a container; link each child dir into deploy_path.
        if self.data.fanout:
            return self._deploy_fanout(deploy_path, target)

        # Idempotent: already the right symlink → no-op.
        if os.path.islink(deploy_path) and os.readlink(deploy_path) == target:
            logger.debug(f"{deploy_path} already symlinks to {target}; skipping")
            return False

        try:
            self._deploy_as_user(deploy_path, target)
        except PermissionError as e:
            logger.warning(f"{deploy_path}: permission denied ({e}); escalating via sudo")
            self._deploy_via_sudo(deploy_path, target)

        logger.info(f"Symlink created {deploy_path} -> {target}")
        return True

    def _deploy_as_user(self, deploy_path: str, target: str) -> None:
        """Remove anything at deploy_path and symlink target → deploy_path as the current user."""
        if os.path.lexists(deploy_path):
            logger.debug(f'Deleting {deploy_path}')
            if os.path.isdir(deploy_path) and not os.path.islink(deploy_path):
                shutil.rmtree(deploy_path)
            else:
                os.remove(deploy_path)

        dirs = os.path.dirname(deploy_path)
        if not os.path.exists(dirs):
            logger.info(f'{dirs} does not exist: creating it')
            os.makedirs(dirs)

        os.symlink(target, deploy_path)

    def _deploy_via_sudo(self, deploy_path: str, target: str) -> None:
        """Replace whatever's at deploy_path with a symlink to target, using sudo.

        Refuses to recurse into directories (sudo rm -rf on a system path is too
        sharp a tool for an auto-elevation fallback); the caller must clean up
        manually if deploy_path is a non-symlink directory.
        """
        if os.path.isdir(deploy_path) and not os.path.islink(deploy_path):
            raise PermissionError(
                f"{deploy_path} is a directory (not a symlink); refusing to sudo-rmtree. "
                f"Remove it manually then re-run deploy."
            )
        result = subprocess.run(
            ["sudo", "ln", "-sfn", target, deploy_path],
            stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            raise PermissionError(
                f"sudo ln failed for {deploy_path}: {result.stderr.strip() or 'unknown error'}"
            )

    def _deploy_fanout(self, container_deploy_path: str, container_target: str) -> bool:
        """Symlink each immediate child directory of container_target into
        container_deploy_path.

        container_target is the repo source dir (e.g. .../Setup/skills);
        container_deploy_path is the on-system container (e.g. ~/.claude/skills).
        Files (e.g. EXTERNAL.md) and dot-dirs are skipped, so only real skills
        are linked. Idempotent per child. A real (non-symlink) dir/file at a
        child path is backed up before replacement; dangling/wrong symlinks are
        removed without backup. Returns True if any child link was created.
        """
        os.makedirs(container_deploy_path, exist_ok=True)
        changed = False
        for name in sorted(os.listdir(container_target)):
            if name.startswith('.'):
                continue
            child_target = os.path.join(container_target, name)
            if not os.path.isdir(child_target):
                continue
            child_deploy = os.path.join(container_deploy_path, name)

            if os.path.islink(child_deploy) and os.readlink(child_deploy) == child_target:
                logger.debug(f"{child_deploy} already symlinks to {child_target}; skipping")
                continue

            if os.path.exists(child_deploy) and not os.path.islink(child_deploy):
                self._backup_path(child_deploy)

            try:
                self._deploy_as_user(child_deploy, child_target)
            except PermissionError as e:
                logger.warning(f"{child_deploy}: permission denied ({e}); escalating via sudo")
                self._deploy_via_sudo(child_deploy, child_target)
            logger.info(f"Symlink created {child_deploy} -> {child_target}")
            changed = True
        return changed

    def _backup_path(self, src_path: str) -> None:
        """Back up an arbitrary real path into the backup dir and record it."""
        stime = get_time()
        name = os.path.basename(src_path.rstrip('/'))
        backup_path = Path(config.project_path).joinpath(config.backup_dir).joinpath(
            f"{self.data.alias}_{name}_{config.identifier}_{stime}"
        ).as_posix()
        Path(backup_path).parent.mkdir(parents=True, exist_ok=True)
        if os.path.isdir(src_path) and not os.path.islink(src_path):
            shutil.copytree(src_path, backup_path)
        else:
            shutil.copy(src_path, backup_path)
        logger.info(f"Backed up {src_path} as {backup_path}")
        self.data.deploy[self.identifier].backups.append(BackupMetadata(
            backup_path=backup_path, datetime=stime
        ))

    def backup(self) -> None:
        """Create a backup of the current file if it exists and is not a symlink."""
        if self.data.fanout:
            # Container entry: children are backed up individually during fan-out deploy.
            logger.debug(f"{self.data.alias}: fanout entry, skipping container backup")
            return

        if not os.path.exists(self.data.deploy[self.identifier].deploy_path):
            logger.warning(f"{self.data.deploy[self.identifier].deploy_path} does not exist, no backup will be done")
            return

        if os.path.islink(self.data.deploy[self.identifier].deploy_path):
            logger.warning(f"File is already a symlink, it will not be backup")
            return

        stime = get_time()
        backup_path = Path(config.project_path).joinpath(config.backup_dir).joinpath(
            f"{self.data.alias}_{config.identifier}_{stime}"
        ).as_posix()

        logger.debug(f"{self.data.deploy[self.identifier].deploy_path = }")
        logger.debug(f"{backup_path = }")
        
        deploy_path = self.data.deploy[self.identifier].deploy_path
        if os.path.isdir(deploy_path) and not os.path.islink(deploy_path):
            shutil.copytree(deploy_path, backup_path)
        else:
            shutil.copy(deploy_path, backup_path)
        logger.info(f"Backed up as {backup_path}")
        
        self.data.deploy[self.identifier].backups.append(BackupMetadata(
            backup_path=backup_path,
            datetime=stime
        ))

    def copy_as_main(self, force: bool = False) -> None:
        """Copy the current file as the main version.
        
        Args:
            force (bool): Whether to overwrite existing main file
        """
        main_abs = resolve_main_path(self.data.main)
        logger.info(f"Copying {self.data.deploy[self.identifier].deploy_path} as main to {main_abs}")
        if os.path.exists(main_abs):
            logger.warning(f'File {self.data.deploy[self.identifier].deploy_path} already exist in Setup')
            if not force:
                return

        if os.path.exists(self.data.deploy[self.identifier].deploy_path):
            shutil.copy(self.data.deploy[self.identifier].deploy_path, main_abs)
            logger.info(f'{main_abs} has been added as main for {self.data.deploy[self.identifier].deploy_path}')

    def translate_to_device(self, original_device: DevicesData, target_device: DevicesData) -> "DotFile":
        """Create a new DotFile instance with paths translated for the target device.
        
        Args:
            original_device: Device data where the paths are currently based
            target_device: Device data where we want to translate paths to
            
        Returns:
            New DotFile instance with translated paths for target device
        """
        logger.debug(f"Translating paths from {original_device.identifier} to {target_device.identifier}")
        assert original_device.identifier in self.data.deploy.keys()
        logger.debug(f"Original path: {self.data.deploy[original_device.identifier].deploy_path}")
        logger.debug(f"Original main: {self.data.main}")

        # Translate system path (e.g., /home/user/.zshrc -> /Users/user/.zshrc)
        new_path = self.data.deploy[original_device.identifier].deploy_path
        if original_device.home_path in new_path:
            new_path = new_path.replace(original_device.home_path, target_device.home_path, 1)
        else:
            # If path doesn't contain original home, assume it's relative to home
            new_path = str(Path(target_device.home_path) / self.data.deploy[original_device.identifier].deploy_path)
        logger.debug(f"Translated path: {new_path}")

        # Translate main path (e.g., dotfiles/.zshrc -> test_dotfiles/.zshrc)
        # ~/-prefixed mains are portable by construction (resolved against each
        # device's home at deploy time); swapping their dotfiles-dir segment
        # would corrupt an external-repo path, so they pass through unchanged.
        new_main = self.data.main
        if not new_main.startswith("~") and original_device.dotfiles_dir_path in new_main:
            new_main = new_main.replace(
                original_device.dotfiles_dir_path,
                target_device.dotfiles_dir_path,
                1
            )
        logger.debug(f"Translated main: {new_main}")

        # Create new model with translated paths
        new_model = DotFileModel(
            alias=self.data.alias,
            main=new_main,
            deploy={
                target_device.identifier: DeployedDotFile(
                    deploy_path=new_path,
                    backups=[]
                )
            },
            only_devices=self.data.only_devices,
            variants=self.data.variants,
            fanout=self.data.fanout,
        )
        
        return DotFile(new_model, target_device.identifier)

    def __str__(self) -> str:
        msg = f"{self.identifier}\n"
        msg += f"{self.data.alias} : {self.data.main}\n"
        for identifier, deploy in self.data.deploy.items():
            msg += f"\t@{identifier} : {deploy.deploy_path}\n"
            for b in deploy.backups:
                msg += f"\t\t{b.datetime} -> {b.backup_path}\n"
        return msg
