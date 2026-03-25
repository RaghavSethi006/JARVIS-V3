import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

import psutil
import webview
import repo_scanner

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app_config import CONFIG, AGENTS_ENABLED
from core.database import init_db
from core.engine import JarvisEngine
from core.logger import logger
try:
    from core.proactive_agent import ProactiveAgent
except ImportError:
    ProactiveAgent = None
    logger.warning("webview_main: ProactiveAgent not available. Proactive features disabled.")

try:
    from agents.orchestrator import OrchestratorAgent
    from agents.info_agent import InfoAgent
    from agents.system_agent import SystemAgent
    from agents.media_agent import MediaAgent
    from agents.comms_agent import CommsAgent
    from agents.browser_agent import BrowserAgent
    from agents.personal_agent import PersonalAgent
except ImportError as exc:
    OrchestratorAgent = None
    InfoAgent = None
    SystemAgent = None
    MediaAgent = None
    CommsAgent = None
    BrowserAgent = None
    PersonalAgent = None
    logger.warning("webview_main: Agent imports failed: %s", exc)

from services.tts import TTSService
from services.stt import STTService
from services.gesture import GestureService
from services.biometrics import BiometricService
from services.wake_word import WakeWordService

from skills.llm_skill import LLMSkill
from skills.weather_skill import WeatherSkill
from skills.browser_control import BrowserControlSkill
from skills.media_control import MediaControlSkill
from skills.media_downloader import MediaSkill
from skills.news_skill import NewsSkill
from skills.productivity import ProductivitySkill
from skills.whatsapp_skill import WhatsAppSkill
from skills.communication import CommunicationSkill
from skills.quick_launch import QuickLaunchSkill
from skills.system import SystemSkill
from skills.system_control import SystemControlSkill
from skills.spotify_skill import SpotifySkill
from skills.calendar_skill import CalendarSkill
from skills.web_automation import WebAutomationSkill

_engine = None
_bus = None
_loop = None
_loop_thread_id = None
_api = None
win = None
_windows = []
_runtime_objects = []
_wake_service = None
_orchestrator = None
_agent_registry = {}

_js_ready: bool = False
_js_queue: list[str] = []
_js_lock: threading.Lock = threading.Lock()
_windows_loaded: int = 0
_total_windows: int = 2
_pre_loop_queue: list[tuple[str, object]] = []
_last_jarvis_response_text: str = ""
_last_jarvis_response_at: float = 0.0

def evaluate_js_call(fn_name: str, payload) -> str:
    return f"if (typeof {fn_name} === 'function') {fn_name}({json.dumps(payload)});"


class JarvisAPI:
    def __init__(self):
        self._dashboard_window = None
        self._pill_window = None

    def send_command(self, text: str):
        text = (text or '').strip()
        if not text:
            return {'status': 'ignored', 'reason': 'empty'}

        parts = text.split()
        cmd = parts[0].lower()

        compatibility_map = {
            'gesture': ('toggle_gesture_control', {}),
            'login': ('auth_login', {}),
            'register': ('auth_register', {}),
            'scan': ('start_scan', {'output': 'docx'}),
            'download': ('download_video', {'url': ' '.join(parts[1:]).strip()}),
            'alarm': ('set_alarm', {'time_text': ' '.join(parts[1:]).strip()}),
            'launch': ('quick_launch', {'app': ' '.join(parts[1:]).strip()}),
            'whatsapp': (
                'send_whatsapp',
                {
                    'contact': parts[1] if len(parts) > 1 else '',
                    'message': ' '.join(parts[2:]).strip() if len(parts) > 2 else '',
                },
            ),
        }
        if cmd in compatibility_map:
            event_name, payload = compatibility_map[cmd]
            if cmd not in {'gesture', 'login', 'register'}:
                _emit_threadsafe('set_core_state', 'thinking')
            _emit_threadsafe(event_name, payload)
            return {'status': 'ok', 'event': event_name}

        _emit_threadsafe('set_core_state', 'thinking')
        _emit_threadsafe('process_user_input', {'text': text})
        return {'status': 'ok', 'event': 'process_user_input'}

    def toggle_listening(self):
        _emit_threadsafe('toggle_listening', {})
        return {'status': 'ok'}

    def start_listening(self):
        _emit_threadsafe('toggle_listening', {})
        return {'status': 'ok'}

    def resize_window(self, mode: str):
        mode = (mode or '').strip().lower()

        if mode == 'pill':
            if self._dashboard_window is not None:
                self._dashboard_window.hide()

            if self._pill_window is not None:
                try:
                    screen_w = int(webview.screens[0].width)
                except Exception:
                    screen_w = 1920

                self._pill_window.move(screen_w - 280, 20)
                self._pill_window.show()

            return {'status': 'ok', 'mode': 'pill'}

        if self._pill_window is not None:
            self._pill_window.hide()

        if self._dashboard_window is not None:
            self._dashboard_window.show()

        return {'status': 'ok', 'mode': 'dashboard'}

    def start_scan(self):
        _emit_threadsafe('start_scan', {'output': 'docx'})
        return {'status': 'ok'}

    def minimize(self):
        if self._dashboard_window is not None:
            self._dashboard_window.minimize()
        return {'status': 'ok'}

    def close(self):
        try:
            # Phase 1: Archive memory before shutdown
            try:
                from core.memory import MemoryManager
                if _loop is not None and _loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        MemoryManager.get().close_session(), _loop
                    )
                    future.result(timeout=10)  # wait up to 10s for summarization
                    logger.info("JarvisAPI: Memory session archived.")
            except ImportError:
                pass  # Memory system not installed
            except Exception as mem_exc:
                logger.warning("JarvisAPI: Memory close failed: %s", mem_exc)

            if _engine is not None:
                _engine.stop()

            if _loop is not None and _loop.is_running():
                _loop.call_soon_threadsafe(_loop.stop)

            if self._pill_window is not None:
                self._pill_window.destroy()

            if self._dashboard_window is not None:
                self._dashboard_window.destroy()
        except Exception as exc:
            logger.exception('Failed to close cleanly: %s', exc)
            return {'status': 'error', 'message': str(exc)}

        return {'status': 'ok'}


def _emit_threadsafe(event_name: str, data=None):
    global _loop_thread_id
    if _bus is None or _loop is None:
        _pre_loop_queue.append((event_name, data))
        return None

    if _loop_thread_id is None:
        _loop_thread_id = threading.get_ident()

    if not _loop.is_running():
        if _loop_thread_id == threading.get_ident():
            while _pre_loop_queue:
                queued_event_name, queued_data = _pre_loop_queue.pop(0)
                _loop.run_until_complete(_bus.emit(queued_event_name, queued_data))
            return _loop.run_until_complete(_bus.emit(event_name, data))
        _pre_loop_queue.append((event_name, data))
        return None

    while _pre_loop_queue:
        queued_event_name, queued_data = _pre_loop_queue.pop(0)
        asyncio.run_coroutine_threadsafe(_bus.emit(queued_event_name, queued_data), _loop)
    return asyncio.run_coroutine_threadsafe(_bus.emit(event_name, data), _loop)


def _drain_pre_loop_emit_queue() -> None:
    if _bus is None or _loop is None or not _loop.is_running():
        return
    while _pre_loop_queue:
        queued_event_name, queued_data = _pre_loop_queue.pop(0)
        asyncio.run_coroutine_threadsafe(_bus.emit(queued_event_name, queued_data), _loop)


def _flush_js_queue() -> None:
    global _js_ready
    _js_ready = True
    while _js_queue:
        js = _js_queue.pop(0)
        for w in _windows:
            try:
                w.evaluate_js(js)
            except Exception as exc:
                logger.warning('JS flush failed on window %s: %s', w, exc)


def _on_window_loaded() -> None:
    """
    Registered as pywebview loaded event handler.
    Sets ready flag and flushes all queued JS calls in order.
    """
    global _windows_loaded
    _windows_loaded += 1
    expected_total = len(_windows) if _windows else _total_windows
    if expected_total <= 0:
        expected_total = 1
    logger.info(f"[Jarvis] Window loaded ({_windows_loaded}/{expected_total}).")
    if _windows_loaded >= expected_total:
        with _js_lock:
            _flush_js_queue()


def _evaluate_js_call(js: str) -> None:
    """
    ONLY entry point for all JS calls. Never call win.evaluate_js directly.
    Thread-safe: queues calls if page not ready, executes immediately if ready.
    """
    with _js_lock:
        if _js_ready:
            global _windows
            for w in _windows:
                try:
                    w.evaluate_js(js)
                except Exception:
                    logger.exception(f"[Jarvis] JS call failed on window {w.title}: {js[:80]}")
        else:
            logger.debug(f"[Jarvis] Queuing JS (page not ready): {js[:60]}")
            _js_queue.append(js)


def _push_jarvis_response(text: object) -> None:
    """Send a Jarvis response to the UI while suppressing back-to-back duplicates."""
    global _last_jarvis_response_text, _last_jarvis_response_at

    message = "" if text is None else str(text)
    now = time.monotonic()
    if message == _last_jarvis_response_text and (now - _last_jarvis_response_at) < 1.0:
        logger.debug("webview_main: Suppressed duplicate Jarvis response: %s", message[:80])
        return

    _last_jarvis_response_text = message
    _last_jarvis_response_at = now
    _evaluate_js_call(evaluate_js_call('window.addJarvisResponse', message))


def setup_bus_callbacks(bus):
    def on_tts_speak(event):
        _push_jarvis_response(event.data if event.data is not None else '')

    def on_stt_recognition(event):
        _evaluate_js_call(evaluate_js_call('window.addUserMessage', event.data if event.data is not None else ''))

    def on_add_jarvis_response(event):
        _push_jarvis_response(event.data if event.data is not None else '')

    def on_set_status(event):
        _evaluate_js_call(evaluate_js_call('window.setStatus', event.data if event.data is not None else ''))

    def on_set_listening(event):
        _evaluate_js_call(evaluate_js_call('window.setListening', bool(event.data)))

    def on_set_core_state(event):
        _evaluate_js_call(evaluate_js_call('window.setCoreState', event.data if event.data is not None else 'idle'))

    async def on_start_scan(event):
        output = ((event.data or {}).get('output') or 'docx').strip().lower()
        if output not in {'docx', 'pdf', 'both'}:
            output = 'docx'

        await bus.emit('set_status', 'Scanning repository...')
        await bus.emit('set_core_state', 'thinking')
        await bus.emit('tts_speak', 'Starting repository scan.')

        loop = asyncio.get_event_loop()

        def _run_scan():
            cwd = os.getcwd()
            try:
                os.chdir(ROOT_DIR)
                if output in {'docx', 'both'}:
                    repo_scanner.generate_word_document('repo_dump.docx')
                if output in {'pdf', 'both'}:
                    repo_scanner.generate_pdf_document('repo_dump.pdf')
            finally:
                os.chdir(cwd)

        try:
            await loop.run_in_executor(None, _run_scan)
            await bus.emit('tts_speak', 'Repository scan complete. Output saved in project root.')
        except Exception as exc:
            logger.exception('Repository scan failed: %s', exc)
            await bus.emit('tts_speak', 'Repository scan failed.')
        finally:
            await bus.emit('set_status', 'ONLINE')
            await bus.emit('set_core_state', 'idle')

    bus.subscribe('tts_speak', on_tts_speak)
    bus.subscribe('stt_recognition', on_stt_recognition)
    bus.subscribe('add_jarvis_response', on_add_jarvis_response)
    bus.subscribe('set_status', on_set_status)
    bus.subscribe('set_listening', on_set_listening)
    bus.subscribe('set_core_state', on_set_core_state)
    bus.subscribe('start_scan', on_start_scan)
    bus.subscribe(
        'show_meet_link',
        lambda event: _evaluate_js_call(
            evaluate_js_call('window.showMeetLink', event.data if event.data is not None else {})
        ),
    )

    async def on_wake_word_detected(event):
        await bus.emit('tts_speak', 'Yes sir?')
        await bus.emit('toggle_listening', {'active': True})

    bus.subscribe('wake_word_detected', on_wake_word_detected)


async def _push_system_stats():
    """Push real CPU/RAM/NET stats to frontend every 3 seconds."""
    previous_total = None
    while True:
        try:
            await asyncio.sleep(3)
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            net = psutil.net_io_counters()
            total_bytes = net.bytes_sent + net.bytes_recv
            if previous_total is None:
                net_mbps = 0.0
            else:
                delta = max(0, total_bytes - previous_total)
                net_mbps = min(100.0, round((delta / 1024 / 1024) / 3, 1))
            previous_total = total_bytes
            js = evaluate_js_call(
                'window.updateSystemStats',
                {'cpu': cpu, 'mem': mem, 'net': net_mbps},
            )
            _evaluate_js_call(js)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug('System stats push failed: %s', exc)


def _register_runtime_components(engine):
    global _wake_service

    _runtime_objects.append(TTSService(engine.bus))
    _runtime_objects.append(STTService(engine.bus, _loop))
    _runtime_objects.append(GestureService(engine.bus))
    _runtime_objects.append(BiometricService(engine.bus))

    _runtime_objects.append(LLMSkill(engine.bus))
    _runtime_objects.append(WeatherSkill(engine.bus))
    _runtime_objects.append(BrowserControlSkill(engine.bus))
    _runtime_objects.append(MediaControlSkill(engine.bus))
    _runtime_objects.append(MediaSkill(engine.bus))
    _runtime_objects.append(NewsSkill(engine.bus))
    _runtime_objects.append(ProductivitySkill(engine.bus))
    _runtime_objects.append(WhatsAppSkill(engine.bus))
    _runtime_objects.append(CommunicationSkill(engine.bus))
    _runtime_objects.append(QuickLaunchSkill(engine.bus))
    _runtime_objects.append(SystemSkill(engine.bus))
    _runtime_objects.append(SystemControlSkill(engine.bus))
    _runtime_objects.append(SpotifySkill(engine.bus))
    _runtime_objects.append(CalendarSkill(engine.bus))
    _runtime_objects.append(WebAutomationSkill(engine.bus))
    _wake_service = WakeWordService(engine.bus, _loop)
    _runtime_objects.append(_wake_service)

    if AGENTS_ENABLED and OrchestratorAgent is not None:
        try:
            _agent_registry.clear()
            _agent_registry.update(
                {
                    "InfoAgent": InfoAgent(engine.bus),
                    "SystemAgent": SystemAgent(engine.bus),
                    "MediaAgent": MediaAgent(engine.bus),
                    "CommsAgent": CommsAgent(engine.bus),
                    "BrowserAgent": BrowserAgent(engine.bus),
                    "PersonalAgent": PersonalAgent(engine.bus),
                }
            )
            _runtime_objects.extend(_agent_registry.values())

            global _orchestrator
            _orchestrator = OrchestratorAgent(engine.bus, _agent_registry)
            _runtime_objects.append(_orchestrator)

            async def _handle_user_input(event):
                text = (event.data or {}).get("text", "").strip()
                if not text:
                    return
                try:
                    await engine.bus.emit("set_status", "Orchestrator · Processing")
                    response = await _orchestrator.process(text)
                    await engine.bus.emit("tts_speak", response)
                    await engine.bus.emit("add_jarvis_response", response)
                except Exception as exc:
                    logger.exception("webview_main: Orchestrator processing failed: %s", exc)
                    await engine.bus.emit(
                        "tts_speak",
                        "I ran into an internal orchestration error while handling that request.",
                    )
                finally:
                    await engine.bus.emit("set_status", "ONLINE")
                    await engine.bus.emit("set_core_state", "idle")

            engine.bus.subscribe("process_user_input", _handle_user_input)
            logger.info("webview_main: Orchestrator agent routing enabled.")
        except Exception as exc:
            logger.warning("webview_main: Orchestrator wiring failed: %s", exc)
    elif AGENTS_ENABLED:
        logger.warning("webview_main: AGENTS_ENABLED but agent classes unavailable.")

    for obj in _runtime_objects:
        register = getattr(obj, 'register', None)
        if callable(register):
            register()

    if _wake_service is not None:
        _wake_service.start()


def run_backend():
    global _engine, _bus, _loop, _loop_thread_id, _wake_service

    _loop_thread_id = threading.get_ident()
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    _engine = JarvisEngine()
    _bus = _engine.bus
    init_db()
    logger.info('Loaded config: %s', CONFIG)

    setup_bus_callbacks(_bus)
    _register_runtime_components(_engine)
    _loop.call_soon(_drain_pre_loop_emit_queue)
    _loop.create_task(_push_system_stats())

    try:
        _loop.create_task(_engine.start())
        if ProactiveAgent is not None:
            try:
                proactive = ProactiveAgent(_bus)
                _runtime_objects.append(proactive)
                _loop.create_task(proactive.start())
            except Exception as exc:
                logger.warning("run_backend: Proactive agent start failed: %s", exc)
        _loop.run_forever()
    finally:
        if _wake_service is not None:
            _wake_service.stop()

        # Archive memory before stopping loop
        try:
            from core.memory import MemoryManager
            if _loop is not None and _loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    MemoryManager.get().close_session(), _loop
                )
                # Wait briefly for summarization to complete before cancelling tasks
                try:
                    future.result(timeout=10.0)
                    logger.info("run_backend: Memory session archived gracefully.")
                except Exception as fut_exc:
                    logger.warning("run_backend: Memory archive err/timeout: %s", fut_exc)
        except ImportError:
            pass  # Memory system not available
        except Exception as e:
            logger.error("run_backend: Memory save failed: %s", e)

        pending = asyncio.all_tasks(_loop)
        for task in pending:
            task.cancel()

        if pending:
            _loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

        _loop.run_until_complete(_loop.shutdown_asyncgens())
        _loop.close()


def _frontend_index_path():
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base, 'frontend', 'dist', 'index.html')


def create_windows():
    global _api, win, _windows

    frontend = _frontend_index_path()
    if not os.path.exists(frontend):
        raise FileNotFoundError(f'Frontend dist not found: {frontend}')

    dashboard_url = Path(frontend).resolve().as_uri()
    pill_url = f'{dashboard_url}?mode=pill'

    api = JarvisAPI()

    dashboard_window = webview.create_window(
        title='J.A.R.V.I.S',
        url=dashboard_url,
        js_api=api,
        width=560,
        height=540,
        min_size=(460, 420),
        resizable=True,
        frameless=False,
        transparent=False,
        on_top=False,
        hidden=False,
    )
    win = dashboard_window
    dashboard_window.events.loaded += _on_window_loaded
    
    def _on_window_closed():
        import os
        logger.info("Main window closed. Terminating process...")
        try:
            api.close()
        except:
            pass
        finally:
            os._exit(0)
            
    dashboard_window.events.closed += _on_window_closed

    try:
        pill_window = webview.create_window(
            title='JARVIS-Pill',
            url=pill_url,
            js_api=api,
            width=260,
            height=64,
            resizable=False,
            frameless=True,
            transparent=True,
            background_color='#00000000',
            on_top=True,
            shadow=True,
            hidden=True,
        )
    except ValueError:
        # Some pywebview builds only accept #RRGGBB background values.
        pill_window = webview.create_window(
            title='JARVIS-Pill',
            url=pill_url,
            js_api=api,
            width=260,
            height=64,
            resizable=False,
            frameless=True,
            transparent=True,
            background_color='#000000',
            on_top=True,
            shadow=True,
            hidden=True,
        )

    api._dashboard_window = dashboard_window
    api._pill_window = pill_window
    _api = api
    _windows.clear()
    _windows.extend([dashboard_window, pill_window])
    pill_window.events.loaded += _on_window_loaded

if __name__ == '__main__':
    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()
    time.sleep(1.0)

    create_windows()
    webview.start(debug='--debug' in sys.argv)
