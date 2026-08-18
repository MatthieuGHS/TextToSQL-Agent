# Image de livraison : une seule, qui sert l'API *et* l'interface.
#
# Deux étapes, pour une raison qui n'est pas la mode : l'outillage Node pèse plusieurs
# centaines de mégaoctets et n'a aucune raison d'exister dans l'image finale. Seul le
# contenu de `web/dist` en sort — du HTML, du CSS et du JavaScript statiques.
#
# **La base de données n'est pas dans l'image, et c'est délibéré.** Elle contient des
# données client ; une image qui la porte se diffuse par accident. Elle est montée au
# lancement (voir `compose.yaml`), au même titre que la clé API, qui n'est jamais
# construite dans une couche.

# --- Étape 1 : l'interface -------------------------------------------------------------
FROM node:22-alpine AS interface

WORKDIR /build
# Les manifestes d'abord : tant qu'ils ne changent pas, Docker réutilise la couche
# d'installation, qui est la plus lente de loin.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# --- Étape 2 : le service --------------------------------------------------------------
FROM python:3.13-slim

# `PYTHONDONTWRITEBYTECODE` : le système de fichiers d'un conteneur est jetable, écrire
# des `.pyc` n'y sert à rien. `PYTHONUNBUFFERED` : sans lui les journaux restent dans le
# tampon et n'apparaissent qu'à l'arrêt — c'est-à-dire jamais quand on en a besoin.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY src/ ./src/
COPY --from=interface /build/dist ./web/dist

# Utilisateur non privilégié : rien ici n'a besoin de root, et l'image a vocation à être
# remise au client.
RUN useradd --create-home --uid 1000 agent && chown -R agent:agent /app
USER agent

EXPOSE 8000
CMD ["uvicorn", "src.app.api:application", "--host", "0.0.0.0", "--port", "8000"]
