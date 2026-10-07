"""Owner-scoped PostgreSQL authority. All mutations lock the owner's settings row."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from domain.personal_memory import MemoryInput, PersonalMemoryError


class PersonalMemoryStore:
    def __init__(self, database, journal=None):
        self.database, self.journal = database, journal

    def _settings(self, c, owner, lock=False):
        c.execute('INSERT INTO personal_memory_settings(owner) VALUES (%s) ON CONFLICT DO NOTHING', (owner,))
        return c.execute('SELECT enabled,revision,write_epoch FROM personal_memory_settings WHERE owner=%s' + (' FOR UPDATE' if lock else ''), (owner,)).fetchone()

    @staticmethod
    def public_settings(row):
        return {'enabled': bool(row['enabled']), 'revision': row['revision'], 'write_epoch': row['write_epoch']}

    def settings(self, owner):
        with self.database.connect() as c:
            return self.public_settings(self._settings(c, owner))

    def set_enabled(self, owner, enabled, revision):
        with self.database.connect() as c:
            row = self._settings(c, owner, True)
            if row['revision'] != revision:
                raise PersonalMemoryError('PERSONAL_MEMORY_CONFLICT', '设置已变化，请刷新重试。')
            c.execute('UPDATE personal_memory_settings SET enabled=%s,revision=revision+1,write_epoch=write_epoch+%s WHERE owner=%s', (int(enabled), int(not enabled), owner))
            return self.public_settings(self._settings(c, owner))

    @staticmethod
    def public(row):
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items() if k not in {'owner', 'content_hash'}}

    def _replay(self, c):
        if self.journal is None:
            raise PersonalMemoryError('PERSONAL_MEMORY_UNAVAILABLE', '个人记忆删除日志尚未准备。', 503)
        for event in self.journal.read_all():
            if event.item_type != 'personal_memory':
                continue
            for memory_id in event.item_ids:
                c.execute("UPDATE personal_memories SET status='deleted',content='',payload='{}',version=version+1 WHERE id=%s AND owner=%s AND status<>'deleted'", (memory_id, event.actor_id))
                c.execute('DELETE FROM personal_memory_embeddings WHERE memory_id=%s', (memory_id,))
                c.execute('DELETE FROM personal_memory_index_jobs WHERE memory_id=%s', (memory_id,))

    def snapshot(self, owner, source_id=None, management=False):
        with self.database.connect() as c:
            settings = self._settings(c, owner, True)
            self._replay(c)
            records = []
            if settings['enabled'] or management:
                rows = c.execute("SELECT * FROM personal_memories WHERE owner=%s AND status='active' AND (expires_at IS NULL OR expires_at>now() OR %s=1) AND (source_id IS NULL OR source_id=%s OR %s=1) ORDER BY created_at,id LIMIT 501", (owner, int(management), source_id, int(management))).fetchall()
                if len(rows) > 500:
                    raise PersonalMemoryError('PERSONAL_MEMORY_CAPACITY', '个人记忆超过读取容量，请先整理记录。', 413)
                records = [self.public(row) for row in rows]
            return {**self.public_settings(settings), 'memories': records}

    def list(self, owner, after='', limit=50):
        with self.database.connect() as c:
            self._settings(c, owner, True)
            self._replay(c)
            rows = c.execute("SELECT * FROM personal_memories WHERE owner=%s AND status='active' AND id>%s ORDER BY id LIMIT %s", (owner, after, limit+1)).fetchall()
            return {'memories': [self.public(row) for row in rows[:limit]], 'next_cursor': rows[limit-1]['id'] if len(rows)>limit else None}

    def _tombstone(self, owner, ids, operation_id):
        if not self.journal:
            raise PersonalMemoryError('PERSONAL_MEMORY_UNAVAILABLE', '个人记忆删除日志尚未准备。', 503)
        # Explicit owner/global event; source_id is empty and never pretends to be a business source.
        self.journal.append(event_id=operation_id, event_type='personal_memory_delete', source_id='', thread_id=None, item_type='personal_memory', item_ids=tuple(ids), request_hash=operation_id, actor_id=owner)

    def _index_jobs(self, c, memory_id, version, digest):
        c.execute("INSERT INTO personal_memory_index_jobs(memory_id,version,content_hash,generation) SELECT %s,%s,%s,id FROM personal_memory_generations WHERE state IN ('active','building') ON CONFLICT(memory_id,generation) DO UPDATE SET version=excluded.version,content_hash=excluded.content_hash,attempts=0,retry_at=now(),error_code=NULL", (memory_id, version, digest))

    def mutate(self, owner, action_id, memories, targets=(), versions=None, epoch=None, thread_binding=None):
        # Validate again at the write boundary: callers may have mutated a parsed
        # payload while attaching successful analysis metadata.
        memories = [MemoryInput.model_validate(x.model_dump(mode='json')) for x in memories]
        versions = versions or {}
        with self.database.connect() as c:
            settings = self._settings(c, owner, True)
            self._replay(c)
            if thread_binding:
                thread_id, source_id = thread_binding
                access = c.execute("SELECT 1 FROM chat_thread_data_sources b JOIN agent_conversation_threads t ON t.thread_id=b.thread_id JOIN auth_users u ON u.id=b.owner_user_id JOIN wren_data_sources s ON s.id=b.data_source_id WHERE b.thread_id=%s AND b.owner_user_id=%s AND b.data_source_id=%s AND t.status='active' AND u.is_active=1 AND s.enabled=1 AND (u.role='admin' OR EXISTS(SELECT 1 FROM auth_user_data_sources g WHERE g.user_id=u.id AND g.data_source_id=s.id))", (thread_id, owner, source_id)).fetchone()
                if not access:
                    raise PersonalMemoryError('PERSONAL_MEMORY_SCOPE_UNAVAILABLE','当前账号或会话的数据源权限已变化，未保存。',404)
            previous = c.execute("SELECT payload FROM personal_memory_operations WHERE owner=%s AND id=%s AND kind='action'", (owner, action_id)).fetchone()
            if previous:
                return previous[0]
            if epoch is not None and (not settings['enabled'] or settings['write_epoch'] != epoch):
                raise PersonalMemoryError('PERSONAL_MEMORY_WRITE_CANCELLED', '个人记忆已关闭，尚未保存的操作已取消。')
            old = []
            for memory_id in targets:
                row = c.execute("SELECT * FROM personal_memories WHERE owner=%s AND id=%s AND status='active' FOR UPDATE", (owner, memory_id)).fetchone()
                if not row or row['version'] != versions.get(memory_id):
                    raise PersonalMemoryError('PERSONAL_MEMORY_CONFLICT', '记忆已变化，请刷新后重试。')
                old.append(row)
            if old:
                self._tombstone(owner, targets, 'pm-delete-'+action_id)
                self._replay(c)
            saved = []
            for memory in memories:
                memory_id = 'pm_'+uuid.uuid4().hex
                c.execute("INSERT INTO personal_memories(id,owner,kind,source_id,scenario,content,payload,expires_at,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)", (memory_id,owner,memory.kind,memory.source_id,memory.scenario,memory.content,json.dumps(memory.payload),memory.expires_at,memory.digest()))
                self._index_jobs(c, memory_id, 1, memory.digest())
                saved.append(memory_id)
            c.execute('UPDATE personal_memory_settings SET revision=revision+1 WHERE owner=%s', (owner,))
            result = {'status': 'saved' if saved else 'deleted', 'memory_ids': saved, 'replaced_ids': list(targets), 'message': ('已记住。' if saved else '已忘记。') + ('已替换原要求。' if old and saved else '')}
            c.execute("INSERT INTO personal_memory_operations(owner,id,kind,payload) VALUES (%s,%s,'action',%s::jsonb)", (owner,action_id,json.dumps(result)))
            return result

    def edit(self, owner, memory_id, memory, version):
        return self.mutate(owner, uuid.uuid4().hex, [memory], [memory_id], {memory_id: version})

    def delete(self, owner, memory_id, version):
        return self.mutate(owner, uuid.uuid4().hex, [], [memory_id], {memory_id: version})

    def confirmation(self, owner, thread_id, source_id, targets, versions, revision, message, clear=False):
        request_id = uuid.uuid4().hex
        payload = {'thread_id': thread_id, 'source_id': source_id, 'targets': targets, 'versions': versions, 'revision': revision, 'clear': clear, 'message': message}
        with self.database.connect() as c:
            c.execute("INSERT INTO personal_memory_operations(owner,id,kind,payload,expires_at) VALUES (%s,%s,'confirmation',%s::jsonb,%s)", (owner,request_id,json.dumps(payload),datetime.now(UTC)+timedelta(minutes=10)))
        with self.database.connect() as c:
            labels={row['id']:row['content'][:80] for row in c.execute("SELECT id,content FROM personal_memories WHERE owner=%s AND id=ANY(%s) AND status='active'",(owner,list(targets))).fetchall()} if not clear else {}
        choices = [{'choice_id': 'confirm', 'label': '确认清空'}] if clear else [{'choice_id': x, 'label': '忘记：'+labels.get(x,'所选记忆')} for x in targets]
        return {'request_id': request_id, 'choices': choices + [{'choice_id': 'cancel', 'label': '取消'}], 'message': message}

    def consume(self, owner, request_id, choice_id, thread_id=None, source_id=None):
        with self.database.connect() as c:
            settings = self._settings(c, owner, True)
            self._replay(c)
            row = c.execute("SELECT * FROM personal_memory_operations WHERE owner=%s AND id=%s AND kind='confirmation' AND expires_at>now() FOR UPDATE", (owner,request_id)).fetchone()
            if not row or row['consumed']:
                raise PersonalMemoryError('PERSONAL_MEMORY_CONFIRMATION_STALE', '确认已失效，请重新操作。')
            p = row['payload']
            if p['thread_id'] != thread_id or p['source_id'] != source_id or p['revision'] != settings['revision']:
                raise PersonalMemoryError('PERSONAL_MEMORY_CONFIRMATION_STALE', '确认范围已变化，请重新操作。')
            ids = p['targets'] if p['clear'] and choice_id=='confirm' else [choice_id] if not p['clear'] and choice_id in p['targets'] else []
            if choice_id != 'cancel' and not ids and not (p['clear'] and choice_id=='confirm'):
                raise PersonalMemoryError('PERSONAL_MEMORY_CONFIRMATION_STALE', '确认选项无效。')
            for memory_id in ids:
                current = c.execute("SELECT version FROM personal_memories WHERE owner=%s AND id=%s AND status='active'", (owner,memory_id)).fetchone()
                if not current or current[0] != p['versions'].get(memory_id):
                    raise PersonalMemoryError('PERSONAL_MEMORY_CONFLICT', '记忆已变化，请重新操作。')
            if ids:
                self._tombstone(owner, ids, 'pm-confirm-'+request_id)
                self._replay(c)
                c.execute('UPDATE personal_memory_settings SET revision=revision+1 WHERE owner=%s', (owner,))
            c.execute('UPDATE personal_memory_operations SET consumed=1 WHERE owner=%s AND id=%s', (owner,request_id))
            return {'status': 'cancelled' if choice_id=='cancel' else 'deleted', 'message': '已取消。' if choice_id=='cancel' else '已忘记。'}

    def clear_preview(self, owner):
        snap = self.snapshot(owner, management=True)
        rows = snap['memories']
        return self.confirmation(owner,None,None,[x['id'] for x in rows],{x['id']:x['version'] for x in rows},snap['revision'],f"将清空 {len(rows)} 条个人记忆，开关保持原状。",clear=True)

    def valid_events(self, owner, events):
        result = []
        with self.database.connect() as c:
            for event in events[:16]:
                public = {k:v for k,v in event.items() if k in {'message','status','request_id','choices'}}
                if public.get('request_id'):
                    pending = c.execute("SELECT 1 FROM personal_memory_operations o JOIN personal_memory_settings s ON s.owner=o.owner WHERE o.owner=%s AND o.id=%s AND o.kind='confirmation' AND o.consumed=0 AND o.expires_at>now() AND (o.payload->>'revision')::bigint=s.revision",(owner,public['request_id'])).fetchone()
                    if not pending:
                        public.pop('choices',None)
                result.append(public)
        return result
