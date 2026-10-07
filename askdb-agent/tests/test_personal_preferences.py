from __future__ import annotations

import json
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet

from domain.personal_memory import MemoryInput, PersonalMemoryError
from integrations.deletion_journal import EncryptedDeletionJournal
from integrations.personal_memory_store import PersonalMemoryStore
from integrations.model_services import checked_embeddings, checked_ranking
from application.personal_memory_query import enforce_personal_query
from model_settings import ModelSettingsStore, validate_configuration


def test_owner_isolation_idempotency_epoch_and_tombstone(postgres_database, tmp_path):
    journal=EncryptedDeletionJournal(tmp_path/'personal-deletions.jsonl',Fernet.generate_key().decode())
    journal.initialize(allow_create=True)
    store=PersonalMemoryStore(postgres_database,journal)
    initial=store.settings('alice')
    assert initial['enabled'] is False
    enabled=store.set_enabled('alice',True,initial['revision'])
    memory=MemoryInput(kind='expression',content='请用中文回答')
    saved=store.mutate('alice','action-one',[memory],epoch=enabled['write_epoch'])
    assert store.mutate('alice','action-one',[memory],epoch=enabled['write_epoch'])==saved
    assert len(store.list('alice')['memories'])==1
    assert store.list('bob')['memories']==[]
    memory_id=saved['memory_ids'][0]
    with pytest.raises(PersonalMemoryError):
        store.delete('bob',memory_id,1)
    settings=store.settings('alice')
    disabled=store.set_enabled('alice',False,settings['revision'])
    store.set_enabled('alice',True,disabled['revision'])
    with pytest.raises(PersonalMemoryError) as cancelled:
        store.mutate('alice','late-write',[memory],epoch=enabled['write_epoch'])
    assert cancelled.value.code=='PERSONAL_MEMORY_WRITE_CANCELLED'
    store.delete('alice',memory_id,1)
    # Simulate a restored backup; independent journal must suppress the old body.
    with postgres_database.connect() as c:
        c.execute("UPDATE personal_memories SET status='active',content='恢复的旧偏好' WHERE id=%s",(memory_id,))
    assert store.list('alice')['memories']==[]


def test_confirmation_is_owner_bound_and_single_use(postgres_database,tmp_path):
    journal=EncryptedDeletionJournal(tmp_path/'journal.jsonl',Fernet.generate_key().decode())
    journal.initialize(allow_create=True)
    store=PersonalMemoryStore(postgres_database,journal)
    store.mutate('alice','save',[MemoryInput(kind='expression',content='中文')])
    confirmation=store.clear_preview('alice')
    with pytest.raises(PersonalMemoryError):
        store.consume('bob',confirmation['request_id'],'confirm')
    store.consume('alice',confirmation['request_id'],'confirm')
    with pytest.raises(PersonalMemoryError):
        store.consume('alice',confirmation['request_id'],'confirm')
    assert not store.list('alice')['memories']


def test_model_types_and_encrypted_versions(postgres_database, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','')
    models=ModelSettingsStore(postgres_database,Fernet.generate_key().decode())
    chat=models.create(validate_configuration('custom','chat','https://example.test/v1','test-key'), 'Chat')
    embedding=models.create(validate_configuration('bailian','text-embedding-v4','https://example.test/embeddings','test-key',model_kind='embedding',service_options={'dimensions':1024}), 'Embedding')
    models.set_default(embedding.id)
    assert models.get_default().id==chat.id
    assert models.get_service('embedding').id==embedding.id
    assert models.get_version(embedding.id,embedding.updated_at).service_options['dimensions']==1024
    models.set_memory_chat(chat.id)
    with pytest.raises(ValueError):models.set_memory_chat(embedding.id)
    with pytest.raises(ValueError):models.delete(embedding.id)
    with postgres_database.connect() as c:
        row=c.execute('SELECT configuration FROM model_profile_versions WHERE profile_id=%s',(embedding.id,)).fetchone()
        assert 'test-key' not in json.dumps(row[0])


def test_cloud_protocol_validation_and_query_constraints():
    assert checked_embeddings({'data':[{'index':1,'embedding':[2.]},{'index':0,'embedding':[1.]}]},2,1)==[[1.],[2.]]
    with pytest.raises(ValueError):checked_embeddings({'data':[{'index':0,'embedding':[float('nan')]}]},1,1)
    with pytest.raises(ValueError):checked_ranking({'results':[{'index':0,'relevance_score':1},{'index':0,'relevance_score':0}]},2)
    constraints={'filters':[{'column':'Sales.region','op':'eq','value':'East'}],'metrics':[{'column':'Sales.paid','aggregation':'sum'}]}
    enforce_personal_query("SELECT SUM(s.paid) FROM Sales s WHERE s.region='East'",'mysql',constraints)
    with pytest.raises(PersonalMemoryError):enforce_personal_query('SELECT SUM(paid) FROM Sales','mysql',constraints)
    with pytest.raises(PersonalMemoryError):enforce_personal_query("SELECT SUM(paid) FROM Sales WHERE region='East' OR region='West'",'mysql',constraints)
    with pytest.raises(PersonalMemoryError):enforce_personal_query("SELECT SUM(paid) FROM Sales WHERE NOT(region='East')",'mysql',constraints)
    with pytest.raises(PersonalMemoryError):enforce_personal_query("SELECT SUM(paid)*100 FROM Sales WHERE region='East'",'mysql',constraints)
    from application.personal_memory_display import projection_formats
    assert projection_formats('SELECT SUM(s.paid) AS amount FROM Sales s','mysql',{'sales.paid':'yuan'},'万元')['amount']['display_unit']=='ten_thousand_yuan'
    assert not projection_formats('SELECT COUNT(paid) AS n FROM Sales','mysql',{'sales.paid':'yuan'},'万元')
    assert not projection_formats('SELECT paid FROM Sales','mysql',{},'万元')
    from application.personal_memory_query import successful_descriptor
    assert successful_descriptor('SELECT SUM(s.paid) FROM Sales s','mysql','分析')['references']==['Sales.paid']


def test_index_generation_switch_and_late_job_cannot_restore_deleted(postgres_database,tmp_path,monkeypatch):
    import asyncio
    from integrations.personal_memory_index import PersonalMemoryIndexer
    journal=EncryptedDeletionJournal(tmp_path/'index-journal.jsonl',Fernet.generate_key().decode())
    journal.initialize(allow_create=True)
    store=PersonalMemoryStore(postgres_database,journal)
    monkeypatch.setenv('OPENAI_API_KEY','')
    models=ModelSettingsStore(postgres_database,Fernet.generate_key().decode())
    profile=models.create(validate_configuration('bailian','text-embedding-v4','https://example.test/embeddings','test-key',model_kind='embedding'), 'Embedding')
    models.set_default(profile.id)
    saved=store.mutate('alice','index-action',[MemoryInput(kind='expression',content='中文')])
    memory_id=saved['memory_ids'][0]
    index=PersonalMemoryIndexer(store,models)
    async def delete_during_embed(configuration,texts):
        store.delete('alice',memory_id,1)
        return [[1.0]*configuration.service_options['dimensions']]
    monkeypatch.setattr('integrations.personal_memory_index.embed',delete_during_embed)
    asyncio.run(index.tick())
    with postgres_database.connect() as c:
        assert c.execute('SELECT count(*) FROM personal_memory_embeddings WHERE memory_id=%s',(memory_id,)).fetchone()[0]==0
    async def fake_embed(configuration,texts):
        return [[1.0]*configuration.service_options['dimensions'] for text in texts]
    monkeypatch.setattr('integrations.personal_memory_index.embed',fake_embed)
    store.mutate('alice','index-new',[MemoryInput(kind='expression',content='简洁回答')])
    asyncio.run(index.tick())
    old=index.active()
    models.update(profile.id,replace(profile,service_options={'protocol':'openai_embedding','dimensions':768}),profile.name)
    asyncio.run(index.tick())
    current=index.active()
    assert current['id']!=old['id'] and current['dimensions']==768
    assert models.get_version(old['profile_id'],old['profile_revision']).service_options['dimensions']==1024


def test_explicit_management_prepares_without_writing_before_turn(postgres_database,tmp_path):
    import asyncio
    from application.personal_memory import PersonalMemoryApplication
    journal=EncryptedDeletionJournal(tmp_path/'manage-journal.jsonl',Fernet.generate_key().decode())
    journal.initialize(allow_create=True)
    store=PersonalMemoryStore(postgres_database,journal)
    store.set_enabled('alice',True,0)
    application=PersonalMemoryApplication(store,None,None)
    async def fake_interpret(instruction,value):
        return {'action':'save','memories':[{'kind':'expression','content':'请使用中文回答'}]}
    application.interpret=fake_interpret
    state=asyncio.run(application.prepare('alice','source','thread','turn','记住以后使用中文回答'))
    assert state['pending'].action=='save'
    assert store.list('alice')['memories']==[]
    assert application.latest_analysis('alice','thread') is None


def test_history_confirmation_becomes_unavailable_after_mutation(postgres_database,tmp_path):
    journal=EncryptedDeletionJournal(tmp_path/'history-journal.jsonl',Fernet.generate_key().decode())
    journal.initialize(allow_create=True)
    store=PersonalMemoryStore(postgres_database,journal)
    result=store.mutate('alice','history-one',[MemoryInput(kind='expression',content='中文')])
    event=store.clear_preview('alice')
    assert store.valid_events('alice',[event])[0]['choices']
    assert 'choices' not in store.valid_events('bob',[event])[0]
    store.delete('alice',result['memory_ids'][0],1)
    assert 'choices' not in store.valid_events('alice',[event])[0]
