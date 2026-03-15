import logging

try:
    from core.logger import logger
except ImportError as exc:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("jarvis")
    logger.warning("agents.__init__: core.logger import failed: %s", exc)

try:
    from .orchestrator import OrchestratorAgent
except ImportError as exc:
    OrchestratorAgent = None
    logger.warning("agents.__init__: OrchestratorAgent import failed: %s", exc)

try:
    from .info_agent import InfoAgent
except ImportError as exc:
    InfoAgent = None
    logger.warning("agents.__init__: InfoAgent import failed: %s", exc)

try:
    from .system_agent import SystemAgent
except ImportError as exc:
    SystemAgent = None
    logger.warning("agents.__init__: SystemAgent import failed: %s", exc)

try:
    from .media_agent import MediaAgent
except ImportError as exc:
    MediaAgent = None
    logger.warning("agents.__init__: MediaAgent import failed: %s", exc)

try:
    from .comms_agent import CommsAgent
except ImportError as exc:
    CommsAgent = None
    logger.warning("agents.__init__: CommsAgent import failed: %s", exc)

try:
    from .browser_agent import BrowserAgent
except ImportError as exc:
    BrowserAgent = None
    logger.warning("agents.__init__: BrowserAgent import failed: %s", exc)

try:
    from .personal_agent import PersonalAgent
except ImportError as exc:
    PersonalAgent = None
    logger.warning("agents.__init__: PersonalAgent import failed: %s", exc)

__all__ = [
    "OrchestratorAgent",
    "InfoAgent",
    "SystemAgent",
    "MediaAgent",
    "CommsAgent",
    "BrowserAgent",
    "PersonalAgent",
]
