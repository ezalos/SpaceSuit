from src_dotfiles.config import config
import json
import re
from pathlib import Path
from ezpy_logs.LoggerFactory import LoggerFactory
from typing import List, Optional
from src_dotfiles.models import DotFileModel, MetaDataDotFiles, DeployedDotFile, DevicesData
from src_dotfiles.DotFile import DotFile

logger = LoggerFactory.getLogger(__name__)

# Moving parts by identifiers:
# - hostname
# - HOME directory :
#       Allow to reuse between OS :
#           -> Mac /Users/ezalos  to /home/ezalos in linux
#       Allow to reuse between users and root :
#           -> /home/ezalos in linux to /home/root in docker
# - Setup location :
#       At the moment everything is in HOME/Setup/dotfiles, but should be configurable


class Dependencies:
    """Manages the collection of dotfiles and their metadata.
    
    This class handles loading and saving the metadata file, and provides
    access to individual dotfiles. It maintains both the raw metadata (through
    Pydantic models) and the operational DotFile instances.
    """
    def __init__(self):
        """Initialize the Dependencies manager.
        
        Loads metadata from file if it exists, otherwise creates empty metadata.
        Then loads all dotfiles and translates them to current device if needed.
        """
        self.test: bool = False
        self.data: List[DotFile] = []  # Initialize data as empty list first
        logger.debug(f"{config.depedencies_path = }")
        logger.debug(f"{config.dotfiles_dir = }")
        self.path: str = config.depedencies_path
        self.path_test: str = config.dotfiles_dir + "test_" + "meta.json"
        self.db_path = self.get_db_path()
        logger.debug(f"{self.db_path = }")

        if self.db_path.exists():
            logger.debug(f"Loading metadata from {self.db_path}")
            with open(self.db_path, "r") as f:
                self.metadata = MetaDataDotFiles.model_validate_json(f.read())
        elif self._legacy_db_path().exists():
            logger.info(f"Migrating from {self._legacy_db_path()} to {self.db_path}")
            with open(self._legacy_db_path(), "r") as f:
                self.metadata = MetaDataDotFiles.model_validate_json(f.read())
            # Ensure version is set (old files won't have it, Pydantic defaults to 1)
            self.save_all()
        else:
            logger.debug(f"Creating new metadata")
            self.metadata = MetaDataDotFiles(
                dotfiles={},
                devices={
                    config.identifier: config.device_data
                }
            )
            self.save_all()

        # Load and translate dotfiles after metadata is initialized
        self.data = self.load_all()

    def get_db_path(self) -> Path:
        """Get the path to the database file

        Returns:
            Path: Path to the database file
        """
        dest = self.path_test if self.test else self.path
        return Path(config.project_path).joinpath(dest)

    def _legacy_db_path(self) -> Path:
        """Get the path to the legacy meta_3.json file."""
        return Path(config.project_path).joinpath(config.legacy_meta3_path)

    def load(self) -> MetaDataDotFiles:
        """Loads the db in memory and validates it

        Returns:
            MetaDataDotFiles: Validated metadata from file
        """
        db_path = self.get_db_path()
        logger.debug(f"{db_path = }")

        if not db_path.exists():
            return MetaDataDotFiles()

        with open(db_path) as f:
            data = f.read()
            # If file is empty, return empty model
            if not data.strip():
                return MetaDataDotFiles()
            return MetaDataDotFiles.model_validate_json(data)

    def create_dotfile(self, path: str, alias: Optional[str] = None, only_device: Optional[str] = None) -> DotFile:
        """Create a new DotFile instance with appropriate model

        Args:
            path (str): Path where the dotfile should exist in the system
            alias (Optional[str]): Custom alias for the dotfile. Default: filename from path
            only_device (Optional[str]): If set, restrict this dotfile to the given device identifier.

        Returns:
            DotFile: New DotFile instance
        """
        if alias is None:
            alias = Path(path).name

        identifier=config.identifier
        main=Path(config.dotfiles_dir).joinpath(alias).as_posix()


        if alias in self.metadata.dotfiles.keys():
            dot_file_model = self.metadata.dotfiles[alias]
        else:
            dot_file_model = DotFileModel(
                alias=alias,
                main=main,
                deploy={
                    identifier: DeployedDotFile(
                        deploy_path=path,
                        backups=[]
                    )
                },
                only_devices=[only_device] if only_device else None,
            )
        
        if identifier in dot_file_model.deploy.keys():
            old_backups = dot_file_model.deploy[identifier].backups
        else:
            old_backups = []

        dot_file_model.deploy[identifier] = DeployedDotFile(
            deploy_path=path,
            backups=old_backups
        )
        return DotFile(dot_file_model, identifier)

    def _infer_device_data(self, identifier: str, deploy_path: str) -> Optional[DevicesData]:
        """Infer device metadata from identifier and a deploy path.

        Extracts home_path by matching standard home directory patterns
        in the deploy path. Returns None if no pattern matches.
        """
        # Standard home directory patterns (ordered by specificity)
        patterns = [
            r'^(/srv/data/home/[^/]+)',  # /srv/data/home/user
            r'^(/home/[^/]+)',            # /home/user
            r'^(/Users/[^/]+)',           # /Users/user (macOS)
            r'^(/root)',                   # /root
        ]
        for pattern in patterns:
            match = re.match(pattern, deploy_path)
            if match:
                home_path = match.group(1)
                logger.info(f"Inferred device data for {identifier}: home_path={home_path}")
                return DevicesData(
                    identifier=identifier,
                    home_path=home_path,
                    dotfiles_dir_path="dotfiles"  # safe default, matches all real data
                )
        return None

    def load_all(self) -> List[DotFile]:
        """Converts the metadata into usable DotFile objects.
        
        If a dotfile is from a different device, translates its paths to current device.
        
        Returns:
            List[DotFile]: List of DotFile objects
        """
        dotfiles = []
        for alias, model in self.metadata.dotfiles.items():
            # Skip dotfiles restricted to other devices
            if model.only_devices is not None and config.identifier not in model.only_devices:
                logger.debug(f"Skipping {alias}: only_devices={model.only_devices}, current={config.identifier}")
                continue
            if config.identifier not in model.deploy.keys():
                # Get device data for translation
                known_devices_in_model = [i for i in model.deploy.keys() if i in self.metadata.devices.keys()]

                if len(known_devices_in_model) == 0:
                    if len(model.deploy) == 0:
                        logger.warning(f"No deploy entries for {model.alias = }, skipping")
                        continue
                    # Try to infer device data from deploy path
                    any_device_id = list(model.deploy.keys())[0]
                    inferred = self._infer_device_data(any_device_id, model.deploy[any_device_id].deploy_path)
                    if inferred is None:
                        logger.warning(f"Cannot infer device data for {any_device_id} from deploy path, skipping {model.alias = }")
                        continue
                    self.metadata.devices[any_device_id] = inferred
                    model_identifier = any_device_id
                else:
                    model_identifier = known_devices_in_model[0]
                    
                original_device = self.metadata.devices[model_identifier]
                translated = DotFile(model).translate_to_device(original_device, config.device_data)
                dotfiles.append(translated)
            else:
                dotfiles.append(DotFile(model, config.identifier))
        self.update_dotfiles(dotfiles)
        return dotfiles

    def update_dotfiles(self, dotfiles: List[DotFile]) -> None:
        for dotfile in dotfiles:
            identifier = dotfile.identifier
            db_dotfile_model = self.metadata.dotfiles.get(dotfile.data.alias)
            if db_dotfile_model is None:
                self.metadata.dotfiles[dotfile.data.alias] = dotfile.data
                continue
            db_deploy = db_dotfile_model.deploy.get(identifier)
            if db_deploy is None:
                db_dotfile_model.deploy[identifier] = dotfile.data.deploy[identifier]
                self.metadata.dotfiles[dotfile.data.alias] = db_dotfile_model
                continue
            db_backups = db_deploy.backups
            for db_backup in db_backups:
                if db_backup not in dotfile.data.deploy[identifier].backups:
                    dotfile.data.deploy[identifier].backups.append(db_backup)
            db_dotfile_model.deploy[identifier] = dotfile.data.deploy[identifier]
            self.metadata.dotfiles[dotfile.data.alias] = db_dotfile_model

    def save_all(self) -> None:
        """Saves all dotfile data to the metadata file"""
        self.update_dotfiles(self.data)
        
        if config.identifier not in self.metadata.devices:
            self.metadata.devices[config.identifier] = config.device_data
            
        db_path = self.get_db_path()
        logger.info(f"Saving to {db_path}")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with open(db_path, 'w') as f:
            f.write(self.metadata.model_dump_json(indent=4))

    def select_by_alias(self, alias: str) -> Optional[DotFile]:
        """Select a dotfile by its alias

        Args:
            alias (str): The alias to search for

        Returns:
            Optional[DotFile]: The found DotFile or None if not found
        """
        selection = [d for d in self.data if d.data.alias == alias]
        if len(selection) == 1:
            return selection[0]
        elif len(selection) > 1:
            logger.warning(f"There is {len(selection)} dotfiles named {alias}")
            for d in selection:
                logger.warning(str(d))
            logger.warning("Selecting 1st entry!")
            return selection[0]
        else:
            logger.debug(f"There is no match in database for {alias}")
            return None
