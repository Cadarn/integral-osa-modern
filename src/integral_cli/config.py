"""
Configuration and environment manager for INTEGRAL OSA CLI.
"""

from pathlib import Path
import json
import os
import platform
from pydantic import BaseModel, Field

CONFIG_FILE = Path.home() / ".integralrc.json"
DEFAULT_DATA_DIR = Path.home() / "science" / "integral_data_archive"


class IntegralConfig(BaseModel):
    data_dir: str = Field(default_factory=lambda: str(DEFAULT_DATA_DIR))
    ic_dir: str = Field(default_factory=lambda: str(DEFAULT_DATA_DIR))
    docker_image: str = "integralsw/osa:11-native-arm64"
    default_instrument: str = "IBIS"
    ref_catalog: str = "/data/cat/hec/gnrl_refr_cat_0043.fits"
    omc_catalog: str = "/data/cat/omc/omc_refr_cat_0005.fits"


    @property
    def rep_base_prod(self) -> Path:
        env_val = os.environ.get("REP_BASE_PROD")
        return Path(env_val) if env_val else Path(self.data_dir)

    @property
    def current_ic(self) -> Path:
        env_val = os.environ.get("CURRENT_IC")
        return Path(env_val) if env_val else Path(self.ic_dir)

    @property
    def host_arch(self) -> str:
        machine = platform.machine().lower()
        if machine in ["arm64", "aarch64"]:
            return "arm64"
        return "x86_64"

    @classmethod
    def load(cls) -> "IntegralConfig":
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                return cls(**data)
            except Exception:
                pass
        config = cls()
        config.save()
        return config

    def save(self) -> None:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.model_dump(), f, indent=2)
        except Exception:
            pass


config = IntegralConfig.load()
