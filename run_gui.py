"""Запуск окна без установки пакета: python run_gui.py (также точка входа для PyInstaller)."""

import sys

from marstek_metrics.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
