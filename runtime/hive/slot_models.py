"""Durable, revisioned operator assignments for the three Pi slots."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def catalog():
    source = Path(__file__).parents[1] / "node" / "go_models.json"
    models = [{"provider": "opencode-go-gateway", "id": "opencode-go/" + row["id"],
               "name": row["name"], "billing": "OpenCode Go"}
              for row in json.loads(source.read_text(encoding="utf-8"))]
    for row in models:
        if row["id"] == "opencode-go/glm-5.3-flash":
            row["billing"] += " · возможен платный резерв OpenRouter"
    return models + [{"provider": "zai", "id": model, "name": model, "billing": "Подписка Z.ai"}
                     for model in ("glm-5.3", "glm-5.3-flash")]


class SlotModels:
    def __init__(self, path):
        self.path = path
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS models (
                slot TEXT PRIMARY KEY, revision INTEGER DEFAULT 0, desired TEXT, applied TEXT,
                state TEXT DEFAULT 'default', error TEXT, delegated INTEGER DEFAULT 0)""")
            db.executemany("INSERT OR IGNORE INTO models(slot) VALUES (?)",
                           [(f"LastOrder0{i}",) for i in range(1, 4)])
            db.execute("""CREATE TABLE IF NOT EXISTS slot_voice (
                       slot TEXT PRIMARY KEY REFERENCES models(slot), source TEXT NOT NULL)""")

    def voices(self):
        with closing(self.connect()) as db:
            return dict(db.execute("""SELECT models.slot, COALESCE(slot_voice.source, 'local')
                                   FROM models LEFT JOIN slot_voice USING(slot)"""))

    def voice(self, slot, source):
        with closing(self.connect()) as db, db:
            db.execute("""INSERT INTO slot_voice VALUES (?, ?) ON CONFLICT(slot)
                       DO UPDATE SET source=excluded.source""", (slot, source))

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def all(self):
        with closing(self.connect()) as db:
            return {row["slot"]: dict(row) for row in db.execute("SELECT * FROM models")}

    def assign(self, slot, model, delegated):
        with closing(self.connect()) as db, db:
            db.execute("""UPDATE models SET desired=?, revision=revision+1, error=NULL,
                       state=?, delegated=? WHERE slot=?""",
                       (model, "requested" if delegated else "pending", delegated, slot))
            return dict(db.execute("SELECT * FROM models WHERE slot=?", (slot,)).fetchone())

    def authorize(self, slot, revision):
        with closing(self.connect()) as db, db:
            db.execute("""UPDATE models SET state='pending' WHERE slot=? AND revision=?
                       AND state='requested' AND delegated=1""", (slot, revision))
            row = db.execute("SELECT * FROM models WHERE slot=? AND revision=? AND delegated=1",
                             (slot, revision)).fetchone()
            return dict(row) if row else None

    def report(self, slot, revision, applied, error):
        with closing(self.connect()) as db, db:
            db.execute("""UPDATE models SET applied=?, error=?, state=CASE
                       WHEN ? IS NOT NULL THEN 'failed' WHEN desired=? THEN 'applied'
                       ELSE state END WHERE slot=? AND revision=? AND state!='requested'""",
                       (applied, error, error, applied, slot, revision))
