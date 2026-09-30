"""Coordinator-run live acceptance using a unique temporary Qdrant collection.

Run only where QDRANT_URL, QDRANT_API_KEY_FILE and QDRANT_CA_FILE point to the
approved Qdrant service credentials and trust root. The script writes only to
the collection it creates and removes that collection in ``finally``.
"""

import json
from uuid import uuid4

from xeonolab.qdrant import FAST_TEXT_COLLECTION, QdrantAdapter


def main():
    name = "xeonolab_accept_fast_text_" + uuid4().hex
    client = QdrantAdapter(collection=name)
    owned = False
    try:
        if client.collection_info() is not None:
            raise RuntimeError("generated Qdrant acceptance collection already exists")
        owned = True
        client.create_collection()
        vector = [0.0] * 256
        vector[0] = 1.0
        point_id = str(uuid4())
        client.upsert(
            [
                {
                    "id": point_id,
                    "vector": {FAST_TEXT_COLLECTION: vector},
                    "payload": {
                        "acceptance_run": name,
                        "model_revision": "acceptance-fixture",
                        "chunker_version": "chunker-v2",
                    },
                }
            ]
        )
        result = client.query(vector, 1, "acceptance-fixture")
        if not result or result[0].get("id") != point_id:
            raise RuntimeError("Qdrant did not return the isolated acceptance point")
        if float(result[0].get("score", 0)) < 0.999:
            raise RuntimeError("Qdrant cosine score is outside the expected range")
        print(json.dumps({"result": "passed", "collection": name, "points_returned": len(result)}))
    finally:
        try:
            if owned:
                client.delete_collection()
                if client.collection_info() is not None:
                    raise RuntimeError("temporary Qdrant acceptance collection was not removed")
        finally:
            client.close()


if __name__ == "__main__":
    main()
