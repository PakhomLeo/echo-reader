import asyncio
import threading

from medialab.events import Emitter


def test_worker_event_is_dispatched_on_bound_loop_thread():
    async def scenario():
        loop_thread = threading.get_ident()
        received = asyncio.Event()
        callback_threads = []
        emitter = Emitter(lambda _event, _payload: (
            callback_threads.append(threading.get_ident()), received.set()
        ))
        emitter.bind_loop(asyncio.get_running_loop())
        await asyncio.to_thread(emitter.emit, "task_progress", {"task_id": "t"})
        await asyncio.wait_for(received.wait(), 1)
        assert callback_threads == [loop_thread]

    asyncio.run(scenario())
