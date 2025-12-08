import configparser
from pathlib import Path
import logging
from dataclasses import dataclass, asdict
from typing import Literal

DEFAULT_CONFIG_FILE = Path("config.ini")
KEYRING_SERVICE = "SoulseekApp"

@dataclass
class Config:
    """
    Application configuration settings.
    """
    download_dir: Path = Path("downloads/")
    concurrency: int = 2
    aioslsk_mode: Literal["pypi", "github"] = "pypi"
    db_path: Path = Path("jobs.db")
    filename_template: str = "{artist} - {title}.{ext}"
    username: str = "" # Stored here for convenience, but keyring is preferred
    password: str = "" # Optional fallback storage (insecure); keyring is preferred

def load_config(config_file: Path = DEFAULT_CONFIG_FILE) -> Config:
    """
    Loads the configuration from config.ini, creating it with defaults if it doesn't exist.
    """
    cfg_parser = configparser.ConfigParser()
    config_obj = Config()

    if not config_file.exists():
        logging.info(f"Creating default config file at {config_file}")
        save_config(config_obj, config_file)
    else:
        logging.info(f"Loading config from {config_file}")
        cfg_parser.read(config_file)

        if "Settings" in cfg_parser:
            settings = cfg_parser["Settings"]
            config_obj.download_dir = Path(settings.get("download_dir", str(config_obj.download_dir)))
            config_obj.concurrency = settings.getint("concurrency", config_obj.concurrency)
            config_obj.aioslsk_mode = settings.get("aioslsk_mode", config_obj.aioslsk_mode) # type: ignore
            config_obj.db_path = Path(settings.get("db_path", str(config_obj.db_path)))
            config_obj.filename_template = settings.get("filename_template", config_obj.filename_template)
        
        if "Credentials" in cfg_parser:
            credentials = cfg_parser["Credentials"]
            config_obj.username = credentials.get("username", config_obj.username)
            # load password if present (may be plaintext if keyring wasn't used)
            config_obj.password = credentials.get("password", config_obj.password)

    return config_obj

def save_config(config_obj: Config, config_file: Path = DEFAULT_CONFIG_FILE):
    """
    Saves the configuration object to config.ini.
    """
    cfg_parser = configparser.ConfigParser()
    
    cfg_parser["Settings"] = {
        "download_dir": str(config_obj.download_dir),
        "concurrency": str(config_obj.concurrency),
        "aioslsk_mode": config_obj.aioslsk_mode,
        "db_path": str(config_obj.db_path),
        "filename_template": config_obj.filename_template,
    }
    # Only write password if explicitly set (user opted to fallback to config)
    creds = {"username": config_obj.username}
    if config_obj.password:
        creds["password"] = config_obj.password
    cfg_parser["Credentials"] = creds

    with open(config_file, "w") as f:
        cfg_parser.write(f)
    logging.info(f"Configuration saved to {config_file}")


def save_credentials(username: str, password: str, config_obj: Config, use_keyring: bool = True, fallback_to_config: bool = False, config_file: Path = DEFAULT_CONFIG_FILE) -> bool:
    """
    Save credentials securely using the system keyring when available.

    - Updates `config_obj.username` and persists it to `config_file`.
    - If `use_keyring` is True, attempts to save `password` to the system keyring.
    - If keyring saving fails and `fallback_to_config` is True, stores the password
      in the config file (in plaintext) and logs a warning.

    Returns True if the password was saved (either to keyring or to config file),
    False otherwise.
    """
    config_obj.username = username
    # try to save to keyring
    if use_keyring:
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, username, password)
            # Ensure we don't leave a plaintext password in the config
            config_obj.password = ""
            save_config(config_obj, config_file)
            logging.info("Password saved to system keyring.")
            return True
        except Exception as e:
            logging.warning(f"Failed to save password to keyring: {e}")

    # fallback: store in config file if allowed (insecure)
    if fallback_to_config:
        config_obj.password = password
        save_config(config_obj, config_file)
        logging.warning("Password stored in config file in plaintext (insecure).")
        return True

    # just save username
    save_config(config_obj, config_file)
    return False


def retrieve_password(username: str, config_file: Path = DEFAULT_CONFIG_FILE) -> str:
    """
    Retrieve a stored password for `username` from the system keyring if available,
    otherwise from the config file (if present). Returns an empty string if not found.
    """
    try:
        import keyring
        pw = keyring.get_password(KEYRING_SERVICE, username)
        if pw:
            return pw
    except Exception:
        pass

    # fallback to config file (plaintext) if present
    cfg_parser = configparser.ConfigParser()
    if config_file.exists():
        cfg_parser.read(config_file)
        if "Credentials" in cfg_parser:
            return cfg_parser["Credentials"].get("password", "")
    return ""