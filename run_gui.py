"""Запуск вікна без встановлення пакета: python run_gui.py (також точка входу для PyInstaller)."""

import sys

from marstek_metrics.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
