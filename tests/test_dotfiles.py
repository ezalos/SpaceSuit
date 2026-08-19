import pytest
from src_dotfiles.config import set_config, config
from pathlib import Path
import shutil
import os
import time
from ezpy_logs.LoggerFactory import LoggerFactory
from src_dotfiles.__main__ import ManageDotfiles
from src_dotfiles.database import Dependencies
from src_dotfiles.models import DevicesData, DotFileModel, DeployedDotFile, MetaDataDotFiles, BackupMetadata
from src_dotfiles.DotFile import get_time, DATETIME_FORMAT, DotFile
from datetime import datetime

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    print("\nInitializing test environment (this should happen only once)...")

    set_config(dotfiles_dir="test_dotfiles")
    shutil.rmtree(config.dotfiles_dir, ignore_errors=True)
    set_config(dotfiles_dir="test_dotfiles")
    Path(config.dotfiles_dir).mkdir(parents=True, exist_ok=True)

    TEST_DATA_ORIGINAL = Path("data/original")
    TEST_DATA_TMP = Path("data/test_data")

    shutil.rmtree(TEST_DATA_TMP, ignore_errors=True)
    shutil.copytree(TEST_DATA_ORIGINAL, TEST_DATA_TMP)
    print("Test environment setup complete!")

    return {
		"TEST_DATA_ORIGINAL": TEST_DATA_ORIGINAL,
		"TEST_DATA_TMP": TEST_DATA_TMP
	}


def verify_dotfile_is_added(alias, path):
	dotfile = ManageDotfiles().db.select_by_alias(alias)
	assert Path(path).is_symlink()
	assert Path(dotfile.data.main).exists()

def verify_file_content_matches(file1: Path, file2: Path):
    """Verify that two files have the same content"""
    with open(file1) as f1, open(file2) as f2:
        assert f1.read() == f2.read()

def remove_file_if_exists(path: Path):
    """Remove a file or symlink if it exists"""
    if path.is_symlink():
        os.unlink(path)
    elif path.exists():
        path.unlink()

def get_latest_backup(dotfile) -> Path:
    """Get the path to the most recent backup of a dotfile"""
    identifier = dotfile.identifier
    assert len(dotfile.data.deploy[identifier].backups) > 0, "No backups found"
    return Path(dotfile.data.deploy[identifier].backups[-1].backup_path)

# --------------------------------- Add file --------------------------------- #

@pytest.mark.run(order=1)
def test_add_dotfile_no_alias(setup_test_environment):
	dotfile_path = f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_a"
	alias = ManageDotfiles().add(path=dotfile_path)
	verify_dotfile_is_added(alias, dotfile_path)

@pytest.mark.run(order=2)
def test_add_dotfile_with_alias(setup_test_environment):
	dotfile_path = f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_b"
	alias = ManageDotfiles().add(path=dotfile_path, alias="b_dotfile")
	assert alias == "b_dotfile"
	verify_dotfile_is_added(alias, dotfile_path)


@pytest.mark.run(order=3)
def test_add_dotfile_alias_exists_no_force(setup_test_environment):
	dotfile_path = f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_c"
	alias = ManageDotfiles().add(path=dotfile_path, alias="b_dotfile")
	assert Path(dotfile_path).exists()
	assert not Path(dotfile_path).is_symlink()
	assert alias is None


# @pytest.mark.run(order=4)
# def test_add_dotfile_alias_exists_force_different_path(setup_test_environment):
#     dotfile_path = f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_c"
#     with pytest.raises(NotImplementedError):
#         alias = ManageDotfiles().add(path=dotfile_path, alias="b_dotfile", force=True)
#         # verify_dotfile_is_added(alias, dotfile_path)



@pytest.mark.run(order=5)
def test_add_dotfile_alias_exists_force_same_path(setup_test_environment):
	dotfile_path = f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_b"
	alias = ManageDotfiles().add(path=dotfile_path, alias="b_dotfile", force=True)
	assert alias == "b_dotfile"
	verify_dotfile_is_added(alias, dotfile_path)


# -------------------------------- Deploy file ------------------------------- #

@pytest.mark.run(order=6)
def test_deploy_dotfile_with_alias_no_file_before(setup_test_environment, tmp_path):
    # Setup
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_a")
    remove_file_if_exists(dotfile_path)
    assert not dotfile_path.exists()

    # Deploy
    manager = ManageDotfiles()
    manager.deploy("test_dotfile_a", skills_dir=tmp_path / "skills-isolated")

    # Verify
    assert dotfile_path.is_symlink()
    dotfile = manager.db.select_by_alias("test_dotfile_a")
    assert dotfile is not None
    assert Path(dotfile.data.main).exists()

@pytest.mark.run(order=7)
def test_deploy_dotfile_with_alias_with_file_before(setup_test_environment, tmp_path):
    # Setup
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_b")
    remove_file_if_exists(dotfile_path)

    # Create a file with different content
    dotfile_path.write_text("Modified content for testing backup")
    assert dotfile_path.exists()
    assert not dotfile_path.is_symlink()

    # Deploy
    manager = ManageDotfiles()
    manager.deploy("b_dotfile", skills_dir=tmp_path / "skills-isolated")

    # Verify file is now a symlink
    assert dotfile_path.is_symlink()
    dotfile = manager.db.select_by_alias("b_dotfile")
    assert dotfile is not None

    # Verify backup was created and contains our modified content
    backup_path = get_latest_backup(dotfile)
    assert backup_path.exists()
    assert "Modified content for testing backup" in backup_path.read_text()

@pytest.mark.run(order=8)
def test_deploy_all_dotfiles(setup_test_environment, tmp_path):
    # Setup - remove all files
    manager = ManageDotfiles()
    for dotfile in manager.db.data:
        identifier = dotfile.identifier
        remove_file_if_exists(Path(dotfile.data.deploy[identifier].deploy_path))
        assert not Path(dotfile.data.deploy[identifier].deploy_path).exists()

    # Deploy all
    manager.deploy(skills_dir=tmp_path / "skills-isolated")

    # Verify all files are deployed
    for dotfile in manager.db.data:
        identifier = dotfile.identifier
        path = Path(dotfile.data.deploy[identifier].deploy_path)
        assert path.is_symlink()
        assert Path(dotfile.data.main).exists()

@pytest.mark.run(order=8)
def test_deploy_idempotent_skips_correct_symlink(setup_test_environment):
    """Re-deploying an already-correct symlink should be a no-op (returns False)."""
    manager = ManageDotfiles()
    dotfile = manager.db.select_by_alias("test_dotfile_a")
    assert dotfile is not None
    # first deploy from prior test landed the symlink; redeploy → idempotent
    deploy_path = Path(dotfile.data.deploy[dotfile.identifier].deploy_path)
    assert deploy_path.is_symlink()
    mtime_before = deploy_path.lstat().st_mtime
    assert dotfile.deploy() is False
    assert deploy_path.lstat().st_mtime == mtime_before  # untouched

@pytest.mark.run(order=8)
def test_deploy_escalates_to_sudo_on_permission_error(setup_test_environment, monkeypatch):
    """When os-level deploy raises PermissionError, fall back to `sudo ln -sfn`."""
    import subprocess
    from src_dotfiles import DotFile as dotfile_mod

    manager = ManageDotfiles()
    dotfile = manager.db.select_by_alias("test_dotfile_a")
    assert dotfile is not None

    # Force the as-user path to fail with PermissionError.
    def boom(self, deploy_path, target):
        raise PermissionError("simulated EACCES")
    monkeypatch.setattr(dotfile_mod.DotFile, "_deploy_as_user", boom)

    # Capture the sudo invocation instead of actually calling sudo.
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stderr="")
    monkeypatch.setattr(dotfile_mod.subprocess, "run", fake_run)

    # Need the existing symlink gone so the idempotent fast-path doesn't fire.
    deploy_path = dotfile.data.deploy[dotfile.identifier].deploy_path
    if os.path.lexists(deploy_path):
        os.unlink(deploy_path)

    assert dotfile.deploy() is True
    assert len(calls) == 1
    assert calls[0][:3] == ["sudo", "ln", "-sfn"]
    assert calls[0][-1] == deploy_path

@pytest.mark.run(order=8)
def test_deploy_all_continues_past_one_failure(setup_test_environment, monkeypatch, tmp_path):
    """All-mode loop must report failures and keep going, not abort the run."""
    from src_dotfiles import DotFile as dotfile_mod

    manager = ManageDotfiles()
    assert len(manager.db.data) >= 2

    # Sabotage exactly one dotfile's deploy.
    victim_alias = manager.db.data[0].data.alias
    original_deploy = dotfile_mod.DotFile.deploy
    def maybe_boom(self):
        if self.data.alias == victim_alias:
            raise RuntimeError("simulated failure")
        return original_deploy(self)
    monkeypatch.setattr(dotfile_mod.DotFile, "deploy", maybe_boom)

    # Should not raise — failures are caught and summarized.
    manager.deploy(skills_dir=tmp_path / "skills-isolated")

    # And the other dotfiles should be reachable / deployable afterwards.
    survivor = manager.db.data[1]
    assert os.path.lexists(survivor.data.deploy[survivor.identifier].deploy_path)

# -------------------------------- Backup tests ------------------------------- #

@pytest.mark.run(order=9)
def test_backup_preserves_content(setup_test_environment):
    """Test that a backup preserves the exact content of the original file."""
    # GIVEN a file with specific content
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_c")
    remove_file_if_exists(dotfile_path)
    test_content = "Test content for backup verification\nWith multiple lines\n123"
    dotfile_path.write_text(test_content)

    # WHEN adding it to the system
    manager = ManageDotfiles()
    alias = manager.add(str(dotfile_path), alias="c_dotfile")

    # THEN the backup should match the original content
    dotfile = manager.db.select_by_alias("c_dotfile")
    backup_path = get_latest_backup(dotfile)
    verify_file_content_matches(dotfile_path, backup_path)

@pytest.mark.run(order=10)
def test_backup_creates_unique_files(setup_test_environment, tmp_path):
    """Test that each backup creates a new file with unique content."""
    # GIVEN a dotfile in the system
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_c")
    manager = ManageDotfiles()
    dotfile = manager.db.select_by_alias("c_dotfile")
    identifier = dotfile.identifier
    initial_backup_count = len(dotfile.data.deploy[identifier].backups)

    # WHEN creating multiple versions
    test_content = "New version for unique backup test"
    remove_file_if_exists(dotfile_path)
    dotfile_path.write_text(test_content)
    manager.deploy("c_dotfile", skills_dir=tmp_path / "skills-isolated")

    # THEN a new backup should be created
    dotfile = manager.db.select_by_alias("c_dotfile")
    assert len(dotfile.data.deploy[identifier].backups) == initial_backup_count + 1
    backup_path = get_latest_backup(dotfile)
    assert backup_path.read_text() == test_content

@pytest.mark.run(order=11)
def test_backup_maintains_version_history(setup_test_environment, tmp_path):
    """Test that backups maintain the correct version history."""
    # GIVEN a dotfile and some content versions
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_c")
    contents = [
        "First version",
        "Second version\nWith a new line",
        "Third version\nWith more\nlines\n",
    ]
    manager = ManageDotfiles()
    dotfile = manager.db.select_by_alias("c_dotfile")
    identifier = dotfile.identifier
    initial_backup_count = len(dotfile.data.deploy[identifier].backups)

    # WHEN creating multiple versions
    for content in contents:
        remove_file_if_exists(dotfile_path)
        dotfile_path.write_text(content)
        manager.deploy("c_dotfile", skills_dir=tmp_path / "skills-isolated")

    # THEN all versions should be preserved in order
    dotfile = manager.db.select_by_alias("c_dotfile")
    new_backups = dotfile.data.deploy[identifier].backups[initial_backup_count:]
    assert len(new_backups) == len(contents)
    
    for backup, expected_content in zip(new_backups, contents):
        assert Path(backup.backup_path).read_text() == expected_content

@pytest.mark.run(order=12)
def test_backup_metadata_is_correct(setup_test_environment, tmp_path):
    """Test that backup metadata is correctly recorded."""
    # GIVEN a dotfile to backup
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_c")
    test_content = "Content for metadata test"
    manager = ManageDotfiles()

    t_0 = get_time()

    # WHEN creating a backup
    remove_file_if_exists(dotfile_path)
    dotfile_path.write_text(test_content)
    manager.deploy("c_dotfile", skills_dir=tmp_path / "skills-isolated")

    t_1 = get_time()
    
    # THEN the metadata should be correct
    dotfile = manager.db.select_by_alias("c_dotfile")
    identifier = dotfile.identifier
    backup = dotfile.data.deploy[identifier].backups[-1]
    assert len(backup.datetime) > 0  # Has a timestamp
    assert Path(backup.backup_path).exists()
    assert Path(backup.backup_path).read_text() == test_content
    assert datetime.strptime(t_0, DATETIME_FORMAT) <= datetime.strptime(backup.datetime, DATETIME_FORMAT) <= datetime.strptime(t_1, DATETIME_FORMAT)

# -------------------------------- Device Tests ------------------------------- #

@pytest.mark.run(order=13)
def test_device_data_is_stored(setup_test_environment):
    """Test that device data is stored in metadata."""
    # GIVEN a fresh database
    manager = ManageDotfiles()
    
    # THEN the current device should be in metadata
    assert config.identifier in manager.db.metadata.devices
    device = manager.db.metadata.devices[config.identifier]
    assert device.identifier == config.identifier
    assert device.home_path == config.home
    assert device.dotfiles_dir_path == config.dotfiles_dir

@pytest.mark.run(order=14)
def test_deploy_to_different_device(setup_test_environment):
    """Test deploying a dotfile to a different device."""
    # GIVEN a dotfile from one device
    original_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_c")
    manager = ManageDotfiles()
    dotfile = manager.db.select_by_alias("c_dotfile")
    
    # WHEN translating to a new device
    new_device = DevicesData(
        identifier="test_device",
        home_path="/home/test_user",
        dotfiles_dir_path="test_dotfiles_new"
    )
    manager.db.metadata.devices["test_device"] = new_device
    
    translated = dotfile.translate_to_device(config.device_data, new_device)

    # THEN paths should be correctly translated
    assert translated.identifier == "test_device"
    assert "test_device" in translated.data.deploy.keys()
    assert translated.data.deploy[translated.identifier].deploy_path.startswith("/home/test_user")
    assert "test_dotfiles_new" in translated.data.main
    assert len(translated.data.deploy[translated.identifier].backups) == 0  # Backups don't transfer between devices

@pytest.mark.run(order=15)
def test_load_from_different_device(setup_test_environment):
    """Test loading dotfiles from a different device."""
    # GIVEN a database with a dotfile from another device
    manager = ManageDotfiles()
    new_identifier = "other_device"
    other_device = DevicesData(
        identifier=new_identifier,
        home_path="/Users/other",
        dotfiles_dir_path="other_dotfiles"
    )
    manager.db.metadata.devices[new_identifier] = other_device
    alias="other_dotfile"
    other_dotfile = DotFile(
        data=DotFileModel(
            alias=alias,
            main=f"other_dotfiles/{alias}",
            deploy={
                new_identifier: DeployedDotFile(
                    deploy_path=f"/Users/other/{alias}",
                    backups=[]
                )
            }
        ), 
        identifier=new_identifier
    )
    manager.db.data.append(other_dotfile)
    manager.db.save_all()
    
    # WHEN loading all dotfiles
    new_manager = ManageDotfiles()
    
    # THEN the dotfile should be translated to current device
    translated = new_manager.db.select_by_alias(alias)
    assert translated is not None
    assert translated.identifier == config.identifier
    assert config.identifier in translated.data.deploy.keys()
    assert translated.data.deploy[config.identifier].deploy_path.startswith(config.home)
    assert translated.data.main.startswith(config.dotfiles_dir)
    assert len(translated.data.deploy[config.identifier].backups) == 0

# ----------------------------- Variant Model Tests ----------------------------- #

@pytest.mark.run(order=16)
def test_model_new_fields_default_none(setup_test_environment):
    """Test that new fields default to None for backward compatibility."""
    model = DotFileModel(
        alias="test_compat",
        main="dotfiles/test_compat",
        deploy={}
    )
    assert model.only_devices is None
    assert model.variants is None


@pytest.mark.run(order=17)
def test_model_new_fields_roundtrip(setup_test_environment):
    """Test that only_devices and variants survive JSON serialization."""
    model = DotFileModel(
        alias="test_variant",
        main="dotfiles/test_variant",
        deploy={},
        only_devices=["TinyButMighty.ezalos"],
        variants={"TinyButMighty.ezalos": "dotfiles/test_variant.TinyButMighty"}
    )
    json_str = model.model_dump_json()
    loaded = DotFileModel.model_validate_json(json_str)
    assert loaded.only_devices == ["TinyButMighty.ezalos"]
    assert loaded.variants == {"TinyButMighty.ezalos": "dotfiles/test_variant.TinyButMighty"}


@pytest.mark.run(order=18)
def test_metadata_version_field(setup_test_environment):
    """Test that MetaDataDotFiles has version field defaulting to 1."""
    meta = MetaDataDotFiles()
    assert meta.version == 1
    json_str = meta.model_dump_json()
    loaded = MetaDataDotFiles.model_validate_json(json_str)
    assert loaded.version == 1

# ------------------------------ Migration Tests ------------------------------ #

@pytest.mark.run(order=19)
def test_migration_from_meta3(setup_test_environment):
    """Test that meta_3.json is auto-migrated to dotfiles.json with version 1."""
    import json

    # GIVEN a meta_3.json file exists and dotfiles.json does not
    meta3_path = Path(config.dotfiles_dir) / "meta_3.json"
    dotfiles_json_path = Path(config.dotfiles_dir) / "dotfiles.json"

    # Remove dotfiles.json if it exists from previous test runs
    if dotfiles_json_path.exists():
        dotfiles_json_path.unlink()

    # Write a minimal meta_3.json (old format, no version field)
    old_data = {
        "dotfiles": {
            "migration_test": {
                "alias": "migration_test",
                "main": "test_dotfiles/migration_test",
                "deploy": {}
            }
        },
        "devices": {}
    }
    meta3_path.write_text(json.dumps(old_data))

    # WHEN loading the database
    manager = ManageDotfiles()

    # THEN dotfiles.json should exist with version=1
    assert dotfiles_json_path.exists()
    with open(dotfiles_json_path) as f:
        loaded = json.loads(f.read())
    assert loaded["version"] == 1
    assert "migration_test" in loaded["dotfiles"]

    # Clean up: remove the meta_3.json we created, restore dotfiles.json as primary
    if meta3_path.exists():
        meta3_path.unlink()

# -------------------------- Only Devices Tests -------------------------- #

@pytest.mark.run(order=20)
def test_only_devices_skip(setup_test_environment):
    """Dotfile with only_devices excluding current device is skipped."""
    import json

    # GIVEN a dotfile restricted to a different device
    manager = ManageDotfiles()
    manager.db.metadata.dotfiles["restricted_file"] = DotFileModel(
        alias="restricted_file",
        main="test_dotfiles/restricted_file",
        deploy={
            "other_device.user": DeployedDotFile(
                deploy_path="/home/other/restricted_file",
                backups=[]
            )
        },
        only_devices=["other_device.user"]
    )
    manager.db.metadata.devices["other_device.user"] = DevicesData(
        identifier="other_device.user",
        home_path="/home/other",
        dotfiles_dir_path="test_dotfiles"
    )
    manager.db.save_all()

    # WHEN loading all dotfiles on current device
    new_manager = ManageDotfiles()

    # THEN the restricted dotfile should not appear
    aliases = [d.data.alias for d in new_manager.db.data]
    assert "restricted_file" not in aliases

    # Clean up
    del new_manager.db.metadata.dotfiles["restricted_file"]
    new_manager.db.save_all()


@pytest.mark.run(order=21)
def test_only_devices_deploy(setup_test_environment):
    """Dotfile with only_devices including current device deploys normally."""
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_dotfile_restricted")
    dotfile_path.write_text("restricted content")

    # GIVEN a dotfile restricted to include the current device
    manager = ManageDotfiles()
    manager.db.metadata.dotfiles["restricted_current"] = DotFileModel(
        alias="restricted_current",
        main="test_dotfiles/restricted_current",
        deploy={
            config.identifier: DeployedDotFile(
                deploy_path=str(dotfile_path),
                backups=[]
            )
        },
        only_devices=[config.identifier]
    )
    # Create the main file so deploy can symlink to it
    main_path = Path(config.project_path) / "test_dotfiles" / "restricted_current"
    main_path.write_text("restricted main content")
    manager.db.save_all()

    # WHEN loading all dotfiles
    new_manager = ManageDotfiles()

    # THEN the dotfile should appear
    aliases = [d.data.alias for d in new_manager.db.data]
    assert "restricted_current" in aliases

    # Clean up
    del new_manager.db.metadata.dotfiles["restricted_current"]
    new_manager.db.save_all()
    if main_path.exists():
        main_path.unlink()
    if dotfile_path.is_symlink():
        os.unlink(str(dotfile_path))
    elif dotfile_path.exists():
        dotfile_path.unlink()

# ----------------------------- Variant Deploy Tests ----------------------------- #

@pytest.mark.run(order=22)
def test_variant_deploy(setup_test_environment):
    """Variant for current device is used as symlink target instead of main."""
    # GIVEN a dotfile with a variant for the current device
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_variant_file")
    dotfile_path.write_text("will be replaced by symlink")

    main_path = Path(config.project_path) / "test_dotfiles" / "variant_test"
    main_path.write_text("default content")

    variant_path = Path(config.project_path) / "test_dotfiles" / "variant_test.MyDevice"
    variant_path.write_text("variant content")

    model = DotFileModel(
        alias="variant_test",
        main="test_dotfiles/variant_test",
        deploy={
            config.identifier: DeployedDotFile(
                deploy_path=str(dotfile_path),
                backups=[]
            )
        },
        variants={config.identifier: "test_dotfiles/variant_test.MyDevice"}
    )
    dotfile = DotFile(model, config.identifier)

    # WHEN deploying
    remove_file_if_exists(dotfile_path)
    dotfile.deploy()

    # THEN the symlink should point to the variant, not main
    assert dotfile_path.is_symlink()
    target = os.readlink(str(dotfile_path))
    assert target.endswith("variant_test.MyDevice")
    assert dotfile_path.read_text() == "variant content"

    # Clean up
    remove_file_if_exists(dotfile_path)
    remove_file_if_exists(main_path)
    remove_file_if_exists(variant_path)


@pytest.mark.run(order=23)
def test_variant_fallback(setup_test_environment):
    """When variant exists for another device, current device gets main."""
    # GIVEN a dotfile with a variant for a DIFFERENT device
    dotfile_path = Path(f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_variant_fallback")
    dotfile_path.write_text("will be replaced by symlink")

    main_path = Path(config.project_path) / "test_dotfiles" / "variant_fallback"
    main_path.write_text("default content")

    variant_path = Path(config.project_path) / "test_dotfiles" / "variant_fallback.OtherDevice"
    variant_path.write_text("other device content")

    model = DotFileModel(
        alias="variant_fallback",
        main="test_dotfiles/variant_fallback",
        deploy={
            config.identifier: DeployedDotFile(
                deploy_path=str(dotfile_path),
                backups=[]
            )
        },
        variants={"OtherDevice.user": "test_dotfiles/variant_fallback.OtherDevice"}
    )
    dotfile = DotFile(model, config.identifier)

    # WHEN deploying
    remove_file_if_exists(dotfile_path)
    dotfile.deploy()

    # THEN the symlink should point to main (not the variant)
    assert dotfile_path.is_symlink()
    target = os.readlink(str(dotfile_path))
    assert target.endswith("variant_fallback")
    assert not target.endswith("variant_fallback.OtherDevice")
    assert dotfile_path.read_text() == "default content"

    # Clean up
    remove_file_if_exists(dotfile_path)
    remove_file_if_exists(main_path)
    remove_file_if_exists(variant_path)


@pytest.mark.run(order=24)
def test_translate_preserves_only_devices_and_variants(setup_test_environment):
    """Test that translate_to_device preserves only_devices and variants."""
    model = DotFileModel(
        alias="translate_test",
        main="test_dotfiles/translate_test",
        deploy={
            config.identifier: DeployedDotFile(
                deploy_path=f"{config.home}/.translate_test",
                backups=[]
            )
        },
        only_devices=[config.identifier, "target_device"],
        variants={config.identifier: "test_dotfiles/translate_test.current"}
    )
    dotfile = DotFile(model, config.identifier)

    target_device = DevicesData(
        identifier="target_device",
        home_path="/home/target",
        dotfiles_dir_path="test_dotfiles_target"
    )

    translated = dotfile.translate_to_device(config.device_data, target_device)
    assert translated.data.only_devices == [config.identifier, "target_device"]
    assert translated.data.variants == {config.identifier: "test_dotfiles/translate_test.current"}


# ----------------------------- CLI Add --only-device Tests ----------------------------- #

@pytest.mark.run(order=25)
def test_add_with_only_device(setup_test_environment):
    """Test that --only-device flag sets only_devices on the dotfile."""
    dotfile_path = f"{setup_test_environment['TEST_DATA_TMP'].as_posix()}/test_only_device_add"
    Path(dotfile_path).write_text("only device content")

    # Create the main file first (simulating it already exists)
    main_path = Path(config.project_path) / "test_dotfiles" / "only_device_add"
    main_path.write_text("only device content")

    manager = ManageDotfiles()
    alias = manager.add(path=dotfile_path, alias="only_device_add", only_device=config.identifier)
    assert alias == "only_device_add"

    # Verify only_devices is set
    new_manager = ManageDotfiles()
    dotfile = new_manager.db.select_by_alias("only_device_add")
    assert dotfile is not None
    assert dotfile.data.only_devices == [config.identifier]

    # Clean up
    if Path(dotfile_path).is_symlink():
        os.unlink(dotfile_path)
    elif Path(dotfile_path).exists():
        Path(dotfile_path).unlink()
    if main_path.exists():
        main_path.unlink()


# ----------------------- Real meta_3.json Migration Tests ----------------------- #

@pytest.mark.run(order=26)
def test_migration_from_real_meta3(setup_test_environment):
    """Test migration using the real dotfiles/meta_3.json with 18 dotfiles and 12 devices."""
    import json

    # GIVEN a copy of the real meta_3.json in the test dotfiles directory
    real_meta3 = Path("dotfiles/meta_3.json")
    assert real_meta3.exists(), "Real meta_3.json must exist for this test"

    test_meta3_path = Path(config.dotfiles_dir) / "meta_3.json"
    test_dotfiles_json = Path(config.dotfiles_dir) / "dotfiles.json"

    # Remove dotfiles.json so the migration path is triggered
    if test_dotfiles_json.exists():
        test_dotfiles_json.unlink()

    shutil.copy(real_meta3, test_meta3_path)

    # WHEN loading the database (triggers migration from meta_3.json)
    manager = ManageDotfiles()

    # THEN dotfiles.json should be created with version 1
    assert test_dotfiles_json.exists()
    with open(test_dotfiles_json) as f:
        loaded = json.loads(f.read())
    assert loaded["version"] == 1

    # All 18 dotfiles from meta_3.json should be present in the migrated data
    assert len(loaded["dotfiles"]) >= 18

    # All 12 original devices should be preserved
    original_device_ids = {
        "ezalos.TM1704.ezalos", "Louiss.MBP.ezalos", "TheBeast.ezalos",
        "Louiss.MBP.lan.ezalos", "MacBook.Pro.de.Louis.local.louis",
        "device3", "louiss.macbook.pro.3.home.ezalos", "device2",
        "device5", "device1", "device4", "TinyButMighty.ezalos",
    }
    for device_id in original_device_ids:
        assert device_id in loaded["devices"], f"Device {device_id} missing after migration"

    # All dotfiles should have loaded successfully (translated to current device)
    assert len(manager.db.data) == 18

    # only_devices and variants should default to None (didn't exist in old format)
    for alias, model in manager.db.metadata.dotfiles.items():
        assert model.only_devices is None, f"{alias} has unexpected only_devices"
        assert model.variants is None, f"{alias} has unexpected variants"

    # Clean up
    if test_meta3_path.exists():
        test_meta3_path.unlink()


@pytest.mark.run(order=27)
def test_unknown_device_inferred(setup_test_environment):
    """Test that a dotfile from an unknown device is recovered via path inference."""
    import json

    # GIVEN a database with a dotfile deployed to an unknown device
    # whose deploy path has a recognizable home directory
    manager = ManageDotfiles()
    unknown_device_id = "mystery_box.someuser"
    alias = "inferred_dotfile"

    manager.db.metadata.dotfiles[alias] = DotFileModel(
        alias=alias,
        main="dotfiles/inferred_dotfile",
        deploy={
            unknown_device_id: DeployedDotFile(
                deploy_path="/home/someuser/.config/inferred_dotfile",
                backups=[]
            )
        }
    )
    # Ensure the unknown device is NOT in metadata.devices
    if unknown_device_id in manager.db.metadata.devices:
        del manager.db.metadata.devices[unknown_device_id]
    manager.db.save_all()

    # WHEN loading all dotfiles
    new_manager = ManageDotfiles()

    # THEN the dotfile should NOT be silently skipped
    aliases = [d.data.alias for d in new_manager.db.data]
    assert alias in aliases, "Dotfile with unknown device should be recovered via inference"

    # The inferred device should now be in metadata.devices
    assert unknown_device_id in new_manager.db.metadata.devices
    inferred_device = new_manager.db.metadata.devices[unknown_device_id]
    assert inferred_device.home_path == "/home/someuser"

    # The dotfile should be translated to the current device
    translated = new_manager.db.select_by_alias(alias)
    assert translated is not None
    assert translated.identifier == config.identifier
    assert config.identifier in translated.data.deploy
    assert translated.data.deploy[config.identifier].deploy_path.startswith(config.home)

    # Clean up
    del new_manager.db.metadata.dotfiles[alias]
    if unknown_device_id in new_manager.db.metadata.devices:
        del new_manager.db.metadata.devices[unknown_device_id]
    new_manager.db.save_all()


@pytest.mark.run(order=28)
def test_unknown_device_unrecognizable_path(setup_test_environment):
    """Test that a dotfile with an unrecognizable deploy path is skipped gracefully."""
    import json

    # GIVEN a dotfile deployed to an unknown device with a non-home path
    manager = ManageDotfiles()
    unknown_device_id = "weird_server.nobody"
    alias = "unskippable_dotfile"

    manager.db.metadata.dotfiles[alias] = DotFileModel(
        alias=alias,
        main="dotfiles/unskippable_dotfile",
        deploy={
            unknown_device_id: DeployedDotFile(
                deploy_path="/etc/nginx/nginx.conf",
                backups=[]
            )
        }
    )
    # Ensure the unknown device is NOT in metadata.devices
    if unknown_device_id in manager.db.metadata.devices:
        del manager.db.metadata.devices[unknown_device_id]
    manager.db.save_all()

    # WHEN loading all dotfiles
    new_manager = ManageDotfiles()

    # THEN the dotfile should be skipped (no crash, graceful degradation)
    aliases = [d.data.alias for d in new_manager.db.data]
    assert alias not in aliases, "Dotfile with unrecognizable path should be skipped"

    # The unknown device should NOT be added to metadata.devices
    assert unknown_device_id not in new_manager.db.metadata.devices

    # Clean up
    del new_manager.db.metadata.dotfiles[alias]
    new_manager.db.save_all()


@pytest.mark.run(order=29)
def test_model_fanout_defaults_false(setup_test_environment):
    """fanout defaults to False for backward compatibility."""
    model = DotFileModel(alias="t_fanout", main="dotfiles/t_fanout", deploy={})
    assert model.fanout is False

@pytest.mark.run(order=30)
def test_model_fanout_roundtrip(setup_test_environment):
    """fanout survives JSON serialization."""
    model = DotFileModel(alias="t_fanout2", main="skills", deploy={}, fanout=True)
    loaded = DotFileModel.model_validate_json(model.model_dump_json())
    assert loaded.fanout is True

# ------------------------------- Fan-out Tests ------------------------------- #

def _make_fanout_dotfile(src_rel, container_abs):
    """Build a fanout DotFile: source dir `src_rel` (under project_path),
    deploy container at absolute `container_abs`."""
    model = DotFileModel(
        alias="fanout_skills",
        main=src_rel,
        deploy={config.identifier: DeployedDotFile(deploy_path=str(container_abs), backups=[])},
        fanout=True,
    )
    return DotFile(model, config.identifier)

@pytest.mark.run(order=31)
def test_fanout_links_child_dirs_skips_files(setup_test_environment):
    """Fan-out symlinks each child dir; ignores files and dot-dirs."""
    src = Path(config.project_path) / "test_dotfiles" / "skills_src"
    shutil.rmtree(src, ignore_errors=True)
    (src / "skillA").mkdir(parents=True)
    (src / "skillA" / "SKILL.md").write_text("A")
    (src / "skillB").mkdir(parents=True)
    (src / "skillB" / "SKILL.md").write_text("B")
    (src / "EXTERNAL.md").write_text("manifest")     # file → skipped
    (src / ".git").mkdir()                            # dot-dir → skipped

    container = Path(config.project_path) / "test_dotfiles" / "skills_deploy"
    shutil.rmtree(container, ignore_errors=True)

    dotfile = _make_fanout_dotfile("test_dotfiles/skills_src", container)
    assert dotfile.deploy() is True

    assert (container / "skillA").is_symlink()
    assert (container / "skillB").is_symlink()
    assert os.readlink(str(container / "skillA")) == str(src / "skillA")
    assert not (container / "EXTERNAL.md").exists()
    assert not (container / ".git").exists()

@pytest.mark.run(order=32)
def test_fanout_idempotent(setup_test_environment):
    """Second fan-out deploy is a no-op (returns False)."""
    src = Path(config.project_path) / "test_dotfiles" / "skills_src_idem"
    shutil.rmtree(src, ignore_errors=True)
    (src / "skillA").mkdir(parents=True)
    (src / "skillA" / "SKILL.md").write_text("A")
    container = Path(config.project_path) / "test_dotfiles" / "skills_deploy_idem"
    shutil.rmtree(container, ignore_errors=True)
    dotfile = _make_fanout_dotfile("test_dotfiles/skills_src_idem", container)
    assert dotfile.deploy() is True       # first deploy creates the links
    assert dotfile.deploy() is False      # second is a no-op

@pytest.mark.run(order=33)
def test_fanout_backs_up_real_dir_before_replacing(setup_test_environment):
    """A pre-existing real dir at a child path is backed up, then replaced by a symlink."""
    src = Path(config.project_path) / "test_dotfiles" / "skills_src2"
    shutil.rmtree(src, ignore_errors=True)
    (src / "skillC").mkdir(parents=True)
    (src / "skillC" / "SKILL.md").write_text("new")

    container = Path(config.project_path) / "test_dotfiles" / "skills_deploy2"
    shutil.rmtree(container, ignore_errors=True)
    (container / "skillC").mkdir(parents=True)             # pre-existing real dir
    (container / "skillC" / "old.txt").write_text("OLD-CONTENT")

    dotfile = _make_fanout_dotfile("test_dotfiles/skills_src2", container)
    assert dotfile.deploy() is True

    assert (container / "skillC").is_symlink()
    backups = dotfile.data.deploy[config.identifier].backups
    assert len(backups) == 1
    assert (Path(backups[-1].backup_path) / "old.txt").read_text() == "OLD-CONTENT"

@pytest.mark.run(order=34)
def test_backup_is_noop_for_fanout_entry(setup_test_environment):
    """backup() must not copy the whole container for a fanout entry."""
    container = Path(config.project_path) / "test_dotfiles" / "skills_deploy_noop"
    shutil.rmtree(container, ignore_errors=True)
    container.mkdir(parents=True)
    (container / "junk").write_text("x")
    dotfile = _make_fanout_dotfile("test_dotfiles/skills_src", container)
    dotfile.backup()
    assert len(dotfile.data.deploy[config.identifier].backups) == 0

# ------------------------- register --fanout / deregister ------------------------- #

@pytest.mark.run(order=35)
def test_register_fanout_creates_child_symlinks(setup_test_environment):
    """register --fanout creates one entry that deploys each child as a symlink."""
    src = Path(config.project_path) / "test_dotfiles" / "reg_skills"
    shutil.rmtree(src, ignore_errors=True)
    (src / "alpha").mkdir(parents=True)
    (src / "alpha" / "SKILL.md").write_text("alpha")

    container = Path(config.project_path) / "test_dotfiles" / "reg_deploy"
    shutil.rmtree(container, ignore_errors=True)

    manager = ManageDotfiles()
    alias = manager.register(
        alias="reg_skills_entry",
        deploy_path=str(container),
        main="test_dotfiles/reg_skills",
        only_device=config.identifier,
        fanout=True,
    )
    assert alias == "reg_skills_entry"
    assert (container / "alpha").is_symlink()

    reloaded = ManageDotfiles().db.metadata.dotfiles["reg_skills_entry"]
    assert reloaded.fanout is True
    assert reloaded.only_devices == [config.identifier]

@pytest.mark.run(order=36)
def test_deregister_removes_entry_and_symlink(setup_test_environment):
    """deregister drops the entry and unlinks a repo-pointing symlink; survives reload."""
    src = Path(config.project_path) / "test_dotfiles" / "dereg_src"
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("x")
    link = Path(config.project_path) / "test_dotfiles" / "dereg_link"
    if link.is_symlink() or link.exists():
        remove_file_if_exists(link)

    manager = ManageDotfiles()
    manager.register(
        alias="dereg_entry",
        deploy_path=str(link),
        main="test_dotfiles/dereg_src",
        only_device=config.identifier,
    )
    assert link.is_symlink()

    assert manager.deregister("dereg_entry") == "dereg_entry"
    assert not link.exists() and not link.is_symlink()   # symlink removed

    reloaded = ManageDotfiles()
    assert "dereg_entry" not in reloaded.db.metadata.dotfiles     # not resurrected
    assert "dereg_entry" not in [d.data.alias for d in reloaded.db.data]

@pytest.mark.run(order=37)
def test_deregister_missing_alias_returns_none(setup_test_environment):
    assert ManageDotfiles().deregister("nope_not_here") is None

# --------------------- deregister hardening / coverage --------------------- #

@pytest.mark.run(order=38)
def test_deregister_leaves_sibling_prefix_symlink_untouched(setup_test_environment):
    """A symlink whose target shares the repo path as a string prefix but is a
    SIBLING dir (repo + '-...') must be treated as outside the repo and left alone."""
    sibling = Path(str(config.project_path).rstrip('/') + "-dereg-sibling-testonly")
    shutil.rmtree(sibling, ignore_errors=True)
    sibling.mkdir(parents=True)
    link = Path(config.project_path) / "test_dotfiles" / "dereg_sibling_link"
    remove_file_if_exists(link)
    os.symlink(str(sibling), str(link))

    manager = ManageDotfiles()
    manager.db.metadata.dotfiles["dereg_sibling_entry"] = DotFileModel(
        alias="dereg_sibling_entry", main="test_dotfiles/na",
        deploy={config.identifier: DeployedDotFile(deploy_path=str(link), backups=[])},
        only_devices=[config.identifier])
    manager.db.save_all()

    assert ManageDotfiles().deregister("dereg_sibling_entry") == "dereg_sibling_entry"
    assert link.is_symlink(), "sibling-prefix symlink must be left untouched"

    remove_file_if_exists(link)
    shutil.rmtree(sibling, ignore_errors=True)

@pytest.mark.run(order=39)
def test_deregister_leaves_non_symlink_untouched(setup_test_environment):
    """deregister leaves a real (non-symlink) file at deploy_path in place."""
    real = Path(config.project_path) / "test_dotfiles" / "dereg_real_file"
    remove_file_if_exists(real)
    real.write_text("REAL-CONTENT")

    manager = ManageDotfiles()
    manager.db.metadata.dotfiles["dereg_real_entry"] = DotFileModel(
        alias="dereg_real_entry", main="test_dotfiles/na",
        deploy={config.identifier: DeployedDotFile(deploy_path=str(real), backups=[])},
        only_devices=[config.identifier])
    manager.db.save_all()

    assert ManageDotfiles().deregister("dereg_real_entry") == "dereg_real_entry"
    assert real.exists() and not real.is_symlink()
    assert real.read_text() == "REAL-CONTENT"
    remove_file_if_exists(real)

@pytest.mark.run(order=40)
def test_register_force_update_sets_fanout(setup_test_environment):
    """register --force on an existing entry updates its fanout flag to True."""
    src = Path(config.project_path) / "test_dotfiles" / "fu_src"
    shutil.rmtree(src, ignore_errors=True)
    (src / "alpha").mkdir(parents=True)
    (src / "alpha" / "SKILL.md").write_text("a")
    c1 = Path(config.project_path) / "test_dotfiles" / "fu_deploy1"
    c2 = Path(config.project_path) / "test_dotfiles" / "fu_deploy2"
    shutil.rmtree(c1, ignore_errors=True); shutil.rmtree(c2, ignore_errors=True)

    manager = ManageDotfiles()
    manager.register(alias="fu_entry", deploy_path=str(c1), main="test_dotfiles/fu_src",
                     only_device=config.identifier, fanout=False)
    assert ManageDotfiles().db.metadata.dotfiles["fu_entry"].fanout is False

    manager.register(alias="fu_entry", deploy_path=str(c2), main="test_dotfiles/fu_src",
                     only_device=config.identifier, fanout=True, force=True)
    assert ManageDotfiles().db.metadata.dotfiles["fu_entry"].fanout is True

# --------------------- absolute main path (external-source registry) --------------------- #

@pytest.mark.run(order=41)
def test_register_with_absolute_main_deploys_correctly(setup_test_environment, tmp_path):
    """register() with an absolute `main` (living outside ~/Setup, e.g. a private
    repo) must deploy a symlink pointing straight at that absolute path -- not at
    config.project_path joined with it."""
    external_dir = tmp_path / "external-repo" / "dotfiles"
    external_dir.mkdir(parents=True)
    external_main = external_dir / "abs_main_dotfile"
    external_main.write_text("absolute main content")

    deploy_target = Path(config.project_path) / "test_dotfiles" / "abs_main_deploy_target"
    remove_file_if_exists(deploy_target)

    manager = ManageDotfiles()
    alias = manager.register(
        alias="abs_main_entry",
        deploy_path=str(deploy_target),
        main=str(external_main),
        only_device=config.identifier,
    )
    assert alias == "abs_main_entry"
    assert deploy_target.is_symlink()
    assert os.readlink(deploy_target) == str(external_main)
    verify_file_content_matches(deploy_target, external_main)

    reloaded = ManageDotfiles().db.metadata.dotfiles["abs_main_entry"]
    assert reloaded.main == str(external_main)

@pytest.mark.run(order=42)
def test_set_main_repoints_registry_and_symlink(setup_test_environment, tmp_path):
    """set-main updates a dotfile's `main` field and redeploys the symlink to match."""
    old_dir = tmp_path / "old-main"
    old_dir.mkdir()
    old_main = old_dir / "set_main_dotfile"
    old_main.write_text("old content")

    deploy_target = Path(config.project_path) / "test_dotfiles" / "set_main_deploy_target"
    remove_file_if_exists(deploy_target)

    manager = ManageDotfiles()
    manager.register(
        alias="set_main_entry",
        deploy_path=str(deploy_target),
        main=str(old_main),
        only_device=config.identifier,
    )
    assert os.readlink(deploy_target) == str(old_main)

    new_dir = tmp_path / "new-main"
    new_dir.mkdir()
    new_main = new_dir / "set_main_dotfile"
    new_main.write_text("new content")

    result = manager.set_main("set_main_entry", str(new_main))
    assert result == "set_main_entry"
    assert os.readlink(deploy_target) == str(new_main)
    verify_file_content_matches(deploy_target, new_main)

    reloaded = ManageDotfiles().db.metadata.dotfiles["set_main_entry"]
    assert reloaded.main == str(new_main)

@pytest.mark.run(order=43)
def test_set_main_missing_alias_returns_none(setup_test_environment):
    assert ManageDotfiles().set_main("nope_not_here", "/tmp/whatever-set-main-testonly") is None

@pytest.mark.run(order=44)
def test_set_main_missing_source_returns_none(setup_test_environment, tmp_path):
    """set-main refuses to repoint to a source that does not exist, and leaves main unchanged."""
    src = tmp_path / "sm_missing_src"
    src.write_text("x")
    deploy_target = Path(config.project_path) / "test_dotfiles" / "sm_missing_deploy"
    remove_file_if_exists(deploy_target)

    manager = ManageDotfiles()
    manager.register(alias="sm_missing_entry", deploy_path=str(deploy_target), main=str(src),
                      only_device=config.identifier)

    result = manager.set_main("sm_missing_entry", str(tmp_path / "does-not-exist-testonly"))
    assert result is None

    reloaded = ManageDotfiles().db.metadata.dotfiles["sm_missing_entry"]
    assert reloaded.main == str(src)

@pytest.mark.run(order=45)
def test_set_main_with_device_repoints_variant(setup_test_environment, tmp_path):
    """set-main(device=...) repoints a per-device variant path instead of `main`."""
    base_dir = tmp_path / "variant-base"
    base_dir.mkdir()
    base_main = base_dir / "variant_dotfile"
    base_main.write_text("base content")

    old_variant_dir = tmp_path / "variant-old"
    old_variant_dir.mkdir()
    old_variant = old_variant_dir / "variant_dotfile_variant"
    old_variant.write_text("old variant content")

    deploy_target = Path(config.project_path) / "test_dotfiles" / "variant_deploy_target"
    remove_file_if_exists(deploy_target)

    manager = ManageDotfiles()
    model = DotFileModel(
        alias="variant_set_main_entry",
        main=str(base_main),
        deploy={config.identifier: DeployedDotFile(deploy_path=str(deploy_target), backups=[])},
        only_devices=[config.identifier],
        variants={config.identifier: str(old_variant)},
    )
    manager.db.metadata.dotfiles["variant_set_main_entry"] = model
    manager.db.data.append(DotFile(model, config.identifier))
    manager.db.save_all()
    manager.deploy("variant_set_main_entry", skills_dir=tmp_path / "skills-isolated")
    assert os.readlink(deploy_target) == str(old_variant)

    new_variant_dir = tmp_path / "variant-new"
    new_variant_dir.mkdir()
    new_variant = new_variant_dir / "variant_dotfile_variant"
    new_variant.write_text("new variant content")

    result = manager.set_main("variant_set_main_entry", str(new_variant), device=config.identifier)
    assert result == "variant_set_main_entry"
    assert os.readlink(deploy_target) == str(new_variant)
    verify_file_content_matches(deploy_target, new_variant)

    reloaded = ManageDotfiles().db.metadata.dotfiles["variant_set_main_entry"]
    assert reloaded.variants[config.identifier] == str(new_variant)
    assert reloaded.main == str(base_main)  # unchanged

@pytest.mark.run(order=46)
def test_set_main_alias_not_deployed_on_current_device(setup_test_environment, tmp_path):
    """set-main on an alias whose only_devices excludes the current device (e.g. a
    per-host config like nginx.conf that only deploys on a different box) must
    update the registry without crashing -- there is no local deploy to attempt."""
    src = tmp_path / "other_device_main"
    src.write_text("x")
    other_device = "OtherDevice.tester"

    manager = ManageDotfiles()
    model = DotFileModel(
        alias="other_device_entry",
        main=str(src),
        deploy={other_device: DeployedDotFile(deploy_path="/some/other/path", backups=[])},
        only_devices=[other_device],
    )
    manager.db.metadata.dotfiles["other_device_entry"] = model
    manager.db.save_all()

    new_src = tmp_path / "other_device_new_main"
    new_src.write_text("y")

    result = manager.set_main("other_device_entry", str(new_src))
    assert result == "other_device_entry"

    reloaded = ManageDotfiles().db.metadata.dotfiles["other_device_entry"]
    assert reloaded.main == str(new_src)

# --------------------- registry migration primitives (rename / scrub) --------------------- #
#
# Deliberately generic and vocab-free: these back a one-time anonymization
# migration, but neither the code nor its tests hardcode the real forbidden
# token -- the actual old/new strings for that migration are passed as CLI
# arguments at run time, never committed to source.

@pytest.mark.run(order=47)
def test_rename_device_updates_devices_deploy_only_devices_variants(setup_test_environment):
    """rename-device renames a device identifier everywhere it appears: the
    devices map, every dotfile's deploy dict, only_devices list, and variants dict."""
    old_id = "rn_migrate_testonly.oldsuffix"
    new_id = "rn_migrate_testonly"

    manager = ManageDotfiles()
    manager.db.metadata.devices[old_id] = DevicesData(
        identifier=old_id, home_path="/home/oldsuffix", dotfiles_dir_path="dotfiles"
    )
    model = DotFileModel(
        alias="rn_migrate_alias",
        main="test_dotfiles/rn_migrate_main",
        deploy={old_id: DeployedDotFile(deploy_path="/home/oldsuffix/.rcfile", backups=[])},
        only_devices=[old_id],
        variants={old_id: "test_dotfiles/rn_migrate_variant"},
    )
    manager.db.metadata.dotfiles["rn_migrate_alias"] = model
    manager.db.save_all()

    result = manager.rename_device(old_id, new_id)
    assert result == new_id

    reloaded = ManageDotfiles()
    assert old_id not in reloaded.db.metadata.devices
    assert new_id in reloaded.db.metadata.devices
    assert reloaded.db.metadata.devices[new_id].identifier == new_id

    m = reloaded.db.metadata.dotfiles["rn_migrate_alias"]
    assert old_id not in m.deploy and new_id in m.deploy
    assert m.deploy[new_id].deploy_path == "/home/oldsuffix/.rcfile"
    assert m.only_devices == [new_id]
    assert old_id not in m.variants and new_id in m.variants

@pytest.mark.run(order=48)
def test_rename_device_missing_identifier_returns_none(setup_test_environment):
    assert ManageDotfiles().rename_device("nope_not_here.oldsuffix", "nope_not_here") is None

@pytest.mark.run(order=49)
def test_rename_device_collision_refuses_without_force(setup_test_environment):
    manager = ManageDotfiles()
    manager.db.metadata.devices["rn_collide_old.oldsuffix"] = DevicesData(
        identifier="rn_collide_old.oldsuffix", home_path="/home/x", dotfiles_dir_path="dotfiles")
    manager.db.metadata.devices["rn_collide_new"] = DevicesData(
        identifier="rn_collide_new", home_path="/home/y", dotfiles_dir_path="dotfiles")
    manager.db.save_all()

    assert manager.rename_device("rn_collide_old.oldsuffix", "rn_collide_new") is None
    reloaded = ManageDotfiles()
    assert "rn_collide_old.oldsuffix" in reloaded.db.metadata.devices  # untouched

@pytest.mark.run(order=50)
def test_scrub_path_substring_updates_home_deploy_backup_paths(setup_test_environment):
    """scrub-path-substring replaces a literal substring in home_path, deploy_path,
    and backup_path strings across the registry -- never touches dict keys/identifiers."""
    dev_id = "scrub_migrate_testonly"
    manager = ManageDotfiles()
    manager.db.metadata.devices[dev_id] = DevicesData(
        identifier=dev_id, home_path="/home/oldsub", dotfiles_dir_path="dotfiles"
    )
    model = DotFileModel(
        alias="scrub_migrate_alias",
        main="test_dotfiles/scrub_migrate_main",
        deploy={dev_id: DeployedDotFile(
            deploy_path="/home/oldsub/.rcfile",
            backups=[BackupMetadata(backup_path="/home/oldsub/Setup/dotfiles/old/x_oldsub_1",
                                     datetime="2026-01-01_00:00:00.000000")],
        )},
    )
    manager.db.metadata.dotfiles["scrub_migrate_alias"] = model
    manager.db.save_all()

    changed = manager.scrub_path_substring("oldsub", "newsub")
    assert changed >= 3  # home_path + deploy_path + backup_path

    reloaded = ManageDotfiles()
    assert reloaded.db.metadata.devices[dev_id].home_path == "/home/newsub"
    m = reloaded.db.metadata.dotfiles["scrub_migrate_alias"]
    assert m.deploy[dev_id].deploy_path == "/home/newsub/.rcfile"
    assert m.deploy[dev_id].backups[0].backup_path == "/home/newsub/Setup/dotfiles/old/x_newsub_1"
    # the device identifier itself (a dict key) must be untouched by a path-substring scrub
    assert dev_id in reloaded.db.metadata.devices

@pytest.mark.run(order=51)
def test_set_only_devices_gates_global_entry(setup_test_environment):
    """set-only-devices restricts a currently-global (only_devices=None) entry
    to an explicit device list in one call -- the shape used to gate
    claude_md/claude_settings to Louis's devices."""
    manager = ManageDotfiles()
    model = DotFileModel(
        alias="sod_gate_alias",
        main="test_dotfiles/sod_gate_main",
        deploy={
            "sod_dev_a": DeployedDotFile(deploy_path="/home/a/.rcfile", backups=[]),
            "sod_dev_b": DeployedDotFile(deploy_path="/home/b/.rcfile", backups=[]),
        },
        only_devices=None,
    )
    manager.db.metadata.dotfiles["sod_gate_alias"] = model
    manager.db.save_all()

    result = manager.set_only_devices("sod_gate_alias", "sod_dev_a,sod_dev_b")
    assert result == "sod_gate_alias"

    reloaded = ManageDotfiles()
    m = reloaded.db.metadata.dotfiles["sod_gate_alias"]
    assert m.only_devices == ["sod_dev_a", "sod_dev_b"]

@pytest.mark.run(order=52)
def test_set_only_devices_replaces_existing_list(setup_test_environment):
    """A second call replaces the whole list (not append -- that's extend_to's job)."""
    manager = ManageDotfiles()
    model = DotFileModel(
        alias="sod_replace_alias",
        main="test_dotfiles/sod_replace_main",
        deploy={"sod_dev_c": DeployedDotFile(deploy_path="/home/c/.rcfile", backups=[])},
        only_devices=["sod_dev_old"],
    )
    manager.db.metadata.dotfiles["sod_replace_alias"] = model
    manager.db.save_all()

    result = manager.set_only_devices("sod_replace_alias", "sod_dev_c")
    assert result == "sod_replace_alias"

    reloaded = ManageDotfiles()
    assert reloaded.db.metadata.dotfiles["sod_replace_alias"].only_devices == ["sod_dev_c"]

@pytest.mark.run(order=53)
def test_set_only_devices_missing_alias_returns_none(setup_test_environment):
    assert ManageDotfiles().set_only_devices("sod_nope_not_here", "sod_dev_a") is None

# --------------------- deploy-time skills integrity check --------------------- #

def test_check_skills_integrity_flags_dangling(tmp_path):
    from pathlib import Path
    from src_dotfiles.__main__ import _check_skills_integrity

    skills = tmp_path / "skills"
    skills.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    (skills / "good").symlink_to(real)
    (skills / "bad").symlink_to(tmp_path / "gone")
    dangling = _check_skills_integrity(skills)
    assert [Path(p).name for p in dangling] == ["bad"]

# --------------------- home-relative (~/) main paths --------------------- #
# `main` values pointing outside the repo used to be absolute paths, which broke
# the moment a second OS was involved (/home/ezalos vs /Users/ezalos — the
# 2026-08-18 Mac dangling-symlink incident). The portable form is ~/-prefixed,
# expanded against the current device's home at resolve time.

@pytest.mark.run(order=54)
def test_resolve_main_path_tilde_expands_to_device_home(setup_test_environment, monkeypatch, tmp_path):
    from src_dotfiles.config import resolve_main_path
    monkeypatch.setitem(config, 'home', tmp_path.as_posix())
    assert resolve_main_path("~/42/Private/dotfiles/identity_file") == \
        f"{tmp_path.as_posix()}/42/Private/dotfiles/identity_file"

@pytest.mark.run(order=55)
def test_resolve_main_path_absolute_and_relative_unchanged(setup_test_environment):
    from src_dotfiles.config import resolve_main_path
    assert resolve_main_path("/abs/elsewhere/file") == "/abs/elsewhere/file"
    assert resolve_main_path("test_dotfiles/x") == \
        Path(config.project_path).joinpath("test_dotfiles/x").as_posix()

@pytest.mark.run(order=56)
def test_deploy_with_tilde_main_targets_home(setup_test_environment, monkeypatch, tmp_path):
    monkeypatch.setitem(config, 'home', tmp_path.as_posix())
    source = tmp_path / "42" / "Private" / "dotfiles" / "identity_file"
    source.parent.mkdir(parents=True)
    source.write_text("identity content")
    deploy_path = tmp_path / ".identity_file"

    model = DotFileModel(
        alias="tilde_main_test",
        main="~/42/Private/dotfiles/identity_file",
        deploy={config.identifier: DeployedDotFile(deploy_path=str(deploy_path), backups=[])},
    )
    DotFile(model, config.identifier).deploy()

    assert deploy_path.is_symlink()
    assert os.readlink(str(deploy_path)) == source.as_posix()
    assert deploy_path.read_text() == "identity content"

@pytest.mark.run(order=57)
def test_deploy_with_tilde_variant_targets_home(setup_test_environment, monkeypatch, tmp_path):
    monkeypatch.setitem(config, 'home', tmp_path.as_posix())
    variant = tmp_path / "42" / "Private" / "dotfiles" / "identity_file.mydevice"
    variant.parent.mkdir(parents=True)
    variant.write_text("variant content")
    deploy_path = tmp_path / ".identity_variant"

    model = DotFileModel(
        alias="tilde_variant_test",
        main="~/42/Private/dotfiles/identity_file",
        deploy={config.identifier: DeployedDotFile(deploy_path=str(deploy_path), backups=[])},
        variants={config.identifier: "~/42/Private/dotfiles/identity_file.mydevice"},
    )
    DotFile(model, config.identifier).deploy()

    assert deploy_path.is_symlink()
    assert os.readlink(str(deploy_path)) == variant.as_posix()
    assert deploy_path.read_text() == "variant content"

@pytest.mark.run(order=58)
def test_translate_leaves_tilde_main_unchanged(setup_test_environment):
    # A ~ main is portable by construction; the dotfiles-dir segment swap that
    # translation applies to repo-relative mains would corrupt it. The path here
    # deliberately contains the original device's dotfiles_dir_path as a substring.
    model = DotFileModel(
        alias="tilde_translate_test",
        main=f"~/42/Private/{config.dotfiles_dir}/identity_file",
        deploy={config.identifier: DeployedDotFile(
            deploy_path=f"{config.home}/.tilde_translate_test", backups=[])},
    )
    target_device = DevicesData(
        identifier="target_device",
        home_path="/home/target",
        dotfiles_dir_path="test_dotfiles_target",
    )
    translated = DotFile(model, config.identifier).translate_to_device(config.device_data, target_device)
    assert translated.data.main == f"~/42/Private/{config.dotfiles_dir}/identity_file"
    assert translated.data.deploy["target_device"].deploy_path == "/home/target/.tilde_translate_test"

@pytest.mark.run(order=59)
def test_copy_as_main_resolves_tilde_main(setup_test_environment, monkeypatch, tmp_path):
    monkeypatch.setitem(config, 'home', tmp_path.as_posix())
    deployed = tmp_path / "deployed_file"
    deployed.write_text("live content")

    model = DotFileModel(
        alias="tilde_copy_test",
        main="~/copied_main",
        deploy={config.identifier: DeployedDotFile(deploy_path=str(deployed), backups=[])},
    )
    DotFile(model, config.identifier).copy_as_main()

    assert (tmp_path / "copied_main").exists()
    assert (tmp_path / "copied_main").read_text() == "live content"
