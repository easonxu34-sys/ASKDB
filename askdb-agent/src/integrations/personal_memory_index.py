from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from integrations.model_services import embed


class PersonalMemoryIndexer:
    def __init__(self, store, models):
        self.store, self.models = store, models

    def generation(self):
        profile = self.models.get_service('embedding')
        space = hashlib.sha256(json.dumps([profile.provider, profile.base_url, profile.model, profile.service_options], sort_keys=True).encode()).hexdigest()
        with self.store.database.connect() as c:
            c.acquire_write_lock()
            self.models._retain_version(c, profile.id)
            self.store._replay(c)
            row = c.execute("SELECT * FROM personal_memory_generations WHERE space_hash=%s AND state IN ('active','building') ORDER BY created_at DESC LIMIT 1", (space,)).fetchone()
            if row:
                return dict(row)
            generation = uuid.uuid4().hex
            c.execute("UPDATE personal_memory_generations SET state='retired' WHERE state='building'")
            c.execute("INSERT INTO personal_memory_generations(id,profile_id,profile_revision,dimensions,space_hash,state) VALUES (%s,%s,%s,%s,%s,'building')", (generation, profile.id, profile.updated_at, profile.service_options['dimensions'], space))
            c.execute("INSERT INTO personal_memory_index_jobs(memory_id,version,content_hash,generation) SELECT id,version,content_hash,%s FROM personal_memories WHERE status='active' AND (expires_at IS NULL OR expires_at>now())", (generation,))
            return dict(c.execute('SELECT * FROM personal_memory_generations WHERE id=%s', (generation,)).fetchone())

    async def tick(self):
        generation = await asyncio.to_thread(self.generation)
        with self.store.database.connect() as c:
            self.store._replay(c)
            jobs = c.execute("SELECT j.*,m.owner,m.content,m.payload FROM personal_memory_index_jobs j JOIN personal_memories m ON m.id=j.memory_id JOIN personal_memory_generations g ON g.id=j.generation WHERE j.retry_at<=now() AND m.status='active' AND m.version=j.version AND m.content_hash=j.content_hash AND g.state IN ('active','building') ORDER BY j.retry_at LIMIT 20").fetchall()
        for job in jobs:
            try:
                with self.store.database.connect() as c:
                    g = c.execute('SELECT * FROM personal_memory_generations WHERE id=%s', (job['generation'],)).fetchone()
                profile = self.models.get_version(g['profile_id'], g['profile_revision'])
                vector = (await embed(profile, [job['content']]))[0]
                with self.store.database.connect() as c:
                    self.store._settings(c, job['owner'], True)
                    self.store._replay(c)
                    current = c.execute("SELECT 1 FROM personal_memories m JOIN personal_memory_generations g ON g.id=%s WHERE m.id=%s AND m.owner=%s AND m.status='active' AND m.version=%s AND m.content_hash=%s AND g.state IN ('active','building')", (job['generation'],job['memory_id'],job['owner'],job['version'],job['content_hash'])).fetchone()
                    if current:
                        c.execute('INSERT INTO personal_memory_embeddings(memory_id,version,generation,content_hash,embedding) VALUES (%s,%s,%s,%s,%s::public.vector) ON CONFLICT(memory_id,generation) DO UPDATE SET version=excluded.version,content_hash=excluded.content_hash,embedding=excluded.embedding', (job['memory_id'],job['version'],job['generation'],job['content_hash'],json.dumps(vector)))
                    c.execute('DELETE FROM personal_memory_index_jobs WHERE memory_id=%s AND generation=%s AND version=%s AND content_hash=%s', (job['memory_id'],job['generation'],job['version'],job['content_hash']))
            except asyncio.CancelledError:
                raise
            except Exception:
                with self.store.database.connect() as c:
                    c.execute("UPDATE personal_memory_index_jobs SET attempts=attempts+1,retry_at=now()+interval '60 seconds' * least(60,power(2,least(attempts,6))),error_code='EMBEDDING_UNAVAILABLE' WHERE memory_id=%s AND generation=%s AND version=%s AND content_hash=%s", (job['memory_id'],job['generation'],job['version'],job['content_hash']))
        with self.store.database.connect() as c:
            c.acquire_write_lock()
            live=c.execute("SELECT state FROM personal_memory_generations WHERE id=%s FOR UPDATE",(generation['id'],)).fetchone()
            if not live or live[0] not in {'active','building'}:
                return
            # Cover writes racing generation creation; never switch with missing versions.
            missing = c.execute("SELECT m.id FROM personal_memories m LEFT JOIN personal_memory_embeddings e ON e.memory_id=m.id AND e.generation=%s AND e.version=m.version AND e.content_hash=m.content_hash WHERE m.status='active' AND (m.expires_at IS NULL OR m.expires_at>now()) AND e.memory_id IS NULL LIMIT 1", (generation['id'],)).fetchone()
            if not missing:
                c.execute("UPDATE personal_memory_generations SET state='retired' WHERE state='active' AND id<>%s", (generation['id'],))
                c.execute("UPDATE personal_memory_generations SET state='active' WHERE id=%s AND state='building'", (generation['id'],))

    def vectors(self, owner, snapshot, vector, generation):
        ids = [x['id'] for x in snapshot]
        if not ids:
            return []
        with self.store.database.connect() as c:
            rows = c.execute("WITH candidates AS MATERIALIZED (SELECT e.* FROM personal_memory_embeddings e JOIN personal_memories m ON m.id=e.memory_id WHERE m.owner=%s AND m.id=ANY(%s) AND e.generation=%s AND m.status='active' AND e.version=m.version AND e.content_hash=m.content_hash AND public.vector_dims(e.embedding)=%s) SELECT memory_id,version FROM candidates ORDER BY embedding <=> %s::public.vector LIMIT 20", (owner, ids,generation['id'],generation['dimensions'],json.dumps(vector))).fetchall()
        versions = {x['id']:x['version'] for x in snapshot}
        return [x[0] for x in rows if versions.get(x[0])==x[1]]

    def active(self):
        with self.store.database.connect() as c:
            row = c.execute("SELECT * FROM personal_memory_generations WHERE state='active'").fetchone()
            return dict(row) if row else None

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # No user data or cloud errors in logs; jobs keep bounded error codes.
            await asyncio.sleep(15)
