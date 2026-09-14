#!/usr/bin/env python3
"""
Surveillance des logements CROUS -> notification Discord (webhook).

Le script :
1. récupère la page de résultats de recherche CROUS (URL fournie via la
   variable d'environnement CROUS_SEARCH_URL)
2. compare les logements trouvés avec ceux déjà vus (stockés dans seen.json)
3. poste un message dans Discord (via un webhook) pour chaque NOUVEAU logement

Pensé pour tourner une fois toutes les 30 min via GitHub Actions (voir le
fichier workflow fourni à côté). Pas de boucle interne : un run = une
vérification, ce qui colle à un cron */30 et reste simple à déboguer.

Variables d'environnement :
    CROUS_SEARCH_URL     (obligatoire) l'URL de recherche CROUS à surveiller
    DISCORD_WEBHOOK_URL  (obligatoire) l'URL du webhook Discord à notifier
"""

import json
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SEARCH_URL = os.environ.get("CROUS_SEARCH_URL", "").strip()
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
SEEN_FILE = Path("seen.json")
BASE = "https://trouverunlogement.lescrous.fr"

# Sans User-Agent réaliste, certains sites renvoient une page différente
# (voire bloquent la requête). On imite un navigateur classique.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Page de surcharge officielle du CROUS ("vous êtes trop nombreux"). Si on ne
# la détecte pas, le script croirait à tort qu'il y a 0 logement et pourrait
# écraser l'état mémorisé pour rien.
OVERLOAD_MARKER = "vous êtes trop nombreux"


class PageAnomalyError(Exception):
    """La page reçue ne ressemble pas à une vraie page de résultats CROUS."""


def fetch_listings() -> dict:
    """Retourne {id_logement: {title, link, price, addr, details}}."""
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"  # requests devine parfois mal l'encodage sur ce site

    lower = r.text.lower()
    if OVERLOAD_MARKER in lower:
        raise PageAnomalyError("Page de surcharge CROUS ('vous êtes trop nombreux').")

    soup = BeautifulSoup(r.text, "html.parser")
    listings = {}

    for card in soup.find_all("div", class_="fr-card"):
        link_el = card.find("a", href=True)
        if not link_el:
            continue
        href = link_el["href"]
        link = href if href.startswith("http") else BASE + href
        key = link.rstrip("/").split("/")[-1].split("?")[0] or link

        title_el = card.find(["h3", "h2"])
        title = (
            title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)
        ) or "Logement CROUS"

        price_el = card.find("p", class_="fr-badge")
        price = price_el.get_text(strip=True) if price_el else ""

        addr_el = card.find("p", class_="fr-card__desc")
        addr = addr_el.get_text(strip=True) if addr_el else ""

        details = " · ".join(
            p.get_text(strip=True) for p in card.find_all("p", class_="fr-card__detail")
        )

        listings[key] = {
            "title": title,
            "link": link,
            "price": price,
            "addr": addr,
            "details": details,
        }

    return listings


def notify_discord(item: dict) -> None:
    embed = {
        "title": item["title"][:250],
        "url": item["link"],
        "description": " · ".join(
            p for p in (item["price"], item["addr"], item["details"]) if p
        )
        or None,
        "color": 0x2ECC71,
    }
    payload = {"content": "🏠 Nouveau logement CROUS !", "embeds": [embed]}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=20)
        if resp.status_code >= 300:
            print(f"Discord a répondu {resp.status_code} : {resp.text[:200]}")
    except Exception as e:
        print(f"Échec envoi Discord : {e}")


def notify_text(message: str) -> None:
    try:
        requests.post(WEBHOOK_URL, json={"content": message}, timeout=20)
    except Exception as e:
        print(f"Échec envoi Discord : {e}")


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_seen(keys) -> None:
    SEEN_FILE.write_text(
        json.dumps(sorted(keys), ensure_ascii=False, indent=0), encoding="utf-8"
    )


def main() -> None:
    if not SEARCH_URL:
        print("ERREUR : CROUS_SEARCH_URL non défini.", file=sys.stderr)
        sys.exit(1)
    if not WEBHOOK_URL:
        print("ERREUR : DISCORD_WEBHOOK_URL non défini.", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    first_run = not SEEN_FILE.exists()

    try:
        listings = fetch_listings()
    except PageAnomalyError as e:
        print(f"Anomalie de page, run ignoré : {e}")
        return
    except Exception as e:
        print(f"Échec de récupération : {e}")
        return

    if first_run:
        # Premier lancement : on mémorise l'existant sans spammer Discord.
        save_seen(listings.keys())
        notify_text(
            f"✅ Surveillance CROUS active — {len(listings)} logement(s) actuellement en ligne."
        )
        print(f"Premier run : {len(listings)} logement(s) mémorisé(s).")
        return

    new_keys = [k for k in listings if k not in seen]
    if new_keys:
        print(f"{len(new_keys)} nouveau(x) logement(s).")
        for k in new_keys:
            notify_discord(listings[k])
    else:
        print(f"Rien de nouveau ({len(listings)} en ligne).")

    save_seen(listings.keys())


if __name__ == "__main__":
    main()
