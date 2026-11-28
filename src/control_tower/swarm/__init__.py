"""蜂群协作模式（Swarm Mode）对外公共接口。"""

from .models import (
    DEFAULT_MODEL_CATALOG,
    SWARM_STATES,
    WORKSPACE_MODES,
    BoardMessage,
    SwarmState,
    SwarmWorker,
    extract_mentions,
    validate_worker_name,
    validate_workspace_mode,
)
from .repository import append_message, load_state, read_messages, save_state
from .service import (
    add_workers,
    get_status,
    kill_worker,
    list_models,
    merge_worker,
    monitor_check,
    post_message,
    run_demo,
    set_title,
    switch_model,
    verify_worker,
)

__all__ = [
    "DEFAULT_MODEL_CATALOG",
    "SWARM_STATES",
    "WORKSPACE_MODES",
    "BoardMessage",
    "SwarmState",
    "SwarmWorker",
    "add_workers",
    "append_message",
    "extract_mentions",
    "get_status",
    "kill_worker",
    "list_models",
    "load_state",
    "merge_worker",
    "monitor_check",
    "post_message",
    "read_messages",
    "run_demo",
    "save_state",
    "set_title",
    "switch_model",
    "validate_worker_name",
    "validate_workspace_mode",
    "verify_worker",
]
