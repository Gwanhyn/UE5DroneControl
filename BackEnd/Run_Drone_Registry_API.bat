@echo off
setlocal
cd /d "%~dp0"
python drone_registry_api.py --config drone_registry_config.yaml
