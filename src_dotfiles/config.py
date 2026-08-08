from ezpy_logs.LoggerFactory import LoggerFactory
import socket
import re
from getpass import getuser
from pathlib import Path
import os
from src_dotfiles.models import DevicesData

LoggerFactory.setup_LoggerFactory()
logger = LoggerFactory.getLogger(__name__)

class DotDict(dict):
    """
    a dictionary that supports dot notation 
    as well as dictionary access notation 
    usage: d = DotDict() or d = DotDict({'val1':'first'})
    set attributes: d.val2 = 'second' or d['val2'] = 'second'
    get attributes: d.val2 or d['val2']
    """
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

config = DotDict()

def get_computer_name():
    identifier = socket.gethostname() + "." + getuser()
    identifier = re.sub(r"[^A-Za-z0-9\.]", ".", identifier)
    if identifier[-1] == ".":
        identifier = identifier + 'x'
    return identifier

def get_project_path(pwd=False):
    # TODO: Function uses a trick which is not viable long term
    project_path = Path(__file__).parent.parent.as_posix() # ~/Setup
    if pwd:
        surplus = "Setup/src_dotfiles"
    else:
        surplus = "src_dotfiles"
    if project_path[-len(surplus):] == surplus:
        project_path = project_path[:-len(surplus)]
    # project_path = "~/Setup/"
    logger.debug(f'Current {"pwd" if pwd else "project path"}: {project_path}')
    return project_path


def get_home_path():
    # The user's real home. Never derive this from the repo location: the old
    # grandparent-of-__file__ trick broke the day the repo moved to ~/42/SpaceSuit
    # (fanout deployed into ~/42/.claude on TinyButMighty, 2026-08-08).
    home_path = Path.home().as_posix()
    logger.debug(f'Current home path: {home_path}')
    return home_path

def set_config(dotfiles_dir="dotfiles"):
    global config
    config.pwd = get_project_path(pwd=False)
    config.home = get_home_path()
    config.project_path = get_project_path()
    config.dotfiles_dir = dotfiles_dir
    config.backup_dir = Path(config.dotfiles_dir).joinpath('old').as_posix()
    config.depedencies_path = Path(config.dotfiles_dir).joinpath('dotfiles.json').as_posix()
    config.legacy_meta3_path = Path(config.dotfiles_dir).joinpath('meta_3.json').as_posix()
    config.identifier = get_computer_name()
    
    # Create device data for current system
    config.device_data = DevicesData(
        identifier=config.identifier,
        home_path=config.home,
        dotfiles_dir_path=config.dotfiles_dir
    )
    
    Path(config.dotfiles_dir).mkdir(parents=True, exist_ok=True)
    logger.info(f"Created directory for {config.dotfiles_dir = }")
    Path(config.backup_dir).mkdir(parents=True, exist_ok=True)
    logger.info(f"Created directory for {config.backup_dir = }")
    logger.debug(f"{config.depedencies_path = }")
    return config

def resolve_main_path(main: str) -> str:
    """Resolve a dotfile's `main` field to an absolute filesystem path.

    Absolute `main` values (external-source registry entries -- e.g. a
    dotfile whose real copy lives in a private repo outside ~/Setup) are
    used as-is. Relative `main` values join with the current project root
    (~/Setup), matching the legacy convention where `main` is always
    `dotfiles/<something>`.

    Named explicitly rather than left as an implicit `Path(...).joinpath(...)`
    quirk: joinpath already drops the base when the joined segment is
    absolute, but that is easy to break by an innocuous refactor (e.g. to
    string concatenation), and the external-source use case deserves a
    documented contract.
    """
    if os.path.isabs(main):
        return main
    return Path(config.project_path).joinpath(main).as_posix()

set_config()
logger.debug(f"{config.identifier = }")
