import os
import yaml
from typing import Any
from dotenv import load_dotenv

# Load env vars first
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), encoding="latin-1")

class ConfigManager:
    _instance = None
    _config = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        # Load default settings.yaml
        settings_path = os.path.join(os.path.dirname(__file__), 'settings.yaml')
        if os.path.exists(settings_path):
            with open(settings_path, "r", encoding="utf-8-sig") as f:
                self._config = yaml.safe_load(f) or {}
        else:
            print(f"Warning: {settings_path} not found. Using defaults.")
            self._config = {}

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get config value.
        Supports nested keys via dot notation e.g. 'tts.rate'
        Priority: Environment Variable > YAML Config > Default
        """
        # 1. Check Env Var (upper case, dots replaced by underscores, e.g. TTS_RATE)
        env_key = key.upper().replace('.', '_')
        if env_key in os.environ:
            return os.environ[env_key]

        # 2. Check YAML Config
        keys = key.split('.')
        value = self._config
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            pass

        # 3. Return Default
        return default

# Global instance
config = ConfigManager()
