from pathlib import Path
import shutil


def prepare(raw: Path, public: Path, private: Path):
    # Copiar el archivo de raw a public y private
    shutil.copy(raw / "GTOC12_Asteroids_Data.txt", public / "GTOC12_Asteroids_Data.txt")
    shutil.copy(raw / "GTOC12_Asteroids_Data.txt", private / "GTOC12_Asteroids_Data.txt")
