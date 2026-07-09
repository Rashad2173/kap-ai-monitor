from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from app.db.connection import Base, engine
from app.db import models  # noqa: F401


def main() -> None:
    print("Veritabanı tabloları oluşturuluyor...")
    Base.metadata.create_all(bind=engine)
    print("Tamamlandı.")


if __name__ == "__main__":
    main()