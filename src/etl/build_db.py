"""Construit la base DuckDB de travail a partir des 4 CSV.
Schema remodele : media / kpi_compteurs / contexte
"""
import os
import pathlib

import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Racine du projet : src/etl/build_db.py -> remonter de 3 niveaux
ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "raw"
OUT = ROOT / os.getenv("DB_PATH", "data/mmm.duckdb")
OUT.parent.mkdir(parents=True, exist_ok=True)
if OUT.exists():
    OUT.unlink()
con = duckdb.connect(str(OUT))

# ---------- 1. media ----------
fc = pd.read_csv(SRC / "features_cost.csv")
parts = fc["type"].str.split("||", regex=False, expand=True)
if parts.shape[1] < 4:
    for i in range(parts.shape[1], 4):
        parts[i] = None
fc["objectif"] = parts[0].where(parts[1].notna(), fc["type"])
fc["format"] = parts[1]
fc["support"] = parts[2]
fc["duree_sec"] = pd.to_numeric(parts[3], errors="coerce")
fc["type_raw"] = fc["type"]
fc["step_date"] = pd.to_datetime(fc["step_date"])
media = fc[["step_date", "entity", "category", "typology", "channel",
            "objectif", "format", "support", "duree_sec", "type_raw",
            "cost", "performance", "performance_metric"]]
con.execute("CREATE TABLE media AS SELECT * FROM media")

# ---------- 2. kpi_compteurs ----------
c = pd.read_csv(SRC / "compteurs.csv")
c["step_date"] = pd.to_datetime(c["step_date"])
c = c.rename(columns={
    "ref_te_new_counters_without_dem": "new_counters_without_dem",
    "ref_te_dem": "dem", "ref_te_inbound": "inbound",
    "ref_te_outbound": "outbound", "ref_te_partners": "partners",
    "ref_te_web": "web", "ref_te_mes": "mes", "ref_te_cdf": "cdf"})
con.execute("CREATE TABLE kpi_compteurs AS SELECT * FROM c")

# ---------- 3. contexte (depivote) ----------
ctx = pd.read_csv(SRC / "features_context.csv")
ctx["step_date"] = pd.to_datetime(ctx["step_date"])
long = ctx.melt(id_vars="step_date", var_name="col", value_name="value")


def parse(col):
    t = col.split("_")
    entity, brand, typ, channel, typology, category = t[-1], t[-2], t[-3], t[-4], t[-5], t[-6]
    metric = "_".join(t[:-6])
    return metric, category, typology, channel, typ, brand, entity


meta = long["col"].map(parse)
long[["metric", "category", "typology", "channel", "type", "brand_name", "entity"]] = \
    pd.DataFrame(meta.tolist(), index=long.index)
contexte = long[["step_date", "metric", "brand_name", "entity",
                 "category", "typology", "channel", "type", "value"]]
con.execute("CREATE TABLE contexte AS SELECT * FROM contexte")

for t in ["media", "kpi_compteurs", "contexte"]:
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"{t:16s} {n:>7,} lignes")
print("\nmedia — controle objectif/format :")
print(con.execute("""
    SELECT channel, objectif, format, COUNT(*) n
    FROM media WHERE format IS NOT NULL
    GROUP BY 1,2,3 ORDER BY n DESC LIMIT 6""").df().to_string(index=False))
print("\ncontexte — metriques :")
print(con.execute("SELECT metric, COUNT(*) n FROM contexte GROUP BY 1 ORDER BY n DESC").df().to_string(index=False))
con.close()
print("\nOK ->", OUT)
