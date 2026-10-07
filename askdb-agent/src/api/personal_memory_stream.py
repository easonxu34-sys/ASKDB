from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from api.streaming import encode_sse
from domain.personal_memory import PersonalMemoryError


async def management_response(app,request,principal,memory_store,state):
    try:
        turn=await run_in_threadpool(memory_store.begin_turn,thread_id=request.thread_id,owner_user_id=principal.user_id,turn_id=request.turn_id,user_content=request.message.content,expected_sequence=request.expected_sequence)
    except Exception:
        raise HTTPException(409,detail={'code':'THREAD_TURN_CONFLICT','message':'会话状态已变化，请刷新后重试。'}) from None
    async def stream():
        finished=False
        try:
            if not turn.is_new:
                if turn.status=='completed':
                    yield encode_sse('replay',{'assistant_content':turn.assistant_content or '','thread_id':request.thread_id,'turn_id':request.turn_id,'assistant_sequence':turn.assistant_sequence})
                    yield encode_sse('done',{'status':'completed'})
                    finished=True
                    return
                raise PersonalMemoryError('THREAD_TURN_CONFLICT','此轮已在处理中，请刷新后重试。')
            if state.get('confirmation'):
                reply=await run_in_threadpool(app.state.personal_memory.store.consume,principal.user_id,state['confirmation'].request_id,state['confirmation'].choice_id,request.thread_id,state['source_id'])
                state['reply']=reply['message'];state['events'].append(('personal_memory_action',reply))
            elif state.get('pending'):
                reply=await app.state.personal_memory.commit(state,state.get('analysis'))
                state['reply']=reply['message'];state['events'].append(('personal_memory_action',reply))
            yield encode_sse('status',{'status':'running','thread_id':request.thread_id,'turn_id':request.turn_id,'user_sequence':turn.user_sequence})
            for event,payload in state['events']:
                yield encode_sse(event,payload)
            text=state['reply'] or '本次没有修改个人记忆。'
            yield encode_sse('token',{'text':text})
            await run_in_threadpool(memory_store.complete_turn,thread_id=request.thread_id,owner_user_id=principal.user_id,turn_id=request.turn_id,assistant_content=text,personal_events=[payload for event,payload in state['events']])
            finished=True
            yield encode_sse('done',{'status':'completed','thread_id':request.thread_id,'turn_id':request.turn_id,'user_sequence':turn.user_sequence,'assistant_sequence':turn.user_sequence+1})
        except PersonalMemoryError as exc:
            yield encode_sse('token',{'text':exc.message})
            yield encode_sse('personal_memory_action',{'status':'failed','message':exc.message,'code':exc.code})
            if turn.is_new:
                await run_in_threadpool(memory_store.complete_turn,thread_id=request.thread_id,owner_user_id=principal.user_id,turn_id=request.turn_id,assistant_content=exc.message)
                finished=True
            yield encode_sse('done',{'status':'completed' if finished else 'failed'})
        except Exception:
            message='个人记忆操作暂不可用，本次结果未确认；请刷新状态后重试。'
            yield encode_sse('personal_memory_action',{'status':'failed','message':message,'code':'PERSONAL_MEMORY_UNAVAILABLE'})
            yield encode_sse('token',{'text':message})
            yield encode_sse('done',{'status':'failed'})
        finally:
            if not finished and turn.is_new:
                await run_in_threadpool(memory_store.fail_turn,thread_id=request.thread_id,owner_user_id=principal.user_id,turn_id=request.turn_id)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store, no-cache','X-Accel-Buffering':'no'})
