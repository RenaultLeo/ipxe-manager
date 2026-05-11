import re


def slugify(text: str) -> str:
    """
    Transforme un label de version en slug utilisable dans un chemin de fichier.
    Ex: "22.04 LTS" → "22.04-lts", "25H2" → "25h2", "2022 Standard" → "2022-standard"
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s\.\-]", "", text)   # garder lettres, chiffres, points, tirets
    text = re.sub(r"[\s_]+", "-", text)         # espaces → tirets
    text = re.sub(r"-+", "-", text)             # tirets multiples → un seul
    return text.strip("-")
