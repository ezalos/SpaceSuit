from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
from pydantic import BaseModel, Field

Alias = str
Identifier = str

class BackupMetadata(BaseModel):
    """Represents a backup of a dotfile at a specific point in time

    Attributes:
        backup_path (str): Path where the backup is stored
        datetime (str): When the backup was made (format: YYYY-MM-DD_HH:MM)
    """
    backup_path: str
    datetime: str

class DeployedDotFile(BaseModel):
    """
    Represents a deployed instance of a dotfile on a specific system.

    Attributes:
        deploy_path (str): The path where the dotfile is deployed on the system.
        identifier (str): The unique identifier of the system where the dotfile is deployed.
        backups (List[BackupMetadata]): List of backups for the deployed dotfile.
    """
    deploy_path: str
    backups: List[BackupMetadata] = Field(default_factory=list)

class DotFileModel(BaseModel):
    """
    Represents a dotfile and its deployment configuration.

    A dotfile is a configuration file that can be tracked and synchronized across systems.
    This model includes the alias, the main version in the dotfiles directory, and deployment details.

    Attributes:
        alias (str): The name used to identify the dotfile (usually the filename or a unique alias).
        main (str): The path to the main version of the dotfile in the dotfiles directory.
        deploy (Dict[Identifier, DeployedDotFile]): Dictionary of deployment information for this dotfile on different systems.
        only_devices (Optional[List[Identifier]]): If set, restrict deployment to only these devices.
        variants (Optional[Dict[Identifier, str]]): Device-specific variant paths, mapping device identifier to an alternate dotfile path.
        fanout (bool): If True, `main` is a container directory and each of its
            immediate child directories is symlinked into `deploy_path` (which is
            itself a container, e.g. ~/.claude/skills). Defaults to False.
    """
    alias: Alias
    main: str
    deploy: Dict[Identifier, DeployedDotFile] = Field(default_factory=dict)
    only_devices: Optional[List[Identifier]] = None
    variants: Optional[Dict[Identifier, str]] = None
    fanout: bool = False

class DevicesData(BaseModel):
    """
    Represents metadata about a device/system where dotfiles can be deployed.

    Attributes:
        identifier (str): Unique identifier for the system (e.g., hostname.username).
        home_path (str): Path to the home directory on this system.
        dotfiles_dir_path (str): Path to the dotfiles directory on this system.
    """
    identifier: Identifier
    home_path: str 
    dotfiles_dir_path: str

class MetaDataDotFiles(BaseModel):
    """
    Root model representing the metadata for all dotfiles and devices.

    This model contains all tracked dotfiles and the configuration for each device
    where dotfiles can be deployed.

    Attributes:
        version (int): Schema version for forward-compatible migrations. Defaults to 1.
        dotfiles (Dict[str, DotFileModel]): Dictionary mapping dotfile aliases to their models.
        devices (Dict[str, DevicesData]): Mapping of system identifiers to their device configuration.
    """
    version: int = 1
    dotfiles: Dict[Alias, DotFileModel] = Field(default_factory=dict)
    devices: Dict[Identifier, DevicesData] = Field(default_factory=dict)
